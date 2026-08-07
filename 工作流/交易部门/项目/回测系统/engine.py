"""回测引擎：逐股滚动评分主循环（ProcessPoolExecutor 多进程并行 + tqdm）

并发模型（2026-08-06 老板拍板：AMD 卡生态不可用 → CPU 多进程先行，吃满 6 核 12 线程）：
  - 由 ThreadPoolExecutor 5 线程（GIL 受限 ≈1 核有效）升级为 ProcessPoolExecutor
    多进程：指标计算/评级纯 CPU 部分真实并行，实测提速 3-4 倍（见 commit message）；
  - duckdb 单文件多进程只读安全：provider.load 每调用新建只读连接（read_kline），
    回测全程无写库路径（指数/家数缓存写入发生在主进程预加载阶段）；
  - Windows spawn 安全：initializer 注入共享引擎 + 各直接运行入口 __main__ 保护；
  - 闸门计数按"每任务增量"收集合并，结果与线程池版一致（EngineResult 结构不变）。

无前视纪律：
  1. 默认路径：指标全序列一次向量化（向后看算子）→ 每个信号日 T 先 df.iloc[:t+1] 截断再评级；
  2. --recompute-each-window 对照路径：先截断基础列、再逐窗重算指标（严格模式，慢）；
  3. 两路径等价性由单测证明（截断后算 == 全序列算后截断）。

网格纪律：信号日索引固定 range(GRID_ANCHOR, n, interval)；--start/--end 只过滤记录、不改网格。
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from tqdm import tqdm

from 分析决策.市场环境.prbook_gate import (  # C1 财报日避让（2026-08-05 老板拍板）
    prbook_verdict,
    prbook_warn,
)
from 回测系统.adapters.base import DataProvider, RiskModel, StrategyProvider
from 回测系统.adapters.data_provider import CacheDataProvider
from 回测系统.adapters.risk_model import DefaultRiskModel
from 回测系统.adapters.strategy_provider import ZuanQianProvider
from 回测系统.params import GRID_ANCHOR, BacktestParams, _parse_yyyymmdd
from 回测系统.tracking import Signal, TrackedRecord, track_signal


@dataclass
class EngineResult:
    """引擎产出：全部跟踪记录 + 运行统计"""

    records: list[TrackedRecord] = field(default_factory=list)
    processed: int = 0        # 成功处理的股票数
    skipped: int = 0          # 无数据/异常跳过的股票数
    failed_codes: list[str] = field(default_factory=list)
    gate_counts: dict = field(default_factory=dict)  # B1/C3/C4 执行层过滤计数（veto_env/veto_sentiment/veto_volume/downgraded/missing/kept）


class BacktestEngine:
    """回测引擎：加载数据 → 逐股网格滚动评级 → 各 hold 跟踪"""

    def __init__(
        self,
        params: BacktestParams,
        provider: DataProvider | None = None,
        strategy: StrategyProvider | None = None,
        risk: RiskModel | None = None,
    ) -> None:
        params.validate()
        self.params = params
        self.provider = provider or CacheDataProvider()
        self.strategy = strategy or ZuanQianProvider()
        self.risk = risk or DefaultRiskModel()
        self.needed_cols = self.strategy.required_indicators()
        # B1/C3/C4 环境闸门（2026-08-05 第3波）：执行层过滤计数 + 指数/涨跌家数数据懒加载
        self.gate_counts: dict = {"veto_env": 0, "veto_sentiment": 0, "veto_volume": 0,
                                  "downgraded": 0, "missing": 0, "kept": 0,
                                  # C1 财报日避让（2026-08-05 老板拍板）：披露日否决/警示/无数据放行
                                  "veto_prbook": 0, "prbook_warn": 0, "prbook_missing": 0,
                                  # C23 收紧（T-027 2026-08-06）：信号层过滤计数（动量>10%/止损距离出界）
                                  "veto_c23": 0}
        self._index_df = None    # 主闸门指数日线（首次使用 env_gate 时加载）
        self._breadth_df = None  # 全市场涨跌家数（首次使用 sentiment_gate 时加载，C4）
        self._prbook_map: dict = {}  # C1 预约披露 {code: 未披露行列表}（run() 内一次性加载；空=无数据放行）

    # ── 主入口 ──

    def run(self, codes: list[str] | None = None) -> EngineResult:
        """执行回测（codes 优先取显式入参；None 时用 params.codes；再 None 用全股票池）

        多进程并行（2026-08-06 线程池升级）：
        - 指数/家数/披露映射主进程一次性预加载（P3 新鲜度检查在此完成）→
          initializer 注入 worker 进程只读共享，避免每进程重复联网/读缓存；
        - 每任务返回 (记录, 闸门计数增量)，主进程按股合并——结果与线程池版一致。
        """
        if codes is None:
            codes = self.params.codes
        if codes is None:
            # ST 过滤（2026-08-07 实盘开盘审计接入）：扫描层已有名称级 ST 剔除，
            # 引擎股票池此前无过滤（仅靠 G1 行为性兜底）——统一为同一来源
            # stock_pool.is_st_name，保证回测信号集与实盘扫描同口径
            from 数据基础.配置.stock_pool import get_all_stocks, is_st_name
            codes = [s["code"] for s in get_all_stocks()
                     if not is_st_name(s.get("name", ""))]

        result = EngineResult()
        if not codes:
            return result

        if self.params.prbook_gate:
            self._load_prbook_map(codes)  # C1 预约披露一次性加载（worker 共享只读）
        if self.params.env_gate:
            self._load_index_df()  # B1 指数（P3：新鲜度检查一次完成）
        if self.params.sentiment_gate:
            self._load_breadth_df()  # C4 涨跌家数（同上）
        result.gate_counts = {k: 0 for k in self.gate_counts}  # 主进程累加器（9 键对齐）

        with ProcessPoolExecutor(max_workers=self.params.max_workers,
                                 initializer=_mp_init, initargs=(self,)) as executor:
            futures = {executor.submit(_mp_process_stock, code): code for code in codes}
            with tqdm(total=len(futures), desc="backtest", unit="stk") as bar:
                for fut in as_completed(futures):
                    code = futures[fut]
                    try:
                        recs, counts = fut.result()
                        result.records.extend(recs)
                        result.processed += 1
                        for k, v in counts.items():
                            result.gate_counts[k] += v
                    except Exception as e:  # 单股异常不中断整体
                        result.skipped += 1
                        result.failed_codes.append(f"{code}:{type(e).__name__}:{e}")
                    bar.update(1)
        return result

    # ── 单股处理 ──

    def _process_stock(self, code: str) -> list[TrackedRecord]:
        """单股滚动评级：网格信号日 × 活跃模式 → 信号 → 各 hold 跟踪"""
        base = self.provider.load(code)
        if base.empty:
            return []
        n = len(base)
        if n < GRID_ANCHOR + 1:
            return []  # 数据不足首窗（≥250 根），无法形成网格

        grid = list(range(GRID_ANCHOR, n, self.params.interval))
        if self.params.recompute_each_window:
            ind_full = None  # 严格模式：不预计算，逐窗重算
        else:
            ind_full = self.provider.compute_indicators(base, self.needed_cols)

        records: list[TrackedRecord] = []
        dates = base["日期"].values
        close_arr = base["收盘"].values

        for t in grid:
            sig_date = pd.Timestamp(dates[t])
            if not self._in_range(sig_date):
                continue

            if self.params.recompute_each_window:
                window = self.provider.compute_indicators(base.iloc[: t + 1], self.needed_cols)
            else:
                window = ind_full.iloc[: t + 1]  # 无前视：先截断，后评级

            if not self.strategy.quick_prefilter(window):
                continue

            prbook_rows = None
            if self.params.prbook_gate:
                # C1 财报日避让（执行层，2026-08-05 老板拍板）：披露日否决 → 跳过后续评级
                prbook_rows = self._prbook_map.get(code)
                if prbook_rows is None:
                    self.gate_counts["prbook_missing"] += 1   # 无该股披露数据 → 放行并计数
                else:
                    if prbook_verdict(prbook_rows, sig_date)[0] == "veto":
                        self.gate_counts["veto_prbook"] += 1
                        continue

            for mode in self._active_modes():
                sig = self._build_signal(code, sig_date, mode, window, close_arr[t])
                if sig is None:
                    continue
                # C23 收紧（T-027 2026-08-06 老板拍板"回测=现行策略 V2"）：
                # 信号层过滤——动量≤10% + 止损距离 0.5~3 元（无前视版，见 _c23_ok）；
                # 默认关（--c23 显式开），与 sim_capital --c23 语义一致
                if self.params.c23 and not self._c23_ok(sig, window):
                    self.gate_counts["veto_c23"] += 1
                    continue
                # B1 环境闸门 + C3 量能过滤（执行层，2026-08-05 第3波）：
                # 评级与执行分离——grade() 评级保持不变，此处只做否决/降级
                sig = self._apply_exec_gate(sig, window)
                if sig is None:
                    continue
                outcomes = {h: track_signal(sig, base, h, enable_cost=self.params.enable_cost,
                                            cost_multiplier=self.params.cost_multiplier,
                                            moving_stop=self.params.moving_stop,
                                            dn_confirm=self.params.dn_confirm,
                                            phase_in=self.params.phase_in)
                            for h in self.params.holds}
                rec = TrackedRecord(signal=sig, outcomes=outcomes)
                # C1 持仓警示：持仓期内（T+1 ~ 最晚出场日）跨过披露日 → 记录警示，不强制平仓
                if prbook_rows is not None:
                    latest_exit = max((oc.exit_date for oc in outcomes.values()
                                       if oc.exit_date is not None), default=None)
                    warn = prbook_warn(prbook_rows, sig_date, latest_exit)
                    if warn:
                        rec.prbook_warn = warn
                        self.gate_counts["prbook_warn"] += 1
                records.append(rec)
        return records

    # ── 内部工具 ──

    def _load_prbook_map(self, codes: list[str]) -> None:
        """C1 预约披露一次性加载（复用 data_sources/store.next_prbook_dates 查询口径）

        - 与数据提供器同库（CacheDataProvider.db_path；默认主库 t017_p2.duckdb）
        - 缺表/异常 → 空 dict：后续按"无数据放行 + prbook_missing 计数"处理
        """
        from 分析决策.市场环境.prbook_gate import load_prbook_map
        db_path = getattr(self.provider, "db_path", None)
        self._prbook_map = load_prbook_map(codes, db_path=db_path)

    def _index_expected_end(self) -> str:
        """闸门数据新鲜度底线（P3 质检修复 2026-08-06 接入）：
        - 回测指定 --end → 指数/家数缓存须覆盖到结束日；
        - 未指定（跑全量到数据末端）→ 底线=今天，缓存旧于最近交易日 → 重新拉取，
          避免缓存永久复用导致尾部信号日指数/家数数据缺口被静默放行。
        """
        if self.params.end:
            return self.params.end
        # 本地时区 aware 时间（DTZ005 合规）；A股按北京时间自然日判断新鲜度
        return datetime.now().astimezone().strftime("%Y%m%d")

    def _load_index_df(self):
        """懒加载主闸门指数日线（B1；缓存优先，无缓存走 pytdx 拉取）"""
        if self._index_df is None:
            from 分析决策.市场环境.index_data import load_index_daily
            self._index_df = load_index_daily(self.params.env_index,
                                              expected_end=self._index_expected_end())
        return self._index_df

    def _load_breadth_df(self):
        """懒加载全市场涨跌家数（C4；缓存优先，无缓存走 pytdx 拉取）"""
        if self._breadth_df is None:
            from 分析决策.市场环境.index_data import load_market_breadth
            self._breadth_df = load_market_breadth(expected_end=self._index_expected_end())
        return self._breadth_df

    def _apply_exec_gate(self, sig: Signal, window: pd.DataFrame) -> Signal | None:
        """执行层判定（B1 环境闸门 + C3 量能过滤 + C4 情绪闸门）——评级与执行分离

        - keep/missing → 原样放行（missing=数据缺口，按各闸门 missing 策略放行）
        - downgrade → 降一档；若新评级不在 --grade 范围内 → 丢弃
        - veto → 丢弃（指数/情绪/量能各计数）
        """
        if not (self.params.env_gate or self.params.volume_filter or self.params.sentiment_gate):
            self.gate_counts["kept"] += 1
            return sig
        from 分析决策.市场环境.gate import MarketGateConfig, exec_verdict
        cfg = MarketGateConfig(
            enabled=self.params.env_gate, index=self.params.env_index,
            drop_pct=self.params.env_drop_pct, mode=self.params.env_mode,
            volume_filter=self.params.volume_filter,
            min_amount=self.params.min_amount, vol_window=self.params.vol_window,
            sentiment_gate=self.params.sentiment_gate,
            sent_threshold=self.params.sent_threshold,
            missing_sentiment=self.params.missing_sentiment,
        )
        index_df = self._load_index_df() if self.params.env_gate else None
        breadth_df = self._load_breadth_df() if self.params.sentiment_gate else None
        action, info, src = exec_verdict(cfg, index_df, sig.date, sig.grade, window, breadth_df)
        if action == "keep":
            self.gate_counts["kept"] += 1
            return sig
        if action == "missing":
            # 数据缺口（指数/涨跌家数/成交额缺失）→ 放行并计数，避免数据问题误杀信号
            self.gate_counts["missing"] += 1
            return sig
        if action == "downgrade":
            self.gate_counts["downgraded"] += 1
            new_grade = str(info)
            if new_grade not in self.params.grades:
                return None
            sig.grade = new_grade
            return sig
        if action == "veto":
            key = {"volume": "veto_volume", "sentiment": "veto_sentiment"}.get(src, "veto_env")
            self.gate_counts[key] += 1
            return None
        self.gate_counts["kept"] += 1
        return sig

    def _active_modes(self) -> list[str]:
        if self.params.mode == "both":
            return ["normal", "prebreak"]
        return [self.params.mode]

    def _in_range(self, d: pd.Timestamp) -> bool:
        """--start/--end 只过滤记录（不改网格）"""
        if self.params.start and d.date() < _parse_yyyymmdd(self.params.start):
            return False
        return not (self.params.end and d.date() > _parse_yyyymmdd(self.params.end))

    def _c23_ok(self, sig: Signal, window: pd.DataFrame) -> bool:
        """C23 收紧判定（T-027 2026-08-06 老板拍板）：动量≤10% 且 止损距离 0.5~3 元（无前视版）

        口径（对齐 tighten_compare / sim_capital / 扫描层，时点差异见下）：
          - mom20 = 潜在突破价 / 20 交易日前收盘 - 1；潜在突破价 prebreak=触发价
            （trigger_price，信号日已知）、normal=信号日收盘（signal.close）；
            基准收盘取 window.iloc[-21]（信号日 T 视作潜在突破日，扫描层同法）——
            **无前视**：只用 T 及之前数据（window 已先截断至 [:t+1]）。
          - 止损距离 = 每股风险 sig.risk（prebreak=trigger-stop / normal=进场-止损）。
          - 保留：mom20 有效且 ≤ DEFAULT_MOM，且 RISK_MIN ≤ risk ≤ RISK_MAX。
          与 tighten_compare 复算口径（触发日真实定位后 mom20）的差异：触发日 ≠ 信号日时
          基准日不同，预期全量对比存在 1-2 笔差（如实记录）。
        阈值单一来源：回测系统/tighten_compare.py（DEFAULT_MOM / RISK_MIN / RISK_MAX）。
        """
        from 回测系统.tighten_compare import DEFAULT_MOM, RISK_MAX, RISK_MIN
        price = sig.trigger if sig.trigger > 0 else sig.close   # 潜在突破价
        if price <= 0 or len(window) < 22:
            return False                                        # 数据不足 → 不达标（与复算失败同语义）
        close20 = float(window["收盘"].iloc[-21])
        if close20 <= 0:
            return False
        mom20 = price / close20 - 1.0
        if mom20 > DEFAULT_MOM:
            return False                                        # 动量追高 → 滤掉
        return RISK_MIN <= sig.risk <= RISK_MAX

    def _build_signal(self, code: str, sig_date: pd.Timestamp, mode: str,
                      window: pd.DataFrame, close_t: float) -> Signal | None:
        """调用现有评级（同源），产出信号或 None"""
        # 板块上下文（G1 分板块涨跌停线 2026-08-06）：与 scanner 同法设置 attrs，
        # gap_limit_detect 据此判定 20cm 票用 19.5% 线（回测=扫描同口径）。
        window.attrs["code"] = code
        if mode == "normal":
            res = self.strategy.grade(window)
            if not res.get("match") or res.get("grade") not in self.params.grades:
                return None
            entry = float(close_t)
            stop = self.risk.normal_stop(window, entry)   # 止损价 = entry - max(2×ATR14, 2%×entry)
            risk = round(max(entry - stop, 0.0), 4)      # 每股风险
            return Signal(code=code, date=sig_date, mode=mode, grade=res["grade"],
                          scores=dict(res.get("scores", {})), close=round(entry, 4),
                          trigger=0.0, stop=stop, risk=risk)
        # prebreak
        res = self.strategy.prebreak_grade(window)
        if not res.get("match") or res.get("grade") not in self.params.grades:
            return None
        return Signal(code=code, date=sig_date, mode=mode, grade=res["grade"],
                      scores=dict(res.get("scores", {})), close=round(float(close_t), 4),
                      trigger=float(res.get("trigger_price", 0.0)),
                      stop=float(res.get("stop_loss", 0.0)),
                      risk=float(res.get("risk_per_share", 0.0)))


# ── 多进程 worker 层（ProcessPool 专用，2026-08-06）──
# worker 进程启动时经 initializer 注入一次只读共享引擎（含主进程预加载的
# 指数/家数 DataFrame、C1 披露映射），避免逐任务重复 pickle 大对象；
# Windows spawn 下 worker 仅 import 本模块（无顶层副作用）+ 执行 initializer，安全。
_MP_ENGINE: BacktestEngine | None = None


def _mp_init(engine: BacktestEngine) -> None:
    """进程池 worker 初始化：注入只读共享引擎（每进程恰一次）"""
    global _MP_ENGINE
    _MP_ENGINE = engine
    # 闸门日期映射预建（2026-08-08 提速方案 C：每信号全列扫描 → O(1) dict 查）
    try:
        from 分析决策.市场环境.gate import build_gate_maps
        build_gate_maps(engine._index_df, engine._breadth_df)
    except Exception:  # noqa: BLE001 - 映射构建失败回退旧逻辑，不阻断
        pass


def _mp_process_stock(code: str) -> tuple[list[TrackedRecord], dict]:
    """进程池单股任务：返回 (记录列表, 该股闸门计数增量)

    - gate_counts 是 worker 内引擎实例的累积计数器（跨任务持续累加），
      取执行前后快照差值作为该股增量，主进程按股合并——结果与线程池版一致；
    - 抛出的异常由主进程 as_completed 捕获（单股异常不中断整体）。
    """
    eng = _MP_ENGINE
    before = dict(eng.gate_counts)
    recs = eng._process_stock(code)
    after = eng.gate_counts
    diff = {k: after[k] - before.get(k, 0) for k in after}
    return recs, diff


def rerun_track_with_cost(signals_path, params, klines=None) -> list:
    """D2 2倍成本压力·跟踪层复用（2026-08-08 提速方案 A）

    信号/评级/闸门与成本无关（成本只影响 _trade_cost），D2 无需整引擎重跑：
    读基线 signals.csv → 重建 Signal → 批量 K 线 → 仅重算 track_signal(cost_multiplier=2.0)。
    结果与旧全量重跑口径一致（触发判定/止损/出场逻辑同源），时间从 ~45% 引擎耗时 → 秒级。
    """
    import pandas as _pd
    from 回测系统.confirm_replay import load_kline_cache
    from 回测系统.report import SCORE_SHORT
    from 回测系统.tracking import Signal, TrackedRecord, track_signal

    df = _pd.read_csv(signals_path, encoding="utf-8-sig")
    if df.empty:
        return []
    codes = df["code"].astype(str).unique().tolist()
    klines = klines if klines is not None else load_kline_cache(codes)
    records = []
    for _, row in df.iterrows():
        sig = Signal(
            code=str(row["code"]), date=_pd.Timestamp(row["date"]), mode=row["mode"],
            grade=row["grade"],
            scores={k: (str(row.get(SCORE_SHORT[k], "C")) or "C", "")
                    for k in Signal.SCORE_KEYS},
            close=float(row["close"]), trigger=float(row["trigger"]),
            stop=float(row["stop"]), risk=float(row["risk"]),
        )
        base = klines.get(sig.code)
        if base is None:
            continue
        outcomes = {h: track_signal(sig, base, h, enable_cost=True, cost_multiplier=2.0,
                                    moving_stop=params.moving_stop,
                                    dn_confirm=params.dn_confirm,
                                    phase_in=params.phase_in)
                    for h in params.holds}
        records.append(TrackedRecord(signal=sig, outcomes=outcomes))
    return records


def read_signals_records(signals_path, params) -> list:
    """从 signals.csv 重建 TrackedRecord 列表（2026-08-08 提速方案 D：信号缓存复用）

    列名约定与 report.signals_to_frame 一致；各 hold 结果从动态列重建。
    用于：参数指纹未变时跳过引擎直接复用信号集（网格/多组实验同源场景）。
    """
    import pandas as _pd
    from 回测系统.report import SCORE_SHORT
    from 回测系统.tracking import Outcome, Signal, TrackedRecord

    df = _pd.read_csv(signals_path, encoding="utf-8-sig")
    if df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        sig = Signal(
            code=str(row["code"]), date=_pd.Timestamp(row["date"]), mode=row["mode"],
            grade=row["grade"],
            scores={k: (str(row.get(SCORE_SHORT[k], "C")) or "C", "")
                    for k in Signal.SCORE_KEYS},
            close=float(row["close"]), trigger=float(row["trigger"]),
            stop=float(row["stop"]), risk=float(row["risk"]),
        )
        outcomes = {}
        for h in params.holds:
            tr = row.get(f"triggered_{h}d")
            if _pd.isna(tr):
                continue  # 缓存源未跑该 hold → 跳过
            en, ex, ed, st, rv = (row.get(f"entry_{h}d"), row.get(f"exit_{h}d"),
                                  row.get(f"exit_date_{h}d"), row.get(f"stopped_{h}d"),
                                  row.get(f"r_{h}d"))
            outcomes[h] = Outcome(
                hold=h, triggered=bool(int(tr)),
                entry_price=float(en) if not _pd.isna(en) else 0.0,
                exit_price=float(ex) if not _pd.isna(ex) else 0.0,
                exit_date=_pd.Timestamp(ed) if isinstance(ed, str) and ed else None,
                stopped=bool(int(st)) if not _pd.isna(st) else False,
                r=float(rv) if not _pd.isna(rv) else 0.0,
            )
        records.append(TrackedRecord(signal=sig, outcomes=outcomes))
    return records
