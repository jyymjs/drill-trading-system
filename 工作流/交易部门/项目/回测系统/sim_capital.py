#!/usr/bin/env python3
"""R-009 模块2·资金约束回测（2026-08-06 老板拍板升级：多持仓参数化 + 完整报告指标）

对回测 signals 逐笔模拟真实资金执行（A股·仅做多）：
  - 多持仓并发：最多同时持仓 max_positions 只（默认 5——2026-08-08 老板确认
    「8401 资金 + 108 元风险额 + 5 仓」：完整周期回测 B2_5 +280.4%/回撤27.3%/
    avgR 0.986/连败 7，跨周期坐实；替代 2026-08-06 旧定稿 2.0%×3仓；
    传 1 = 旧版单持仓顺序行为）
  - 可买检查：买入金额 ≤ 可用现金；整手 100 股向下取整；
    每股风险 = entry - stop ≤ 单笔风险额/100（单笔风险额 = 初始资金 × 风险比例，恒定）
  - 费用：佣金万1.3（双边，最低1元）+ 印花税万5（卖出）
  - 输出：资金曲线（每笔平仓后已实现净值）+ 总收益/最大回撤（金额+%+回撤时长）+
    交易笔数/年化笔数/平均持有天数（交易日）+ 胜率/平均R/盈亏比 +
    单笔风险执行检查（是否恒 ≤ 单笔风险额）+ 100 笔节奏预估（按实际信号频率）

用法:
    python 项目/回测系统/sim_capital.py [--signals 路径] [--capital 5600] [--risk-ratio 0.02]
        [--max-positions 3] [--mode prebreak] [--hold 20d] [--grades S A B]
        [--out-csv 资金曲线.csv]
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 交易部根路径注入（复用回测 main.py 方式）
_HERE = os.path.dirname(os.path.abspath(__file__))   # 项目/回测系统
sys.path.insert(0, os.path.dirname(_HERE))            # 项目/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 交易部根

import numpy as np
import pandas as pd

# C23 条件常量单一来源：回测系统/tighten_compare.py（T-024 复算口径）
from 回测系统.tighten_compare import DEFAULT_MOM, RISK_MAX, RISK_MIN

DEFAULT_SIGNALS = os.path.join("项目", "扫描输出", "backtest", "20230701_20260804", "signals.csv")


def _hold_label(hold: str) -> int:
    """'20d' → 20；容忍 '20' 写法"""
    return int(str(hold).replace("d", ""))


def c23_mask(df: pd.DataFrame, mom: float = DEFAULT_MOM) -> pd.Series:
    """C23 过滤掩码：动量≤10% 且 止损距离 0.5~3 元（2026-08-06 老板拍板替换进策略）

    mom20 需由调用方预先复算（tighten_compare.enrich，duckdb 同口径：
    trigger / 20 交易日前 qfq 收盘 - 1）；risk 列 = trigger - stop，
    signals.csv 直接可用。与 c23_capital_compare.c23_mask 同式同源。
    """
    return ((df["mom20"].notna() & (df["mom20"] <= mom))
            & (df["risk"] >= RISK_MIN) & (df["risk"] <= RISK_MAX))


def default_confirm_fn():
    """half_phase 资金模拟的默认确认判定（真实 K 线，带缓存）

    判定规则单一来源：indicators.phase_confirm_from_kline（信号日→触发日→
    次日收线确认；confirm_mode="delay2" = 2026-08-06 老板拍板生产确认规则：
    首根 reject 且存在 T+2 → 二次判定；内部复用 half_position_confirm_delay2，
    与回测层 tracking._phase_in_track / 模拟层 sim_trading._check_half_position
    同规则同源，不复制）。K 线经 fetcher duckdb 优先链路读取（只读）。

    Returns:
        fn(code, signal_date, entry_price, stop) ->
        {"confirmed","stopped","close","confirm_date"}
        confirm_date = 确认日（YYYY-MM-DD，补款扣款日；缺省 = signal_date 次日）
        数据不可得/未触发 → 放行侧默认确认（不因数据问题误拒补仓）
    """
    from 分析决策.分析.indicators import phase_confirm_from_kline
    from 数据基础.duckdb.reader import read_kline

    _cache: dict[str, object] = {}

    def _fn(code: str, signal_date: str, entry_price: float,
            stop_loss: float) -> dict:
        # signals 历史格式 code 去前导零（如 685 → 000685）；duckdb symbol 恒 6 位。
        # 直读 duckdb（只读，全量历史 qfq）——与回放/引擎同口径；不走网络
        # 回退（网络三源复权口径不一致会污染判定），库外个股 → 放行侧确认。
        sym = str(code).zfill(6)
        df = _cache.get(sym)
        if df is None:
            try:
                df = read_kline(sym)
            except Exception:  # noqa: BLE001 - 数据异常 → 放行侧
                df = None
            _cache[sym] = df
        if df is None or len(df) < 2:
            return {"confirmed": True, "stopped": False, "close": 0.0,
                    "confirm_date": signal_date}
        v = phase_confirm_from_kline(df, signal_date, entry_price, stop_loss,
                                     confirm_mode="delay2")
        if v["wait"]:
            return {"confirmed": True, "stopped": False, "close": 0.0,
                    "confirm_date": signal_date}
        return {"confirmed": v["confirmed"], "stopped": v["stopped"],
                "close": v["close"], "confirm_date": v["confirm_date"]}

    return _fn


def _final_pnl(p: dict, fee_out: float) -> float:
    """平仓口径 pnl（统一在平仓循环实算，避免成交时按半仓预存的残留偏差）

    half_phase 确认补仓的持仓：买入费用 = 两笔半仓佣金 + 补仓费（shares 已在
    补款时翻倍）；未确认/直开持仓：单笔买入费用。出场费 = 平仓实算。
    """
    if p.get("half") and p.get("half_ok") and not p.get("half_shortfall"):
        # R-051 修复（交易部审核意见 2）：补仓失败笔（确认但没钱补，half_shortfall）
        # 只付过一次买入佣金 → 不计双费（旧逻辑 half_ok 分支会多扣 1 次佣金+add_fee=0）
        fee_in = 2 * p.get("fee_in", 0) + p.get("add_fee", 0)
    else:
        fee_in = p.get("fee_in", 0)
    return round((p["exit_price"] - p["entry"]) * p["shares"] - fee_in - fee_out, 2)


def simulate_capital(df: pd.DataFrame, capital: float, risk_ratio: float,
                     max_positions: int = 5, mode: str = "prebreak",
                     hold: str = "20d", grades: list[str] | None = None,
                     c23: bool = False,
                     monthly_inject: float = 0.0,
                     risk_growth: bool = False,
                     half_phase: bool = False,
                     confirm_fn=None,
                     same_day_order: str = "time",
                     cap_per_day: int = 0,
                     max_date: str | None = None,
                     debug_rejects: bool = False,
                     vol_map: dict | None = None,
                     confirm_shortfall_skip: bool = False) -> dict:
    """资金约束逐笔模拟（核心逻辑，可单测）

    R-049 扩展（2026-08-11 交易部审核通过，默认行为零变化）：max_date=传值 →
    覆盖"数据末"判定（默认 None = sub 内最后信号日）——R-049 B2 滚动窗跑时传
    真实数据末，窗内持仓可跨窗出场不截断（不引入未来信号，仅出场完整性）。

    Args:
        df: signals.csv 全量（需 mode/code/date/grade/close/stop/risk/entry_/exit_ 列）
        capital: 初始资金（元）
        risk_ratio: 单笔风险比例（初始资金 × 比例 = 单笔风险额；risk_growth=False 时恒定）
        max_positions: 最多同时持仓数（≥1；1 = 单持仓顺序）
        max_date: 数据末覆盖（YYYY-MM-DD；None=现状）
        debug_rejects: 被拒候选明细记录（R-050 审核修订，默认 False 零行为变化）：
            True 时逐笔记录被拒候选 {code,date,risk_ps,reason,risk_amt_at} → res["rejects"]，
            供选择偏差分析（资金不足错过集/超限集精确捕获——巨资对照法被证不可行，
            风险额随资金同步放大使资金约束结构性不可消除）。
        confirm_shortfall_skip: 补仓资金不足跳过（R-051 老板提议规则，默认 False 零行为
            变化）：True 时模拟实盘无预留——开仓不冻结待补款（pending=0），确认日余额
            不足 add_cost+fee 时跳过补仓（维持 0.5R 到出场，half_shortfall 标记、
            reasons["补仓资金不足"] 计数）——评估"钱不够 0.5R 不补"对最终收益的影响。
        mode: normal / prebreak
        hold: 观察窗（'20d'）
        grades: 只做哪些评级（None=全部；默认由 CLI 传，老板约束=只做 S）
        c23: 是否应用 C23 收紧（动量≤10% + 止损距离 0.5~3 元；2026-08-06 老板拍板
            替换进策略）。要求 df 已含 mom20 列（tighten_compare.enrich 复算）。
            仅做信号集过滤，模拟核心逻辑零改动。
        monthly_inject: 每月注入金额（元，默认 0=无注入；>0 时从首信号自然月起
            到末信号自然月，每自然月一笔注入进 balance。2026-08-06 最后全面测试
            注入版资金模拟：5600 起步 + 每月 3000 定投口径）。
        risk_growth: 风险额是否随累计投入（初始+注入）增长（默认 False=按初始
            capital 恒定，与既有口径一致）。True 时每笔成交前按
            (capital + 累计注入) × risk_ratio 重算单笔风险额——模拟"资金增长后
            配置同步上调"（最后全面测试 A 档对照口径）。
        half_phase: G3 0.5R 分步资金占用（2026-08-06 老板确认②，默认 False=现有
            行为零变化）。True 时每笔成交按 0.5R 起步：首日半额风险预算 → 股数
            减半、仅占用半仓资金；确认日（入场次一交易日）收线确认（规则单一来源
            indicators.phase_confirm_from_kline）→ 补 0.5R 至总 1R（资金占用补全）；
            不确认/触止损 → 该笔以半仓结束（出场价/日期/R 仍取 signals 的
            phase_in 引擎口径——backtest_final 已按 phase_in 计算 reject 日平仓）。
            半额预算买不起 1 手 → 回退 1R 直开（保持可执行集与默认一致，对照可比）。
        confirm_fn: 确认判定注入（测试用）：fn(code, signal_date, entry, stop)
            -> {"confirmed","stopped","close"}；None → default_confirm_fn（真实 K 线）
        same_day_order: 同日多候选的处理顺序（2026-08-06 老板拍板质量优先排序实验）：
            "time"=时间先到先得（现状，按 date,code 序）｜"s_count"=S 数降序
            （六维评级中 S 个数多者优先）｜"risk_mid"=每股风险居中（|risk-1.5| 升序）｜
            "mom_asc"=动量升序（需 mom20 列）｜"vol_desc"=触发日量比降序（需 vol_ratio 列）
        cap_per_day: 每日候选处理上限（0=不限=现状；N=当日最多尝试 N 个候选入场
            ——"挂单策略 A：只挂排序前 N"的模拟口径）

    Returns:
        dict: 摘要指标 + trades（逐笔成交）+ equity（资金曲线 DataFrame）
    """
    grades = grades or []
    h = _hold_label(hold)
    sub = df[(df["mode"] == mode) & (df[f"triggered_{h}d"] == 1)]
    if c23:
        sub = sub[c23_mask(sub)]
    if grades:
        sub = sub[sub["grade"].isin(grades)]
    # 同日多候选排序（2026-08-06 老板拍板质量优先实验；"time"=现状零变化）
    if same_day_order == "time":
        sub = sub.sort_values(["date", "code"])
    else:
        sub = sub.copy()
        if same_day_order == "s_count":
            sub["_key"] = (sub[["PT", "TY", "DN", "DL", "LK", "SF"]] == "S").sum(axis=1)
            sub = sub.sort_values(["date", "_key"], ascending=[True, False],
                                  kind="stable")
        elif same_day_order == "risk_mid":
            sub["_key"] = (sub["risk"].astype(float) - 1.5).abs()
            sub = sub.sort_values(["date", "_key"], ascending=[True, True],
                                  kind="stable")
        elif same_day_order == "mom_asc":
            if "mom20" not in sub.columns:
                raise ValueError("same_day_order=mom_asc 需要 mom20 列（tighten_compare.enrich 复算）")
            sub = sub.sort_values(["date", "mom20"], ascending=[True, True],
                                  kind="stable")
        elif same_day_order == "vol_desc":
            if "vol_ratio" not in sub.columns:
                raise ValueError("same_day_order=vol_desc 需要 vol_ratio 列（引擎算过未存，需复算）")
            sub = sub.sort_values(["date", "vol_ratio"], ascending=[True, False],
                                  kind="stable")
        elif same_day_order == "dist_asc":
            # 触发距离优先（P2-5 · 2026-08-09）：|触发价-收盘|/收盘 升序——贴价候选优先
            # （7.5 年信号层：贴价<0.5% avgR +1.965/胜率72.4% vs >3% +0.296）
            sub["_key"] = ((sub["trigger"].astype(float) - sub["close"].astype(float))
                           .abs() / sub["close"].astype(float))
            sub = sub.sort_values(["date", "_key"], ascending=[True, True],
                                  kind="stable")
        else:
            raise ValueError(f"未知 same_day_order={same_day_order}")
        sub = sub.drop(columns=["_key"], errors="ignore")
    sub = sub.copy()
    if not max_date:
        max_date = str(sub["date"].max())[:10] if len(sub) else ""  # 数据末交易日（持仓未完成判定）

    risk_amt = capital * risk_ratio                     # 单笔风险额（元；risk_growth 时随注入更新）
    max_risk_per_share = risk_amt / 100                  # 每股风险上限（整手 100 股）
    _entry_col, exit_col = f"entry_{h}d", f"exit_{h}d"
    exit_date_col, r_col = f"exit_date_{h}d", f"r_{h}d"

    from 分析决策.风控.capital import calc_trade_fee

    balance = capital          # 现金（买入扣款/卖出回款，平仓后 = 总资产已实现值）
    positions: list[dict] = []  # 活跃持仓（到期才平，日线粒度）
    trades: list[dict] = []     # 已平仓成交
    equity: list[dict] = []     # 资金曲线（起点快照 + 每笔平仓后快照 + 注入快照）
    reasons: dict[str, int] = {}
    rejects: list[dict] = []   # debug_rejects=True 时逐笔被拒候选明细（R-050）
    peak, max_dd = capital, 0.0

    # half_phase（G3 0.5R 分步资金占用，2026-08-06 老板确认②）：
    # 确认判定注入（默认真实 K 线，见 default_confirm_fn）；确认日 = 入场次一交易日，
    # 由信号日 + signals 的 phase_in 语义回放（indicators.phase_confirm_from_kline）。
    # 判定结果在成交时立即计算（无 I/O 延迟），补款延迟到确认日扣——模拟半仓期间
    # 资金占用 = 0.5 仓位金额。
    _confirm = confirm_fn if confirm_fn is not None else default_confirm_fn()

    # 注入计划（2026-08-06 最后全面测试 A 档）：首信号自然月 → 末信号自然月，每月一笔
    injected_total = 0.0
    inject_plan_done = 0
    inject_plan: list[tuple[str, float]] = []
    if monthly_inject > 0 and len(sub):
        _start_m = str(sub["date"].iloc[0])[:7]
        _end_m = max_date[:7]
        _months = pd.period_range(start=_start_m, end=_end_m, freq="M")
        inject_plan = [(str(p), monthly_inject) for p in _months]

    # 起点快照：首信号日前的到期注入先入账（起始月注入不落在循环外），
    # 快照用实际 balance/injected_total——保证注入不晚于其对应现金（净曲线正确性）
    if len(sub):
        _first_date = str(sub["date"].iloc[0])[:10]
        while inject_plan and _first_date >= inject_plan[0][0]:
            inj_date, inj_amt = inject_plan.pop(0)
            balance += inj_amt
            injected_total += inj_amt
            inject_plan_done += 1
        equity.append({"date": _first_date, "balance": round(balance, 2),
                       "injected_total": round(injected_total, 2)})
        peak = balance  # 起点即峰值锚（含注入后现金）

    _cur_day, _day_cnt = None, 0   # cap_per_day 每日候选计数（2026-08-06 老板拍板）

    for _, row in sub.iterrows():
        date = str(row["date"])[:10]
        if date != _cur_day:
            _cur_day, _day_cnt = date, 0
        # 0) 到期注入（每自然月一笔，先注入后交易）
        while inject_plan and date >= inject_plan[0][0]:
            inj_date, inj_amt = inject_plan.pop(0)
            balance += inj_amt
            injected_total += inj_amt
            inject_plan_done += 1
            equity.append({"date": inj_date, "balance": round(balance, 2),
                           "injected_total": round(injected_total, 2), "inject": True})
        if risk_growth and monthly_inject > 0:
            # 风险额随累计投入（初始+注入）增长——资金增长后配置同步上调口径
            risk_amt = (capital + injected_total) * risk_ratio
            max_risk_per_share = risk_amt / 100
        # 0.5) 分步确认补款（half_phase）：到确认日（入场次一交易日）→ 收线确认的
        #     补 0.5R（扣补仓款 + 手续费，资金占用补全）；不确认/触止损 → 无补款
        #     （该笔以半仓结束，平仓由下方"先平到期"处理——signals 的 phase_in
        #     引擎口径中 reject 日 = exit_date）。
        # R-051 B 变体（审核意见 6）：确认日缺钱判定计入同日到期持仓回款——
        # 实盘同日先回款后补款（平仓循环稍后入账），A 模式预留制顺序无关
        if confirm_shortfall_skip:
            _incoming = sum(p["exit_price"] * p["shares"]
                            - calc_trade_fee(p["exit_price"] * p["shares"])
                            for p in positions if p["exit_date"] <= date)
            balance_eff = balance + _incoming
        else:
            balance_eff = balance
        for p in [p for p in positions if p.get("half") and not p.get("half_settled")]:
            if p["confirm_date"] and date >= p["confirm_date"]:
                if p["half_ok"]:
                    # 确认补仓：扣补款 + 手续费；持仓翻倍至 1R（平仓回款口径随之
                    # 翻倍）。pnl 不在此算——统一在平仓循环按最终股数重算（单一口径）
                    fee_add = calc_trade_fee(p["add_cost"])
                    if confirm_shortfall_skip and balance_eff < p["add_cost"] + fee_add:
                        # R-051 B 变体（模拟实盘无预留）：确认日余额不足 → 不补，
                        # 维持 0.5R 到出场（half_shortfall 标记供归因；R 不变金额减半）
                        reasons["补仓资金不足"] = reasons.get("补仓资金不足", 0) + 1
                        p["half_shortfall"] = True
                    else:
                        balance -= p["add_cost"] + fee_add
                        balance_eff -= p["add_cost"] + fee_add  # 逐笔同步递减（同日多笔补款连续判定）
                        p["add_fee"] = fee_add
                        p["shares"] *= 2
                        p["risk_actual"] = round(p["risk_actual"] * 2, 2)
                p["half_settled"] = True
        # 1.5) 每日候选上限（2026-08-06 老板拍板挂单策略 A 模拟：同日只挂排序前 N）
        if cap_per_day and _day_cnt >= cap_per_day:
            reasons["每日候选上限(挂单前N)"] = reasons.get("每日候选上限(挂单前N)", 0) + 1
            if debug_rejects:
                rejects.append({"code": row["code"], "date": date, "risk_ps": risk_ps,
                                "reason": "每日候选上限(挂单前N)", "risk_amt_at": round(risk_amt, 2)})
            continue
        _day_cnt += 1
        # 1) 先平到期持仓（exit_date ≤ 当前信号日 → 以该日成交价出场）
        for p in [p for p in positions if p["exit_date"] <= date]:
            proceed = p["exit_price"] * p["shares"]
            fee_out = calc_trade_fee(proceed)
            balance += proceed - fee_out
            p["pnl"] = _final_pnl(p, fee_out)   # 统一口径：按最终股数实算（half 确认后翻倍）
            p["fee_out"] = fee_out              # E-058 回写：trades 出场费按最终股数（half 确认补仓后旧值按半仓计，重建现金流会少计补仓费）
            trades.append({**p, "exit_date": p["exit_date"], "pnl": p["pnl"]})
            equity.append({"date": p["exit_date"], "balance": round(balance, 2),
                           "injected_total": round(injected_total, 2)})
            peak = max(peak, balance)
            max_dd = max(max_dd, peak - balance)
        positions = [p for p in positions if p["exit_date"] > date]

        # 2) 持仓数上限
        if len(positions) >= max_positions:
            reasons[f"持仓数已满(最多{max_positions}只)"] = \
                reasons.get(f"持仓数已满(最多{max_positions}只)", 0) + 1
            continue

        # 成交价 = 引擎入场价（entry_{h}d 列）：prebreak=触发价 / normal=信号日收盘——
        # 与 R 口径一致（R = (exit - entry)/risk，引擎基于触发价计算）。
        # 2026-08-06 修复：旧版用信号日 close 成交，突破日大阳线收盘远离触发价，
        # 导致 18/79 笔 R 与金额盈亏符号矛盾（触发价买/收盘价买差异）。
        price = float(row[_entry_col])
        risk_ps = float(row.get("risk", 0) or 0)
        # 3) 可买股数：资金上限与风险上限取 min，整手（100股）向下取整
        # half_phase 待补预留：确认未决（half 且 half_ok 且未 settle）的补仓款
        # 在确认日前不可用于其他开仓——半仓释放的资金只有"非待补部分"可用，
        # 保证确认日补款余额恒充足（0.5R 分步资金占用的真实语义）。
        # R-051 B 变体（confirm_shortfall_skip=True）：不预留——模拟实盘无预留
        # 机制（挂单多 → 触发多 → 确认日可能没钱补，缺钱按"补仓资金不足"跳过）
        if confirm_shortfall_skip:
            pending = 0
        else:
            pending = sum(p.get("add_cost", 0) for p in positions
                          if p.get("half") and p.get("half_ok")
                          and not p.get("half_settled"))
        avail = balance - pending
        half = False
        if risk_ps <= 0:
            shares = 0
        elif half_phase and risk_amt * 0.5 // risk_ps >= 100:
            # 0.5R 起步（2026-08-06 老板确认②）：半额风险预算可买 ≥1 手 → 半仓起步
            shares = int(min(avail // price, risk_amt * 0.5 // risk_ps) / 100) * 100
            half = shares >= 100
        if risk_ps > 0 and not half:
            shares = int(min(avail // price, risk_amt // risk_ps) / 100) * 100
        if shares < 100:
            if risk_ps > 0 and risk_amt // risk_ps < 100:
                _reason = f"每股风险{risk_ps:.2f}超限(>{max_risk_per_share:.2f})"
                reasons[_reason] = reasons.get(_reason, 0) + 1
            else:
                _reason = "资金不足"
                reasons[_reason] = reasons.get(_reason, 0) + 1
            if debug_rejects:
                rejects.append({"code": row["code"], "date": date, "risk_ps": round(risk_ps, 3),
                                "reason": _reason, "risk_amt_at": round(risk_amt, 2)})
            continue
        # R-080 G11（2026-08-13）：流动性容量约束——单笔成交额 ≤ 当日成交额 5%
        # （vol_map: code → date(YYYY-MM-DD) → 当日成交量(手)；5600 资金容量内不触发，
        #  资金放大防"成交量幻觉"；amount 口径= vol×100×price）
        if vol_map is not None:
            _v = (vol_map.get(str(row["code"]), {}) or {}).get(str(date)[:10], 0) or 0
            if _v > 0:
                _cap_shares = int(_v * 5 / 100) * 100   # 成交额5% ÷ price 后股数 = vol×100×0.05
                if shares > _cap_shares:
                    shares = _cap_shares
                    if shares < 100:
                        reasons["容量不足(>日成交5%)"] = reasons.get("容量不足(>日成交5%)", 0) + 1
                        if debug_rejects:
                            rejects.append({"code": row["code"], "date": date,
                                            "risk_ps": round(risk_ps, 3),
                                            "reason": "容量不足(>日成交5%)",
                                            "risk_amt_at": round(risk_amt, 2)})
                        continue
        cost = price * shares

        # 4) 出场日期合法性校验（坏行/数据末尾未完成持仓跳过）
        ex_d = str(row[exit_date_col])
        if not ex_d or len(ex_d) < 8 or not ex_d[:4].isdigit():
            reasons["出场日期异常(坏数据行)"] = reasons.get("出场日期异常(坏数据行)", 0) + 1
            if debug_rejects:
                rejects.append({"code": row["code"], "date": date, "risk_ps": round(risk_ps, 3),
                                "reason": "出场日期异常(坏数据行)", "risk_amt_at": round(risk_amt, 2)})
            continue
        if ex_d[:10] >= max_date:
            reasons["持仓未完成(数据末尾)"] = reasons.get("持仓未完成(数据末尾)", 0) + 1
            if debug_rejects:
                rejects.append({"code": row["code"], "date": date, "risk_ps": round(risk_ps, 3),
                                "reason": "持仓未完成(数据末尾)", "risk_amt_at": round(risk_amt, 2)})
            continue

        # 5) 成交（整手，费用已含）
        fee_in = calc_trade_fee(cost)
        balance -= cost + fee_in
        exit_price = float(row[exit_col])
        fee_out = calc_trade_fee(exit_price * shares)
        pos = {
            "date": date, "code": row["code"], "grade": row["grade"],
            "entry": price, "exit_price": exit_price, "exit_date": ex_d[:10],
            "r": float(row[r_col]), "shares": int(shares),
            "risk_actual": round(risk_ps * shares, 2),   # 单笔实际风险（每股风险×股数）
            "risk_amt_at": round(risk_amt, 2),           # 成交时单笔风险额（risk_growth 动态档用）
            "pnl": round((exit_price - price) * shares - fee_in - fee_out, 2),
            "fee_in": fee_in, "fee_out": fee_out,        # half_phase 补仓重算 pnl 用
        }
        if half:
            # 0.5R 起步（半仓成交）：确认判定立即算（无 I/O 延迟），补款延迟到
            # 确认日扣——半仓期间资金占用 = 0.5 仓位金额；确认 → 补等额 0.5R
            # （总 1R）；不确认/触止损 → 半仓到 exit_date 平仓（signals phase_in 口径）
            # 注意：止损价 = 进场价 - 每股风险（risk = entry - stop，signals 口径）
            # E-047 口径（2026-08-06 定案）：补仓 = **按初始 R 基准等额补仓**
            # （add_cost = 入场价×半仓股数，不随确认日市价调整）——确认日 C1 要求
            # 收盘≥进场价，故实盘按市价等额买入的实际风险敞口 ≤ 1R 预算（只低不高）；
            # 模拟以初始价等效（出场按 exit_price 实算），与实盘语义一致。
            v = _confirm(str(row["code"]), date, price, price - risk_ps)
            half_ok = bool(v.get("confirmed"))
            cdate = str(v.get("confirm_date") or "")
            if not cdate or cdate < date:
                cdate = date
            pos.update({
                "half": True, "half_ok": half_ok, "half_settled": False,
                "confirm_date": cdate, "add_cost": round(cost, 2), "add_fee": 0.0,
                "pnl": round((exit_price - price) * shares - fee_in - fee_out, 2),
            })
        positions.append(pos)

    # 模拟结束：数据末尾仍持仓的按已记录成交平掉（不开新仓）
    for p in positions:
        proceed = p["exit_price"] * p["shares"]
        fee_out = calc_trade_fee(proceed)
        balance += proceed - fee_out
        p["pnl"] = _final_pnl(p, fee_out)
        p["fee_out"] = fee_out              # E-058 回写（同上方平仓循环）
        trades.append({**p, "pnl": p["pnl"]})
        equity.append({"date": p["exit_date"], "balance": round(balance, 2),
                       "injected_total": round(injected_total, 2)})

    # ── 指标汇总 ──
    n_all = len(sub)
    n_exec = len(trades)
    exec_rate = n_exec / n_all * 100 if n_all else 0
    total_invested = capital + injected_total          # 总投入 = 初始 + 累计注入
    total_pnl = balance - total_invested               # 净盈利（扣除注入，2026-08-06 注入口径）
    total_ret = total_pnl / capital * 100 if capital else 0.0  # 相对初始资金（与静态基线可比）
    total_ret_invested = total_pnl / total_invested * 100 if total_invested else 0.0  # 相对总投入
    rs = [t["r"] for t in trades]
    avg_r = sum(rs) / n_exec if n_exec else 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / n_exec if n_exec else 0.0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    # 回撤时长（已实现净值口径，起点=首个信号日锚点）：从最近峰值到当前净值的最长自然日跨度
    dd_days = 0
    if len(equity) >= 2:
        eq = pd.DataFrame(equity).sort_values("date").reset_index(drop=True)
        peak_idx = 0
        for i in range(len(eq)):
            if eq["balance"].iloc[i] >= eq["balance"].iloc[: i + 1].max():
                peak_idx = i
            elif eq["balance"].iloc[i] < eq["balance"].iloc[: i + 1].max():
                span = (pd.Timestamp(eq["date"].iloc[i]) - pd.Timestamp(eq["date"].iloc[peak_idx])).days
                dd_days = max(dd_days, span)

    # 平均持有天数（交易日口径）+ 年化 + 100 笔节奏
    hold_days = []
    for t in trades:
        try:
            hold_days.append(np.busday_count(str(t["date"]), str(t["exit_date"])))
        except ValueError:
            hold_days.append(0)
    avg_hold = float(np.mean(hold_days)) if hold_days else 0.0

    dates_all = sub["date"].astype(str)
    if len(dates_all):
        n_days = max(1, np.busday_count(dates_all.min()[:10], str(max_date)))  # 首信号~末交易日
        years = max(n_days / 252.0, 1e-9)
        per_year = n_exec / years if years else 0.0
        months_for_100 = 100 / per_year * 12 if per_year else float("inf")
    else:
        n_days, per_year, months_for_100 = 0, 0.0, float("inf")

    return {
        "capital": capital, "risk_amt": risk_amt, "risk_amt_first": capital * risk_ratio,
        "max_risk_per_share": max_risk_per_share,
        "max_positions": max_positions, "mode": mode, "hold": h, "grades": grades,
        "n_all": n_all, "n_exec": n_exec, "exec_rate": exec_rate,
        "reasons": reasons,
        "rejects": rejects,   # debug_rejects=True 时被拒候选明细（R-050；默认空列表）
        "end_balance": balance, "total_pnl": total_pnl, "total_ret": total_ret,
        "total_invested": total_invested, "injected_total": injected_total,
        "n_inject_months": inject_plan_done,
        "total_ret_invested": total_ret_invested,
        "max_dd": max_dd, "max_dd_pct": max_dd / capital * 100 if capital else 0.0,
        "dd_days": dd_days,
        "avg_r": avg_r, "win_rate": win_rate, "profit_factor": profit_factor,
        "avg_hold_days": avg_hold, "per_year": per_year, "months_for_100": months_for_100,
        "risk_exec": {
            "min": min((t["risk_actual"] for t in trades), default=0.0),
            "max": max((t["risk_actual"] for t in trades), default=0.0),
            "mean": float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0,
            # risk_growth 动态档：逐笔对比成交时风险额；恒定档所有 risk_amt_at 相同
            "over_risk_amt": sum(1 for t in trades if t["risk_actual"] > t.get("risk_amt_at", risk_amt)),
            "risk_amt": risk_amt,
        },
        "trades": trades,
        "equity": pd.DataFrame(equity),
        # half_phase（G3 0.5R 分步）执行统计：半仓起步笔数 / 确认补仓笔数 / 未确认半仓止步笔数
        "half_stats": {
            "n_half": sum(1 for t in trades if t.get("half")),
            "n_confirm": sum(1 for t in trades if t.get("half") and t.get("half_ok")),
            "n_reject": sum(1 for t in trades if t.get("half") and not t.get("half_ok")),
        },
        # R-051：补仓资金不足笔数（confirm_shortfall_skip=True 时 reasons["补仓资金不足"]）
        "n_confirm_shortfall": sum(1 for t in trades if t.get("half_shortfall")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=DEFAULT_SIGNALS)
    ap.add_argument("--capital", type=float, default=5600.0, help="初始资金（默认 5600，老板约束）")
    ap.add_argument("--risk-ratio", type=float, default=0.02,
                    help="单笔风险比例（默认 2.0%%——G9 实盘线定稿参数 2026-08-06 老板拍板，"
                         "2.0%%×3仓 与网格实验 T-023 同口径；对照实验显式传旧值 0.015）")
    ap.add_argument("--max-positions", type=int, default=3,
                    help="最多同时持仓数（默认 2，老板实盘约束；1=旧版单持仓顺序）")
    ap.add_argument("--mode", default="prebreak", choices=["normal", "prebreak"])
    ap.add_argument("--hold", default="20d", choices=["5d", "10d", "20d"])
    ap.add_argument("--grades", nargs="+", default=["S"], help="只做评级（默认 S，老板约束）")
    ap.add_argument("--c23", action="store_true",
                    help="C23 收紧：动量≤10%% + 止损距离 0.5~3 元（2026-08-06 老板拍板替换进策略）")
    ap.add_argument("--monthly-inject", type=float, default=0.0,
                    help="每月注入金额（元，默认 0=无注入；最后全面测试 A 档=3000）")
    ap.add_argument("--risk-growth", action="store_true",
                    help="风险额随累计投入（初始+注入）增长（默认关=按初始资金恒定）")
    ap.add_argument("--half-phase", action="store_true",
                    help="G3 0.5R 分步资金占用（2026-08-06 老板确认②）：半仓起步、"
                         "确认日补款、资金占用=半仓金额；默认关=整仓占用（现有行为）")
    ap.add_argument("--out-csv", default=None, help="资金曲线 CSV 输出路径（可选）")
    args = ap.parse_args()

    df = pd.read_csv(args.signals, encoding="utf-8-sig")
    if not len(df):
        print("无信号数据")
        return 1
    if args.c23:
        # mom20 复算（tighten_compare.enrich，duckdb 同口径，与 c23_capital_compare 一致）
        from 回测系统.tighten_compare import enrich

        df = enrich(df)
        # 统计口径与 simulate_capital 内部一致（质检建议级修复 2026-08-06）：
        # 先过滤触发集（mode + triggered_{hold}d），再套 C23 掩码——旧版对全表套掩码，
        # 非触发行也会被计入留存，导致留存 >100%（实测 107.6%）荒谬数字
        trig_col = f"triggered_{_hold_label(args.hold)}d"
        if trig_col in df.columns:
            triggered = df[(df["mode"] == args.mode) & (df[trig_col] == 1)]
        else:
            triggered = df
        n_before = len(triggered)
        kept = triggered[c23_mask(triggered)]
        df = df[c23_mask(df)]
        print(f"[C23 过滤] {args.mode}/{args.hold} 触发信号 {n_before} → {len(kept)} 笔"
              f"（动量≤10% + 止损0.5~3元，留存 {len(kept) / n_before:.1%}）")
    res = simulate_capital(df, args.capital, args.risk_ratio,
                           max_positions=args.max_positions, mode=args.mode,
                           hold=args.hold, grades=args.grades, c23=args.c23,
                           monthly_inject=args.monthly_inject,
                           risk_growth=args.risk_growth,
                           half_phase=args.half_phase)
    if not res["n_exec"]:
        print("无触发信号")
        return 1
    if args.out_csv:
        res["equity"].to_csv(args.out_csv, index=False, encoding="utf-8-sig")
        print(f"资金曲线 → {args.out_csv}")

    # ── 版式输出 ──
    W = 78
    line = "-" * W
    r = res
    print(line)
    print("模拟实盘回测·资金约束（2026-08-06 老板拍板口径）".center(W))
    print(line)
    print(f"  初始资金        {r['capital']:>10,.2f} 元 | 单笔风险 {r['risk_amt_first']:,.0f} 元起（{args.risk_ratio:.1%}"
          f"{'，随投入增长' if args.risk_growth else '，恒定'}")
    print(f"                   | 持仓上限 {r['max_positions']} 只 | 评级 {'/'.join(r['grades'])} | {r['mode']}/{r['hold']}d"
          f"{' | C23 收紧' if args.c23 else ''}"
          f"{' | 0.5R分步(半仓占用)' if args.half_phase else ' | 整仓占用'}")
    print(f"  信号总数        {r['n_all']:>10,}")
    print(f"  实际可执行      {r['n_exec']:>10,}（{r['exec_rate']:.1f}%）")
    for reason, cnt in sorted(r["reasons"].items(), key=lambda x: -x[1]):
        print(f"    不可买原因      {reason}: {cnt}")
    print(line)
    if args.monthly_inject > 0:
        print(f"  总投入          {r['total_invested']:>10,.2f} 元（初始 {r['capital']:,.0f} + 注入 {r['injected_total']:,.0f}）")
    print(f"  终值资金        {r['end_balance']:>10,.2f} 元（净盈利 {r['total_pnl']:+,.2f} 元"
          f" / 相对初始 {r['total_ret']:+.1f}%"
          + (f" / 相对总投入 {r['total_ret_invested']:+.1f}%" if args.monthly_inject > 0 else "")
          + "）")
    print(f"  最大回撤        {r['max_dd']:>10,.2f} 元（{r['max_dd_pct']:.1f}%），最长回撤时长 {r['dd_days']} 天"
          + ("（注入掩盖口径，扣除注入后真实回撤见注入对比脚本）" if args.monthly_inject > 0 else ""))
    print("  ⚠️ 回撤口径警告（2026-08-10 R-041 补标）：本行与资金曲线均为【现金余额口径】"
          "——买入占用现金不算持仓市值，回撤被系统性放大（实测 19.9% 真实回撤误报 99.8%）；"
          "08-06 铁律已禁现金口径，真实回撤请跑 capital_dd_recalc.py（总资产口径）")
    print(f"  交易笔数        {r['n_exec']:>10,} 笔 | 年化 {r['per_year']:.1f} 笔 | 平均持有 {r['avg_hold_days']:.1f} 交易日")
    print(f"  胜率 / 平均R    {r['win_rate']:.1%} / {r['avg_r']:.3f}")
    if r["profit_factor"] == float("inf"):
        print("  盈亏比(金额)    ∞（无亏损笔）")
    else:
        print(f"  盈亏比(金额)    {r['profit_factor']:.2f}")
    print(f"  100笔节奏预估   {r['months_for_100']:.1f} 个月（约 {r['months_for_100'] / 12:.1f} 年）")
    print(f"  单笔风险执行    min {r['risk_exec']['min']:.2f} / mean {r['risk_exec']['mean']:.2f} / "
          f"max {r['risk_exec']['max']:.2f} 元 | 超风险额违规 {r['risk_exec']['over_risk_amt']} 笔")
    print(line)
    print("  说明：整手100股；费用已含（佣金万1.3最低1元+印花税万5卖出）；资金曲线=每笔平仓后已实现净值"
          "（现金口径，见上方回撤口径警告）")
    print("  说明：单笔风险额 = 初始资金 × 风险比例（恒定，不随净值浮动；注入档可用 --risk-growth 随投入增长）")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
