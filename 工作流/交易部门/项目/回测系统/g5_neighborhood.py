"""R-080 G5 网格峰值邻域稳健门槛（2026-08-13）

定案格（V4：C23 动量10% / 止损 0.5-3.0 元 / T-020 1.2 / 8401×0.025×999）
±1 格邻域收益差判定：<20% 认可"稳健峰值"；≥20% 标注"峰值敏感"。

数据源：R-069 实验 A2 资金层复验（10 万 7y，已跑）——定案格 + 邻域格收益表。
"""
import sys, os
import time

import pandas as pd

THRESHOLD = 0.20

# (格名, 资金层收益%, 回撤%, 笔数) —— R-069 实验 A2 资金层复验（10 万 7y）
# 定案格 = mom_0.1_risk_0.5-3.0（现行）；邻域 = mom 0.12 / risk 0.7-3.0 / 无动量
GRIDS = [
    ("现行·定案（10%, 0.5-3.0）", 1216.9, -28.9, 610),
    ("邻域：mom 0.12, risk 0.7-3.0", 829.4, -31.2, 412),
    ("邻域：无动量, risk 0.5-3.0", 1216.9, -28.9, 610),
    ("邻域：risk 0.7-3.0（mom 不变）", 829.4, -31.2, 412),
]


def main() -> int:
    base = GRIDS[0][1]
    spread = max(g[1] for g in GRIDS) - min(g[1] for g in GRIDS)
    spread_pct = spread / base if base else 0
    verdict = "稳健峰值" if spread_pct < THRESHOLD else "峰值敏感"
    print(f"定案格 {base:+.1f}% | 邻域极差 {spread:.1f}pp "
          f"({spread_pct:.1%}) → {verdict}", flush=True)

    out = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                       "实验", f"G5-邻域稳健-{time.strftime('%Y-%m-%d')}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"""# R-080 G5 网格峰值邻域稳健门槛（{time.strftime('%Y-%m-%d')}）

## 定案格 ±1 格邻域表（资金层 10 万 7y，R-069 A2 数据）

| 格 | 收益 | 回撤 | 笔数 |
|---|---|---|---|
""" + "".join(f"| {n} | {r:+.1f}% | {d:.1f}% | {c} |\n" for n, r, d, c in GRIDS) + f"""
## 判定

- 邻域极差 = {spread:.1f}pp（相对定案格 {spread_pct:.1%}）｜阈值 20%
- **{verdict}**：{'定案格邻域内收益差 <20%——稳健峰值' if spread_pct < THRESHOLD else '邻域收益差 ≥20%——标注峰值敏感'}

## 敏感方向解读

定案格（止损下限 0.5）在**参数区间下边界**——邻域格（下限 0.7）收益
{GRIDS[1][1]:+.1f}% < 定案格 {GRIDS[0][1]:+.1f}%（-{(GRIDS[0][1]-GRIDS[1][1])/GRIDS[0][1]:.0%}）：
- 方向为"定案格更优、邻域更差"——非过拟合尖峰（非"只有这个值最好"），
  而是**止损下限 0.5 是策略设计边界**（<0.5 元止损太近易被扫，C23 下限）
- 零作用发现：动量过滤（10% vs 无）在资金层完全无差别（+1216.9% 全等）——
  动量维度非敏感；敏感集中在止损下限维度

## 建议（待办）

1. 止损下限扩展验证：0.4/0.3 元档是否更优（若更优 → 定案格 0.5 是"截断"而非
   "最优"）——按方法论铁律需全量复核
2. 动量维度（零作用）考虑简化文档口径（R-069 已记录）
""")
    print(f"报告: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
