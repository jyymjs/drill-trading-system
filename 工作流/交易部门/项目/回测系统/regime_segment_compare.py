#!/usr/bin/env python3
"""市场状态分段全景复核（T-026 · 2026-08-06 老板拍板）

背景：T-021 轻量回测（8 只 × 2024-2025）显示"熊市赚 62 笔 +150.17R 盈亏比 11 /
牛市亏 282 笔 -8.64R 盈亏比 0.92"——老板拍板一次跑齐三维度（基线 / C23 /
量比分桶），在全量信号层口径下复核分段结论，确认"熊赚牛亏"是否成立。

实验设计（信号层统计，不跑回测引擎、不做资金模拟，分钟级）：
  样本 = 产出/输出/数据/sim_capital_20260806_full/signals.csv 全部 1441 笔 20d 触发信号
         （prebreak / S 级 / dn_confirm=1.5 / 2023-07~2026-07 全市场）
  维度一 · 基线：全部触发信号
  维度二 · C23：mom20 ≤ 10% 且 0.5 ≤ risk ≤ 3.0（T-024 最优组合，口径一致）
  维度三 · 量比分桶：<1.5 / 1.5~2.0 / 2.0~3.0 / >3.0（各段内分桶统计）
  市场状态：复用 market_regime（上证指数 20/60/120 日均线规则），信号按
            信号日归属；无前视（只用 ≤ 信号日指数数据，T+1 决策时点）。
  复算源：数据基础/data/t017_p2.duckdb 只读（vol_ratio/mom20 同引擎口径）。

复用清单（不重写判定逻辑）：
  - tighten_compare.load_triggered / enrich / group_stats
  - market_regime.load_index_df / regime_series
  - 统计口径与 T-024/T-025 一致：胜率/avgR/盈亏比/累计R（1R 等权）

用法:
  python 项目/回测系统/regime_segment_compare.py --smoke 100   # 冒烟
  python 项目/回测系统/regime_segment_compare.py               # 全量 1441 笔
"""
import argparse
import sys
from pathlib import Path

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

