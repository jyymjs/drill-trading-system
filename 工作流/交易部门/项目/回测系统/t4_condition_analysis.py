#!/usr/bin/env python3
"""T-4.4 第一步：条件 S 档贡献量化分析（只读 signals.csv）

定位综合评级聚合规则的问题：
  1. 各条件（PT/TY/DN/DL/LK/SF）S 档 vs 非 S 档的平均 R——哪个条件的 S 档在拖累
  2. S 档数量效应：S 个数 0-6 的平均 R——「至少 3 个 S」是否合理
  3. 综合 S 级信号的条件构成
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pandas as pd

df = pd.read_csv("项目/回测输出/backtest/20230701_20260804/signals.csv", encoding="utf-8-sig")
tr = df[df["triggered_20d"] == 1]
print(f"总信号: {len(tr)} | normal: {len(tr[tr['mode']=='normal'])} | "
      f"prebreak: {len(tr[tr['mode']=='prebreak'])}")
print()

CONDS = ["PT", "TY", "DN", "DL", "LK", "SF"]

for mode in ("normal", "prebreak"):
    sub = tr[tr["mode"] == mode].copy()
    print("=" * 62)
    print(f"{mode} 模式：各条件 S 档 vs 非 S 档（20d 平均R，n）")
    print("=" * 62)
    for c in CONDS:
        s = sub[sub[c] == "S"]
        ns = sub[sub[c] != "S"]
        avg = lambda x: f"{x['r_20d'].mean():.3f}" if len(x) else "  -  "
        print(f"  {c}: S档={avg(s)}({len(s):>6})   vs   非S={avg(ns)}({len(ns):>6})")

    print()
    sub["S_count"] = sum([sub[c].eq("S") for c in CONDS])
    print(f"  {mode}：S 档数量效应（20d 平均R，n）")
    for k in sorted(sub["S_count"].unique()):
        x = sub[sub["S_count"] == k]
        print(f"    {k} 个S: {x['r_20d'].mean():.3f}  ({len(x):>6})")

    # 综合 S 级信号的条件构成
    gs = sub[sub["grade"] == "S"]
    if len(gs):
        print(f"\n  综合S级信号 {len(gs)} 个，各条件 S 占比：")
        for c in CONDS:
            print(f"    {c}=S: {(gs[c]=='S').mean()*100:.0f}%")

    print()
