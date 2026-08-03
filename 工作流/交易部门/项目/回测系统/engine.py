"""回测引擎：逐股滚动评分主循环（ThreadPoolExecutor 5 线程 + tqdm）

无前视纪律：
  1. 默认路径：指标全序列一次向量化（向后看算子）→ 每个信号日 T 先 df.iloc[:t+1] 截断再评级；
  2. --recompute-each-window 对照路径：先截断基础列、再逐窗重算指标（严格模式，慢）；
  3. 两路径等价性由单测证明（截断后算 == 全序列算后截断）。

网格纪律：信号日索引固定 range(GRID_ANCHOR, n, interval)；--start/--end 只过滤记录、不改网格。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import pandas as pd
from tqdm import tqdm

from backtest.adapters.base import DataProvider, RiskModel, StrategyProvider
from backtest.adapters.data_provider import CacheDataProvider
from backtest.adapters.risk_model import DefaultRiskModel
from backtest.adapters.strategy_provider import ZuanQianProvider
from backtest.params import GRID_ANCHOR, BacktestParams, _parse_yyyymmdd
from backtest.tracking import Signal, TrackedRecord, track_signal


@dataclass
class EngineResult:
    """引擎产出：全部跟踪记录 + 运行统计"""

    records: list[TrackedRecord] = field(default_factory=list)
    processed: int = 0        # 成功处理的股票数
    skipped: int = 0          # 无数据/异常跳过的股票数
    failed_codes: list[str] = field(default_factory=list)


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

    # ── 主入口 ──

    def run(self, codes: list[str] | None = None) -> EngineResult:
        """执行回测（codes 优先取显式入参；None 时用 params.codes；再 None 用全股票池）"""
        if codes is None:
            codes = self.params.codes
        if codes is None:
            from 数据基础.配置.stock_pool import get_stock_codes
            codes = get_stock_codes()

        result = EngineResult()
        if not codes:
            return result

        with ThreadPoolExecutor(max_workers=self.params.max_workers) as executor:
            futures = {executor.submit(self._process_stock, code): code for code in codes}
            with tqdm(total=len(futures), desc="backtest", unit="stk") as bar:
                for fut in as_completed(futures):
                    code = futures[fut]
                    try:
                        recs = fut.result()
                        result.records.extend(recs)
                        result.processed += 1
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

            for mode in self._active_modes():
                sig = self._build_signal(code, sig_date, mode, window, close_arr[t])
                if sig is None:
                    continue
                outcomes = {h: track_signal(sig, base, h) for h in self.params.holds}
                records.append(TrackedRecord(signal=sig, outcomes=outcomes))
        return records

    # ── 内部工具 ──

    def _active_modes(self) -> list[str]:
        if self.params.mode == "both":
            return ["normal", "prebreak"]
        return [self.params.mode]

    def _in_range(self, d: pd.Timestamp) -> bool:
        """--start/--end 只过滤记录（不改网格）"""
        if self.params.start and d.date() < _parse_yyyymmdd(self.params.start):
            return False
        if self.params.end and d.date() > _parse_yyyymmdd(self.params.end):
            return False
        return True

    def _build_signal(self, code: str, sig_date: pd.Timestamp, mode: str,
                      window: pd.DataFrame, close_t: float) -> Signal | None:
        """调用现有评级（同源），产出信号或 None"""
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