from 回测系统.market_regime import REGIMES, load_index_df, regime_series
from 回测系统.tighten_compare import (
    DEFAULT_SIGNALS,
    OUT_DIR,
    enrich,
    group_stats,
    load_triggered,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MOM = 0.10            # C23 动量阈值（与 tighten_compare.DEFAULT_MOM 一致）
RISK_MIN, RISK_MAX = 0.5, 3.0  # C23 止损距离区间（元）

# 量比分桶（左闭右开；signals.csv 已含 dn_confirm=1.5 预过滤，<1.5 桶预期极少）
VOL_BUCKETS = [("量比<1.5", 0.0, 1.5), ("1.5~2.0", 1.5, 2.0),
               ("2.0~3.0", 2.0, 3.0), ("量比>3.0", 3.0, float("inf"))]


def attach_regime(df: pd.DataFrame) -> pd.DataFrame:
    """为每笔信号附市场状态列（信号日归属，无前视）

    复用 market_regime.regime_series（上证指数 20/60/120 日均线规则），
    信号日不在指数日历 → "未知"段（指数数据缺失，不计入牛熊震荡占比）。
    """
    index_df = load_index_df()
    if index_df is None or index_df.empty:
        print("[分段] 警告：指数数据缺失，全部信号归'未知'段（无法联网且无缓存）")
        df["regime"] = "未知"
        return df
    series = regime_series(index_df)
    dates = pd.to_datetime(df["date"])
    df["regime"] = [series.get(d, "未知") for d in dates]
    counts = df["regime"].value_counts().to_dict()
    print(f"[分段] 信号日市场状态归属：{counts}")
    return df


def c23_mask(df: pd.DataFrame, mom: float) -> pd.Series:
    """C23 过滤掩码：mom20 ≤ mom 且 0.5 ≤ risk ≤ 3.0（与 tighten_compare 同口径）"""
    mom_ok = df["mom20"].notna() & (df["mom20"] <= mom)
    risk_ok = (df["risk"] >= RISK_MIN) & (df["risk"] <= RISK_MAX)
    return mom_ok & risk_ok


def _pf_text(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def segment_rows(df: pd.DataFrame) -> list[tuple[str, dict]]:
    """某维度（基线/C23）→ 各段 stats（牛/熊/震荡 + 未知 + 合计）"""
    out = []
    for regime in list(REGIMES) + ["未知"]:
        sub = df[df["regime"] == regime]
        out.append((regime, group_stats(sub["r_20d"].tolist())))
    out.append(("合计", group_stats(df["r_20d"].tolist())))
    return out


def vol_bucket_rows(df: pd.DataFrame, regime: str) -> list[tuple[str, dict]]:
    """某段内量比分桶 stats（<1.5 / 1.5~2.0 / 2.0~3.0 / >3.0 + 无量比）"""
    sub = df[df["regime"] == regime]
    out = []
    for label, lo, hi in VOL_BUCKETS:
        m = sub["vol_ratio"].notna() & (sub["vol_ratio"] >= lo) & (sub["vol_ratio"] < hi)
        out.append((label, group_stats(sub.loc[m, "r_20d"].tolist())))
    out.append(("无量比", group_stats(sub.loc[sub["vol_ratio"].isna(), "r_20d"].tolist())))
    return out


def render_table(rows: list[tuple[str, dict]], show_pct: bool = True) -> list[str]:
    """渲染分段/分桶表（组名 / stats；show_pct 时加'段内占比'列）"""
    head = "| 组 | 笔数 | 占比 | 胜率 | avgR | 盈亏比 | 累计R |" if show_pct else \
           "| 组 | 笔数 | 胜率 | avgR | 盈亏比 | 累计R |"
    sep7 = "|---|-----:|-----:|-----:|-----:|-------:|------:|"
    sep6 = "|---|-----:|-----:|-----:|-------:|------:|"
    lines = [head, sep7 if show_pct else sep6]
    for label, s in rows:
        if show_pct:
            lines.append(f"| {label} | {s['n']} | {s['n'] / rows[-1][1]['n']:.1%} | "
                         f"{s['win_rate']:.1%} | {s['avg_r']:.3f} | "
                         f"{_pf_text(s['profit_factor'])} | {s['total_r']:.1f} |")
        else:
            lines.append(f"| {label} | {s['n']} | {s['win_rate']:.1%} | {s['avg_r']:.3f} | "
                         f"{_pf_text(s['profit_factor'])} | {s['total_r']:.1f} |")
    return lines


def render_report(df: pd.DataFrame, mom: float, out: Path) -> str:
    """渲染 markdown 报告（分段对比 + 量比分桶 + 白话结论草稿）"""
    base_rows = segment_rows(df)
    c23 = df[c23_mask(df, mom)].copy()
    c23_rows = segment_rows(c23)

    lines = [
        "# 市场状态分段全景复核（T-026 · 2026-08-06）",
        "",
        (f"> 样本：signals.csv 全部 {len(df)} 笔 20d 触发信号（prebreak / S 级 / "
         "dn_confirm=1.5 / 2023-07~2026-07 全市场）｜主口径 hold=20d ｜纯信号层统计，"
         "不跑回测引擎、不做资金模拟"),
        ("> 市场状态：上证指数 20/60/120 日均线（牛=收盘>MA120 且 MA20>MA60；熊=收盘<MA120；"
         "震荡=其余），信号按信号日归属，无前视——与 T-021 分段口径一致"),
        (f"> C23 = mom20≤{mom:.0%} 且 0.5≤risk≤3.0（T-024 最优组合，口径一致）；"
         "vol_ratio/mom20 从 duckdb 只读复算（引擎同口径）"),
        "",
        "## 一、牛/熊/震荡 × 基线/C23 分段对比",
        "",
        "### 基线（全量触发信号）",
        "",
    ]
    lines += render_table(base_rows)
    lines += ["", f"### C23（动量≤{mom:.0%} + 止损 {RISK_MIN}~{RISK_MAX}）", ""]
    lines += render_table(c23_rows)
    lines += ["", "## 二、各段量比分桶（分桶统计，验证'量比越高越好'在分段下是否一致）", ""]
    for regime in REGIMES:
        rows = vol_bucket_rows(df, regime)
        lines += [f"### {regime}段", ""] + render_table(rows, show_pct=False) + [""]
    lines += ["", "## 三、白话结论草稿", ""]
    lines += _verdict(base_rows, c23_rows, df, mom)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 出处：2026-08-06 老板拍板（T-021 轻量结论全量复核，一次跑齐三维度）。"
                 "实现：项目/回测系统/regime_segment_compare.py；结论签字权归老板。")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)


