#!/usr/bin/env python3
"""R-044 仓位与风险额对照·统一执行器（2026-08-11 · 交易部审核修订版）

口径（审核否决项 A 修正）：与 R-043 现行一致——half_phase=True + delay2 确认 +
risk_mid 排序（simulate_capital 同参）；回撤一律总资产口径（build_total_asset_curve）。
本脚本 = R-043 补充 C 的入库版（补审核指出的"脚本未入库"缺口）+ 全指标 + V2 自检。

用法（单组）:
  python 项目/回测系统/r44_position_grid.py --capital 8401 --risk-ratio 0.012855 \
      --max-positions 5 --signals 产出/输出/backtest_r43_t2/signals.csv [--monthly-inject 3000]
  python 项目/回测系统/r44_position_grid.py --anchor   # 锚点对账（3 个已知点）
输出：单组 = JSON 行（含 V2 自检）；--anchor = 对账表
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402

from 回测系统.tighten_compare import enrich  # noqa: E402
from 回测系统.sim_capital import simulate_capital  # noqa: E402
from 回测系统.capital_dd_recalc import build_total_asset_curve, _kline  # noqa: E402
from 回测系统.r43_drawdown_segments import find_drawdowns  # noqa: E402

DEFAULT_SIGNALS = os.path.join("产出", "输出", "backtest_r43_t2", "signals.csv")
DB = "数据基础/行情数据/t017_p2.duckdb"


def _exposure_series(trades: list[dict], capital: float) -> tuple[float, float]:
    """逐日敞口曲线（Σ在持 risk_actual/资金）→ (峰值, 均值)；分母=初始资金（口径注明）"""
    events = []
    for t in trades:
        ra = float(t.get("risk_actual", 0) or 0)
        events.append((str(t["date"])[:10], +ra))
        events.append((str(t["exit_date"])[:10], -ra))
    events.sort()
    cur = 0.0
    peak = 0.0
    days = []
    for d, delta in events:
        cur += delta
        days.append((d, cur))
        peak = max(peak, cur)
    if not days:
        return 0.0, 0.0
    return peak / capital, sum(v for _, v in days) / len(days) / capital


def run_one(signals_path: str, capital: float, risk_ratio: float,
            max_positions: int, monthly_inject: float = 0.0,
            min_date: str | None = None, risk_growth: bool = False,
            return_raw: bool = False, max_date: str | None = None,
            enriched_path: str | None = None,
            debug_rejects: bool = False,
            confirm_shortfall_skip: bool = False,
            half_phase: bool = True):
    """单组合全指标（含 V2 自检）

    R-048 扩展（2026-08-11 交易部审核通过，默认行为零变化）：
      return_raw=True → 返回 (metrics, res)（res 含 reasons，供 R-048 reasons 提取）
      max_date=传值  → 覆盖 simulate_capital 的"数据末"判定（R-049 B2 滚动窗
                      跨窗出场不截断用）；None = 现状（sub 内最后信号日）
      enriched_path  → 直接读已 enrich 的信号文件（跳过复算，R-048 提速；
                      调用方保证指纹一致，默认 None 走原 enrich 路径）
    """
    if enriched_path:
        df = pd.read_csv(enriched_path, encoding="utf-8-sig", dtype={"code": str})
    else:
        df = pd.read_csv(signals_path, encoding="utf-8-sig", dtype={"code": str})
    if min_date:
        df = df[df["date"].astype(str) >= min_date]
    if not enriched_path:
        # enrich 进度打印到 stdout 会污染 JSON 输出——重定向抑制（2026-08-11 批处理实测）
        import contextlib
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            df = enrich(df)
    res = simulate_capital(df, capital=capital, risk_ratio=risk_ratio,
                           max_positions=max_positions, mode="prebreak", hold="20d",
                           grades=["S"], c23=True, half_phase=half_phase,
                           monthly_inject=monthly_inject, risk_growth=risk_growth,
                           max_date=max_date, debug_rejects=debug_rejects,
                           confirm_shortfall_skip=confirm_shortfall_skip)
    trades = res["trades"]
    if not trades:
        # 空成交防御（2026-08-11 R-048 冒烟实测：空窗/空信号集 → build_total_asset_curve
        # 对空 trades 行为未定义会 IndexError；空窗不跑引擎，但防御保证路径不崩溃）
        empty = {
            "capital": capital, "risk_ratio": risk_ratio, "risk_amt": round(capital * risk_ratio, 2),
            "max_positions": max_positions, "monthly_inject": monthly_inject,
            "min_date": min_date or "",
            "injected_total": 0.0,
            "total_ret_invested_pct": 0.0,
            "end_balance": capital, "total_ret_pct": 0.0,
            "dd_peak_pct": 0.0, "dd_init_pct": 0.0,
            "dd_days": 0, "dd_trough": "",
            "n_exec": 0,
            "peak_positions": 0, "avg_positions": 0.0, "max_streak": 0,
            "exposure_peak_pct": 0.0, "exposure_avg_pct": 0.0,
            "avg_r": 0.0, "avg_r_no_top5": 0.0,
            "reject_insufficient": None,
            "over_1.5R_count": 0,
            "selfcheck": {
                "curve_end_matches": True, "max_loss_within_1.5R": True, "dd_total_asset": True,
            },
        }
        if return_raw:
            return empty, res
        return empty
    curve = build_total_asset_curve(trades, capital, lambda c: _kline(int(c), DB))
    if monthly_inject > 0:
        # 注入版：注入是现金进账（不影响交易 pnl/持仓市值），总资产 = 无注入曲线 + 累计注入
        # 注入事件从 res["equity"] 提取（injected_total 增长的日期）
        inj_map = {}
        inj_cum = 0.0
        equity = res.get("equity")
        if hasattr(equity, "to_dict"):
            equity = equity.to_dict("records")
        for eq in (equity or []):
            it = float(eq.get("injected_total", 0) or 0)
            if it > inj_cum:
                inj_cum = it
            inj_map[str(eq.get("date", ""))[:10]] = inj_cum
        if inj_map:
            curve["total_asset"] = curve["total_asset"].astype(float) + \
                curve["date"].astype(str).str[:10].map(inj_map).fillna(method="ffill").fillna(0.0)
    segs = find_drawdowns(curve, min_depth=0.01)
    vals = curve["total_asset"].astype(float).values
    peak = np.maximum.accumulate(vals)
    dd = (vals - peak) / peak
    dd_i = int(np.argmin(dd))
    dd_peak = float(dd[dd_i])
    dd_init = float((vals[dd_i] - capital) / capital) if vals[dd_i] < capital else 0.0
    end = float(vals[-1])
    ret = (end - capital) / capital

    # 敞口 + 持仓数日线重建（审核 P2：sim_capital 不提供 peak_positions，从 trades 重建）
    exp_peak, exp_avg = _exposure_series(trades, capital)
    pos_events = []
    for t in trades:
        pos_events.append((str(t["date"])[:10], +1))
        pos_events.append((str(t["exit_date"])[:10], -1))
    pos_events.sort()
    cur = 0
    peak_pos = 0
    pos_days = []
    for d, delta in pos_events:
        cur += delta
        pos_days.append(cur)
        peak_pos = max(peak_pos, cur)
    avg_pos = float(np.mean(pos_days)) if pos_days else 0.0


    # 成交质量（avgR = mean(pnl/risk_actual)——risk_growth 注入版分母随实际风险额，
    # 固定 risk_ratio 口径会虚高（2026-08-11 实测发现）
    pnls = [float(t.get("pnl", 0) or 0) for t in trades]
    risk_actuals = [float(t.get("risk_actual", 0) or 0) for t in trades]
    per_r = [p / ra for p, ra in zip(pnls, risk_actuals) if ra > 0]
    avg_r = float(np.mean(per_r)) if per_r else 0.0
    # 连败统计（审核 P5：max_streak = 平仓 pnl≤0 连续笔数，与实盘预警线同口径）
    max_streak = 0
    streak = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        max_streak = max(max_streak, streak)
    top5 = sorted(pnls, reverse=True)[: max(1, len(pnls) // 20)]
    avg_r_no_top5 = float(np.mean([p for p in pnls if p not in top5])) / (capital * risk_ratio) \
        if len(pnls) > len(top5) else 0.0

    # 单笔亏损超 1.5R 计数（逐笔用实际 risk_actual——risk_growth 版风险额变化，
    # 固定 capital×risk_ratio 阈值会误判（2026-08-11 注入版实测发现）
    over_r15 = sum(1 for t in trades
                   if float(t.get("pnl", 0) or 0) <= -1.5 * float(t.get("risk_actual", 0) or 0))

    # V2 自检（曲线终点 vs 现金终值：数据截止日附近未平持仓 → 总资产 ≥ 现金，容忍）
    # 注：peak_positions 为记录项（None=sim_capital 未提供），不参与自检布尔
    selfcheck = {
        "curve_end_matches": bool(float(end) >= float(res["end_balance"]) - 0.5),
        "max_loss_within_1.5R": bool(over_r15 == 0),
        "dd_total_asset": True,  # 本脚本回撤一律总资产口径
    }

    # 注入版双口径：相对初始（虚高）+ 相对总投入（真实；审核 B 要求）
    injected_total = float(res.get("injected_total", 0) or 0)
    total_invested = capital + injected_total
    ret_invested = (end - total_invested) / total_invested if total_invested > 0 else 0.0

    metrics = {
        "capital": capital, "risk_ratio": risk_ratio, "risk_amt": round(capital * risk_ratio, 2),
        "max_positions": max_positions, "monthly_inject": monthly_inject,
        "min_date": min_date or "",
        "injected_total": round(injected_total, 0),
        "total_ret_invested_pct": round(ret_invested * 100, 1),
        "end_balance": round(end, 2), "total_ret_pct": round(ret * 100, 1),
        "dd_peak_pct": round(dd_peak * 100, 1), "dd_init_pct": round(dd_init * 100, 1),
        "dd_days": int((pd.Timestamp(curve["date"].iloc[-1]) - pd.Timestamp(curve["date"].iloc[dd_i])).days),
        "dd_trough": str(curve["date"].iloc[dd_i])[:10],
        "n_exec": len(trades),
        "peak_positions": peak_pos, "avg_positions": round(avg_pos, 1), "max_streak": max_streak,
        "exposure_peak_pct": round(exp_peak * 100, 1), "exposure_avg_pct": round(exp_avg * 100, 1),
        "avg_r": round(avg_r, 3), "avg_r_no_top5": round(avg_r_no_top5, 3),
        "reject_insufficient": res.get("reject_insufficient"),
        "over_1.5R_count": over_r15,
        "selfcheck": selfcheck,
    }
    # R-048 扩展：return_raw=True → (metrics, res)；默认只返回 metrics（行为零变化）
    if return_raw:
        return metrics, res
    return metrics


# 锚点基准 = 0.012855（定案精确值 108/8401.26）实测复现值（2026-08-11 实测确认：
# 0.0129 近似版产 +397.4%（R-043 数字），0.012855 精确版 +391.0%——0.35% 精度差
# 在 26 年累积 5.7pp，R-044 统一精确值口径；R-043 数字为 0.0129 近似口径）
ANCHORS = [
    {"capital": 8401, "risk_ratio": 0.012855, "max_positions": 5,
     "expect_ret": 391.0, "expect_dd": -10.3, "label": "5仓"},
    {"capital": 8401, "risk_ratio": 0.012855, "max_positions": 8,
     "expect_ret": 515.2, "expect_dd": -9.8, "label": "8仓"},
    {"capital": 8401, "risk_ratio": 0.012855, "max_positions": 999,
     "expect_ret": 629.6, "expect_dd": -11.0, "label": "无限制"},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=DEFAULT_SIGNALS)
    ap.add_argument("--capital", type=float, default=8401.0)
    ap.add_argument("--risk-ratio", type=float, default=0.012855)
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--monthly-inject", type=float, default=0.0)
    ap.add_argument("--min-date", default=None)
    ap.add_argument("--risk-growth", action="store_true",
                    help="风险额随累计投入同步上调（注入路径的比例语义，审核 P1）")
    ap.add_argument("--anchor", action="store_true", help="锚点对账（V1）")
    args = ap.parse_args()

    if args.anchor:
        print("=== V1 锚点对账（R-043 已知点复现 · 偏差>1pp 停跑）===")
        ok = True
        for a in ANCHORS:
            r = run_one(args.signals, a["capital"], a["risk_ratio"], a["max_positions"])
            d_ret = r["total_ret_pct"] - a["expect_ret"]
            d_dd = r["dd_peak_pct"] - a["expect_dd"]
            mark = "✅" if abs(d_ret) < 1.0 else "❌"
            if abs(d_ret) >= 1.0:
                ok = False
            print(f"  {a['label']:<6} 收益 {r['total_ret_pct']:>7.1f}% (期望 {a['expect_ret']}, "
                  f"差 {d_ret:+.1f}pp) | 回撤 {r['dd_peak_pct']:>6.1f}% (期望 {a['expect_dd']}) {mark}")
        print("  V1 结论:", "全部通过 → 开跑网格" if ok else "有偏差 → 停跑查因")
        return

    r = run_one(args.signals, args.capital, args.risk_ratio, args.max_positions,
                args.monthly_inject, args.min_date, args.risk_growth)
    r["selfcheck_pass"] = all(r["selfcheck"].values())
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
