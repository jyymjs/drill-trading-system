#!/usr/bin/env python3
"""R-043 T3 扩展：总资产口径回撤段明细（2026-08-11 · 承接 capital_dd_recalc）

复用 capital_dd_recalc.build_total_asset_curve（逐日总资产 = 现金 + Σ持仓×收盘），
输出全部回撤段（深度/时长/恢复期），补 R-043 方案 T3 的"逐段明细"缺口。
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import pandas as pd
import numpy as np

from 回测系统.tighten_compare import enrich  # noqa: E402
from 回测系统.sim_capital import simulate_capital  # noqa: E402
from 回测系统.capital_dd_recalc import build_total_asset_curve, _kline  # noqa: E402


def find_drawdowns(curve: pd.DataFrame, min_depth: float = 0.05) -> pd.DataFrame:
    """逐段回撤（历史峰值→谷底→恢复至新高；min_depth 过滤小段）"""
    vals = curve["total_asset"].astype(float).values
    dates = curve["date"].values
    n = len(vals)
    peak = vals[0]
    peak_i = 0
    in_dd = False
    start_i = trough_i = 0
    trough = peak
    out = []

    def _close_seg(recovered: bool):
        nonlocal in_dd
        depth = (trough - peak) / peak
        if depth <= -min_depth:
            out.append({"start": str(dates[start_i])[:10], "trough": str(dates[trough_i])[:10],
                        "end": str(dates[peak_i])[:10] if recovered else "未恢复",
                        "peak": round(peak, 1), "trough_val": round(trough, 1),
                        "depth_pct": round(depth * 100, 1),
                        "trough_days": (pd.Timestamp(dates[trough_i]) - pd.Timestamp(dates[start_i])).days})
        in_dd = False

    for i in range(1, n):
        if vals[i] > peak:
            if in_dd:
                _close_seg(recovered=True)
            peak = vals[i]
            peak_i = i
        else:
            if not in_dd:
                in_dd = True
                start_i = peak_i
                trough_i = i
                trough = vals[i]
            elif vals[i] < trough:
                trough_i = i
                trough = vals[i]
    if in_dd:
        _close_seg(recovered=False)
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--capital", type=float, default=8401.0)
    ap.add_argument("--risk-ratio", type=float, default=0.0129)
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--duckdb", default="数据基础/行情数据/t017_p2.duckdb")
    ap.add_argument("--min-depth", type=float, default=0.05)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.signals, encoding="utf-8-sig")
    df = enrich(df)
    res = simulate_capital(df, capital=args.capital, risk_ratio=args.risk_ratio,
                           max_positions=args.max_positions, mode="prebreak", hold="20d",
                           grades=["S"], c23=True, half_phase=True)
    curve = build_total_asset_curve(res["trades"], args.capital,
                                    lambda c: _kline(int(c), args.duckdb))
    segs = find_drawdowns(curve, min_depth=args.min_depth)
    print(f"总资产口径逐段回撤（深度 >{args.min_depth:.0%}，共 {len(segs)} 段）:")
    print(f"{'起':<12}{'谷':<12}{'峰值':>9}{'谷值':>9}{'深度':>8}{'谷距起':>8}")
    print("-" * 60)
    for _, r in segs.sort_values("depth_pct").iterrows():
        print(f"{r['start']:<12}{r['trough']:<12}{r['peak']:>9.0f}{r['trough_val']:>9.0f}"
              f"{r['depth_pct']:>7.1f}%{r['trough_days']:>7}d")
    # 最大回撤汇总
    vals = curve["total_asset"].astype(float).values
    peak = np.maximum.accumulate(vals)
    dd = (vals - peak) / peak
    i = int(np.argmin(dd))
    print(f"\n最大回撤: {dd[i]:.1%}（{str(curve['date'].iloc[i])[:10]}）| 终值: {vals[-1]:.0f} 元")
    if args.out:
        segs.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"明细 → {args.out}")


if __name__ == "__main__":
    main()