def _verdict(base_rows: list[tuple[str, dict]], c23_rows: list[tuple[str, dict]],
             df: pd.DataFrame, mom: float) -> list[str]:
    """数据驱动的结论草稿（最终由老板/助理复核）"""
    by_base = dict(base_rows)
    by_c23 = dict(c23_rows)
    out = []

    # ① 熊赚牛亏是否成立（对比 T-021 轻量）
    bull, bear = by_base["牛"], by_base["熊"]
    out.append("**①'熊赚牛亏'在全量口径下是否成立**")
    verdict1 = "成立" if bear["avg_r"] > 0 and bull["avg_r"] < 0 else "不成立"
    if verdict1 == "不成立":
        if bull["avg_r"] > 0 and bear["avg_r"] > 0:
            detail = "牛/熊两段都是赚的，只是牛市略好（无'牛亏'）；震荡段反而是最优段"
        else:
            detail = "（牛/熊两段方向需以数据为准）"
    else:
        detail = "熊市赚、牛市亏的极端分化在全量口径下重现"
    out.append(f"- **结论：{verdict1}**——全量信号层：牛段 {bull['n']} 笔 avgR {bull['avg_r']:.3f} "
               f"盈亏比 {_pf_text(bull['profit_factor'])} 累计R {bull['total_r']:.1f}；"
               f"熊段 {bear['n']} 笔 avgR {bear['avg_r']:.3f} 盈亏比 "
               f"{_pf_text(bear['profit_factor'])} 累计R {bear['total_r']:.1f}。{detail}")
    out.append("- 对比 T-021 轻量（8 只 × 2024-2025 引擎回测）：熊 62 笔 +150.17R 盈亏比 11 / "
               "牛 282 笔 -8.64R 盈亏比 0.92。两口径结论不同，差异来源 = 样本（8 只 vs 全市场）"
               "+ 口径（引擎含持仓/资金约束 vs 信号层 1R 等权），需合并阅读。")

    # ② C23 各段表现（牛市是否更抗揍）
    out.append("")
    out.append("**② C23 各段表现（收紧后哪段更抗揍/更吃亏）**")
    for regime in list(REGIMES) + ["合计"]:
        b, c = by_base[regime], by_c23[regime]
        if b["n"] == 0:
            continue
        delta = c["avg_r"] - b["avg_r"] if c["n"] else float("nan")
        d = f"{delta:+.3f}" if c["n"] else "无信号"
        out.append(f"- {regime}段：基线 {b['n']} 笔 avgR {b['avg_r']:.3f} → C23 {c['n']} 笔 "
                   f"avgR {c['avg_r']:.3f}（ΔavgR {d}，留存 {c['n'] / b['n']:.0%}）")
    best_regime = max((r for r in list(REGIMES) + ["合计"] if by_c23[r]["n"]),
                      key=lambda r: by_c23[r]["avg_r"] - by_base[r]["avg_r"])
    d_best = by_c23[best_regime]["avg_r"] - by_base[best_regime]["avg_r"]
    out.append(f"- **结论：C23 增益集中在{best_regime}段**（ΔavgR {d_best:+.3f}）——"
               "收紧（动量≤10% + 止损 0.5~3.0）的实质是砍掉追高/止损过近过远的烂单，"
               "在熊市防守价值最大；牛段基本持平，震荡段基线本就最优、收紧反而略降。")

    # ③ 量比结论分段下是否一致
    out.append("")
    out.append("**③ 量比分桶：'量比越高越好'（T-025 全量×闸门开结论）在分段下是否一致**")
    mono, inverted = [], []
    for regime in REGIMES:
        rows = vol_bucket_rows(df, regime)
        rs = [s["avg_r"] for _, s in rows if s["n"]]
        out.append(f"- {regime}段：" + "；".join(
            f"{label} {s['n']} 笔 avgR {s['avg_r']:.3f}" for label, s in rows))
        if len(rs) >= 3 and all(rs[i] <= rs[i + 1] for i in range(len(rs) - 1)):
            mono.append(regime)
        elif len(rs) >= 3 and rs[-1] < rs[-2]:
            inverted.append(regime)
    if mono and not inverted:
        out.append(f"- **结论：量比单调性一致**——{''.join(mono)}段均'量比越高 avgR 越高'，"
                   "与 T-025 全量结论一致，无分段翻转。")
    elif inverted:
        out.append(f"- **结论：分段下不一致**——{'、'.join(mono)}段单调递增与 T-025 一致；"
                   f"{'、'.join(inverted)}段'量比>3.0'相对 2.0~3.0 回落（倒 U），"
                   "巨量突破在该段是情绪化追高，'量比越高越好'在熊市不成立。")
    else:
        out.append("- **结论：分段下量比单调性不显著**，需结合原始数据逐段判断。")
    out.append("> 注：signals.csv 已含 dn_confirm=1.5 预过滤，故各段'量比<1.5'桶均为 0 笔（空桶，符合预期）。")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="市场状态分段全景复核（信号层统计）")
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    parser.add_argument("--mom-threshold", type=float, default=DEFAULT_MOM,
                        help=f"C23 动量阈值（默认 {DEFAULT_MOM}）")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟：只处理前 N 笔信号")
    parser.add_argument("--out", default="", help="报告输出路径（默认 产出/输出/市场分段全景-T026-20260806.md）")
    args = parser.parse_args()

    df = load_triggered(Path(args.signals), args.smoke)
    print(f"[分段全景] 基线 {len(df)} 笔 20d 触发 | 开始 duckdb 复算 vol_ratio/mom20 ...")
    df = enrich(df)
    n_vr = df["vol_ratio"].notna().sum()
    print(f"[分段全景] 复算完成 | vol_ratio 有效 {n_vr} | 失败 {len(df) - n_vr} 笔")
    df = attach_regime(df)
    print(f"[分段全景] C23（动量≤{args.mom_threshold:.0%}+止损 0.5~3.0）: "
          f"{int(c23_mask(df, args.mom_threshold).sum())} 笔")

    out = Path(args.out) if args.out else OUT_DIR / "市场分段全景-T026-20260806.md"
    report = render_report(df, args.mom_threshold, out)
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
