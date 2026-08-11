#!/usr/bin/env python3
"""R-044 实验 3：同日竞争分析（控制仓位 = 质量选择？ · 2026-08-11）

控制变量 = cap_per_day（每日候选处理上限，sim_capital 语义：cap 计数先于持仓上限检查）：
  cap=0 全买（现状） / cap=1 只处理排序第一 / cap=2 前二 —— 排序统一 risk_mid
输出：各组收益/成交/胜率 + 被 cap 丢掉的次优候选后续 20d R（机会成本）
口径：8401 元 × 0.012855（108 元）× 8 仓，26 年全量，S 级 dn1.5 现行口径
"""
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

DEFAULT_SIGNALS = os.path.join("产出", "输出", "backtest_r43_t2", "signals.csv")
CAPITAL, RISK_RATIO, MAX_POS = 8401, 0.012855, 8


def main() -> None:
    df = pd.read_csv(DEFAULT_SIGNALS, encoding="utf-8-sig", dtype={"code": str})
    df = enrich(df)
    print("=== 实验 3：同日竞争（cap_per_day 控制 · risk_mid 排序统一）===")
    print(f"{'cap':<6}{'成交':>6}{'收益%':>8}{'avgR':>8}{'胜率':>8}{'峰值持仓':>8}")
    for cap in (0, 1, 2):
        res = simulate_capital(df, capital=CAPITAL, risk_ratio=RISK_RATIO,
                               max_positions=MAX_POS, mode="prebreak", hold="20d",
                               grades=["S"], c23=True, half_phase=True,
                               same_day_order="risk_mid", cap_per_day=cap)
        trades = res["trades"]
        pnls = [float(t.get("pnl", 0) or 0) for t in trades]
        ret = (res["end_balance"] - CAPITAL) / CAPITAL
        avg_r = float(np.mean(pnls)) / (CAPITAL * RISK_RATIO) if pnls else 0.0
        win = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0
        print(f"{cap:<6}{len(trades):>6}{ret * 100:>8.1f}{avg_r:>8.3f}{win:>8.1%}"
              f"{res.get('peak_positions', '—'):>8}")

    # 被 cap 丢掉的次优候选机会成本（信号层视角：同日 ≥2 候选的日子）
    print("\n=== 被 cap 丢掉的次优候选后续 20d R（信号层机会成本）===")
    t = df[(df["mode"] == "prebreak") & (df["triggered_20d"] == 1)].copy()
    t["day"] = t["date"].astype(str).str[:10]
    multi = t.groupby("day").size()
    multi_days = set(multi[multi >= 2].index)
    md = t[t["day"].isin(multi_days)].copy()
    # 按 risk_mid 排序：每股风险居中优先（|risk-1.5| 升序）
    md["risk_mid_key"] = (md["risk"] - 1.5).abs()
    md = md.sort_values(["day", "risk_mid_key"])
    best = md.groupby("day").head(1)
    rest = md[~md.index.isin(best.index)]
    print(f"同日 ≥2 候选的日子: {len(multi_days)} 天 | 候选 {len(md)} 笔（最优 {len(best)} / 次优 {len(rest)}）")
    for lbl, g in [("最优(risk_mid 第一)", best), ("被丢掉的次优", rest)]:
        if len(g):
            print(f"  {lbl:<18} {len(g):>4} 笔 | 20d avgR {g['r_20d'].mean():+.3f} | "
                  f"胜率 {(g['r_20d']>0).mean():.1%} | 累计R {g['r_20d'].sum():+.1f}")
    # 机会成本 = 次优组若全买的累计 R
    print(f"  机会成本（次优组 20d 累计 R）: {rest['r_20d'].sum():+.1f} R")


if __name__ == "__main__":
    main()
