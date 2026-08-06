#!/usr/bin/env python3
"""C23 收紧方案 vs 现方案——资金约束下全量对比（T-024 后续 · 2026-08-06 老板拍板）

背景：T-024 信号层结论——C23（动量≤10% + 止损距离 0.5~3 元）avgR 0.974 vs
基线 0.872（+0.10）。老板要求补"资金约束下的全量对比"：模拟实盘视角
（5600 元 / 整手 / 多持仓）看收紧在资金约束下是否依然成立——
两半合起来才是完整对比依据。

口径（与 sim_capital 验收 / capital_grid_compare 完全一致）：
  - 信号源：prebreak S 级 / dn_confirm 1.5 / entry_20d 触发价 / 2023-07~2026-07 全市场
    （默认 = 产出/输出/sim_capital_20260806_full/signals.csv）
  - 资金配置：默认 1.5% × 3 持仓（T-023 折中档——避免 2 仓"已验证错误"和 5 仓
    极端回撤的干扰；--risk-ratio / --max-positions 可覆盖）
  - 初始资金 5600 元 / 整手 100 股 / 费用 佣金万1.3（最低1元）+ 印花税万5
  - 复算：vol_ratio / mom20 全部复用 tighten_compare（duckdb 同口径，
    与 T-024 全量 0 失败一致）；C23 过滤在信号层完成，模拟核心逻辑零改动
  - 过滤掩码：mom20 ≤ 10%（trigger / 20 交易日前 qfq 收盘 - 1）
             且 0.5 ≤ risk ≤ 3.0（止损距离，signals.csv risk 列）

每组指标：终值/收益%/胜率/avgR/盈亏比/最大回撤/回撤时长/笔数/年化笔数/100笔节奏/
          执行率/可执行池特征（成交均价、每股风险均值、被拒原因 TOP3、未成交信号avgR）
未成交信号 avgR 口径与 capital_grid_compare.pool_features 一致（触发但被资金约束拒绝）。

用法:
  python 项目/回测系统/c23_capital_compare.py --smoke 50      # 自检（前 50 笔触发信号）
  python 项目/回测系统/c23_capital_compare.py                 # 全量两方案对比
"""
import argparse
import datetime as _dt
import sys
from pathlib import Path

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

from 回测系统.capital_grid_compare import pool_features
from 回测系统.sim_capital import simulate_capital
from 回测系统.tighten_compare import (
    DEFAULT_MOM,
    RISK_MAX,
    RISK_MIN,
    enrich,
    load_triggered,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 默认信号源 = sim_capital 验收同口径全量信号（prebreak/S/dn_confirm1.5/3年全市场）
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "sim_capital_20260806_full" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_CAPITAL = 5600.0
DEFAULT_HOLD = "20d"
DEFAULT_GRADES = ["S"]
DEFAULT_MODE = "prebreak"

MOM_MAX = DEFAULT_MOM     # C23 动量阈值 = T-024 探索最优 10%


def c23_mask(df: pd.DataFrame, mom: float = MOM_MAX) -> pd.Series:
    """C23 过滤掩码：动量≤mom 且止损距离 0.5~3 元

    mom20 由 tighten_compare 复算（trigger / 20 交易日前 qfq 收盘 - 1，
    与引擎同口径）；risk 列 = trigger - stop，signals.csv 直接可用。
    """
    return ((df["mom20"].notna() & (df["mom20"] <= mom))
            & (df["risk"] >= RISK_MIN) & (df["risk"] <= RISK_MAX))


def run_pair(df: pd.DataFrame, mom: float, capital: float, risk_ratio: float,
             max_positions: int, hold: str = DEFAULT_HOLD,
             grades: list[str] | None = None) -> tuple[dict, dict]:
    """两方案各跑一组资金模拟，返回 (baseline, c23) 两组 (res, pool)"""
    grades = grades or []
    h = int(str(hold).replace("d", ""))
    base = simulate_capital(df, capital, risk_ratio, max_positions=max_positions,
                            mode=DEFAULT_MODE, hold=hold, grades=grades)
    base_pool = pool_features(base["trades"], df, h, mode=DEFAULT_MODE, grades=grades)

    df_c23 = df[c23_mask(df, mom)].copy()
    c23 = simulate_capital(df_c23, capital, risk_ratio, max_positions=max_positions,
                           mode=DEFAULT_MODE, hold=hold, grades=grades)
    c23_pool = pool_features(c23["trades"], df_c23, h, mode=DEFAULT_MODE, grades=grades)
    return (base, base_pool), (c23, c23_pool)


def _pf(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def render_report(base: tuple[dict, dict], c23: tuple[dict, dict], args) -> str:
    """渲染两方案对比报告（markdown）：总览并排表 + 池特征 + 节奏衰减 + 结论草稿"""
    br, bp = base
    cr, cp = c23
    n_c23 = int(cr["n_all"])
    n_base = int(br["n_all"])
    decay = n_c23 / n_base if n_base else 0.0
    lines = [
        "# C23 收紧方案 vs 现方案 · 资金约束下全量对比（T-024 后续）",
        "",
        (f"> 日期：{_dt.datetime.now().astimezone().date().isoformat()} · 背景：T-024 信号层结论——"
         "C23（动量≤10% + "
         "止损距离 0.5~3 元）avgR 0.974 vs 基线 0.872（+0.10）。本报告补上资金约束视角："
         "模拟实盘（5600 元 / 整手 / 多持仓）看收紧在资金约束下是否依然成立。"),
        (f"> 资金配置：{args.capital:,.0f} 元 × 单笔风险 {args.risk_ratio:.1%} × 持仓上限 "
         f"{args.max_positions} 只（T-023 折中档——2 仓已验证被资金约束扭曲、5 仓回撤极端，"
         f"3 仓兼顾分散与单笔资金占用）｜评级 {'/'.join(args.grades)}｜mode={DEFAULT_MODE} "
         f"hold={args.hold}"),
        (f"> 信号源：{Path(args.signals).name}（{'全量' if not args.smoke else f'自检前 {args.smoke} 笔触发'}｜"
         "prebreak / S / dn_confirm=1.5 / 2023-07~2026-07）｜C23 过滤 = mom20 ≤ "
         f"{MOM_MAX:.0%}（tighten_compare 复算）+ risk 0.5~3 元｜整手 100 股｜"
         "费用 佣金万1.3(最低1元)+印花税万5"),
        "",
        "## 一、两方案总览（并排对比）",
        "",
        ("| 指标 | 现方案（基线全信号） | C23 方案（过滤后） | 差异 |"),
        "|---|---:|---:|---:|",
        f"| 候选信号数（20d 触发） | {n_base:,} | {n_c23:,} | {n_c23 / n_base:.1%}（留存） |",
        f"| 实际可执行（笔） | {br['n_exec']:,} | {cr['n_exec']:,} | {cr['n_exec'] - br['n_exec']:+,} |",
        f"| 执行率 | {br['exec_rate']:.1f}% | {cr['exec_rate']:.1f}% | {cr['exec_rate'] - br['exec_rate']:+.1f}pp |",
        f"| 终值资金（元） | {br['end_balance']:,.2f} | {cr['end_balance']:,.2f} | {cr['end_balance'] - br['end_balance']:+,.2f} |",
        (
            f"| 总收益 | {br['total_pnl']:+,.2f} 元（{br['total_ret']:+.1f}%） | "
            f"{cr['total_pnl']:+,.2f} 元（{cr['total_ret']:+.1f}%） | {cr['total_ret'] - br['total_ret']:+.1f}pp |"
        ),
        f"| 胜率 | {br['win_rate']:.1%} | {cr['win_rate']:.1%} | {cr['win_rate'] - br['win_rate']:+.1%} |",
        f"| 平均R（成交口径） | {br['avg_r']:.3f} | {cr['avg_r']:.3f} | {cr['avg_r'] - br['avg_r']:+.3f} |",
        f"| 盈亏比（金额） | {_pf(br['profit_factor'])} | {_pf(cr['profit_factor'])} | — |",
        (
            f"| 最大回撤 | {br['max_dd']:,.2f} 元（{br['max_dd_pct']:.1f}%） | "
            f"{cr['max_dd']:,.2f} 元（{cr['max_dd_pct']:.1f}%） | {cr['max_dd_pct'] - br['max_dd_pct']:+.1f}pp |"
        ),
        f"| 回撤时长（天） | {br['dd_days']} | {cr['dd_days']} | {cr['dd_days'] - br['dd_days']:+d} |",
        f"| 平均持有（交易日） | {br['avg_hold_days']:.1f} | {cr['avg_hold_days']:.1f} | — |",
        f"| 年化笔数 | {br['per_year']:.1f} | {cr['per_year']:.1f} | {cr['per_year'] - br['per_year']:+.1f} |",
        f"| 100 笔节奏（月） | {br['months_for_100']:.1f} | {cr['months_for_100']:.1f} | — |",
        (
            f"| 单笔风险执行均值（元） | {br['risk_exec']['mean']:.2f}（超限额 {br['risk_exec']['over_risk_amt']} 笔） | "
            f"{cr['risk_exec']['mean']:.2f}（超限额 {cr['risk_exec']['over_risk_amt']} 笔） | — |"
        ),
        "",
        (
            "> 口径说明：回撤/资金曲线沿用 sim_capital 验收口径（现金余额峰值追踪，数值偏保守，两组一致可比）；"
            "avgR 为成交笔口径（R = (exit-entry)/risk，引擎触发价口径）。"
        ),
        "",
        "## 二、可执行池特征",
        "",
        ("| 指标 | 现方案（基线全信号） | C23 方案（过滤后） |"),
        "|---|---:|---:|",
        f"| 成交均价（元） | {bp['avg_price']:.2f} | {cp['avg_price']:.2f} |",
        f"| 每股风险均值（元） | {bp['avg_risk_ps']:.3f} | {cp['avg_risk_ps']:.3f} |",
        f"| 未成交信号 avgR | {bp['rej_avg_r']:+.3f} | {cp['rej_avg_r']:+.3f} |",
        "",
        "**被拒原因分布（TOP3）**",
        "",
        "| 方案 | 原因 | 笔数 |",
        "|---|---|---:|",
    ]
    for name, r in (("现方案", br), ("C23 方案", cr)):
        top = sorted(r["reasons"].items(), key=lambda x: -x[1])[:3] or [("（无）", 0)]
        for k, v in top:
            lines.append(f"| {name} | {k} | {v} |")
    lines += [
        "",
        "> 未成交信号 avgR = 触发但被资金约束挡在门外的信号集 20d 平均R（口径与 C23 版 12 组网格一致）。",
        "",
        "## 三、信号量与实盘节奏",
        "",
        (f"- 信号留存：{n_base:,} → {n_c23:,} 笔（{decay:.0%}），衰减 = 动量追高 + 止损过近/过远 "
         "两类信号被滤。"),
        (
            f"- 实盘执行节奏：基线年化 {br['per_year']:.1f} 笔 / C23 年化 {cr['per_year']:.1f} 笔；"
            f"100 笔检查点：基线 {br['months_for_100']:.1f} 个月 / C23 {cr['months_for_100']:.1f} 个月。"
        ),
        (
            f"- 50 笔 / 100 笔检查点口径（模拟实盘画像）：50 笔约 {50 / cr['per_year'] * 12:.1f} 个月"
            f"（C23），100 笔约 {cr['months_for_100']:.1f} 个月——见结论草稿判定。"
        ),
        "",
        "## 四、白话结论草稿",
        "",
    ]
    lines += _verdict(base, c23)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 出处：2026-08-06 老板拍板（C23 先隔离，全量对比后再决定是否替换）。"
                 "实现：项目/回测系统/c23_capital_compare.py；复算复用 tighten_compare"
                 "（T-024 同口径），模拟复用 sim_capital.simulate_capital（核心逻辑零改动）。"
                 "替换与否签字权归老板。")
    return "\n".join(lines)


def _verdict(base: tuple[dict, dict], c23: tuple[dict, dict]) -> list[str]:
    """数据驱动的结论草稿（最终由老板/助理复核）"""
    br, bp = base
    cr, cp = c23
    d_ret = cr["total_ret"] - br["total_ret"]
    d_r = cr["avg_r"] - br["avg_r"]
    d_dd = cr["max_dd_pct"] - br["max_dd_pct"]
    out = [
        f"- **资金约束下收紧是否依然成立**：收益 {br['total_ret']:+.1f}% → {cr['total_ret']:+.1f}%"
        f"（{d_ret:+.1f}pp），成交 avgR {br['avg_r']:.3f} → {cr['avg_r']:.3f}（{d_r:+.3f}），"
        f"最大回撤 {br['max_dd_pct']:.1f}% → {cr['max_dd_pct']:.1f}%（{d_dd:+.1f}pp）→ "
        + ("**与信号层结论同向，收紧在资金约束下依然成立**"
           if d_ret > 0 and d_r > 0
           else "收益/avgR 方向与信号层结论不一致，需人工复核（见下方明细）。"),
    ]
    out.append("")
    pace_ok = cr["n_all"] >= 100   # 信号层样本（C23 过滤后）仍 ≥ 100 笔验证需求
    out.append(f"- **信号量衰减后实盘节奏**：候选 {br['n_all']:,} → {cr['n_all']:,} 笔"
               f"（{cr['n_all'] / br['n_all']:.0%} 留存，信号层样本仍 "
               f"{'≥' if pace_ok else '<'} 100 笔验证需求"
               f"{'，统计意义不受影响' if pace_ok else '，样本不足需人工判断'}）；"
               f"资金层执行年化 {br['per_year']:.1f} → {cr['per_year']:.1f} 笔，"
               f"50 笔检查点约 {50 / cr['per_year'] * 12:.1f} 个月，"
               f"100 笔检查点 {br['months_for_100']:.1f} → {cr['months_for_100']:.1f} 个月"
               f"（{cr['months_for_100'] - br['months_for_100']:+.1f}）→ 两家均属资金层"
               "慢节奏范畴（既有口径：资金层 100 笔≈4 年不可行，只作执行可行性参考、"
               "不以之作为验证主口径），**信号层验证节奏不受收紧影响**。")
    out.append("")
    out.append(f"- **可执行池特征变化**：成交均价 {bp['avg_price']:.2f} → {cp['avg_price']:.2f} 元"
               f"（{cp['avg_price'] - bp['avg_price']:+.2f}），每股风险均值 "
               f"{bp['avg_risk_ps']:.3f} → {cp['avg_risk_ps']:.3f} 元；"
               f"未成交（被资金挡）信号 avgR {bp['rej_avg_r']:+.3f} → {cp['rej_avg_r']:+.3f}"
               f" → " + ("池右移（可买标的更贵/风险结构更适配 84 元风险额度），"
                         "资金约束挤压效应明显缓解"
                        if cp["avg_price"] > bp["avg_price"]
                        else "池未明显右移，资金约束挤压效应仍在"))
    out.append("")
    out.append("> 判定提醒：收益%与 avgR 同为关键判据；若收益改善但 avgR 回落，说明"
               "改善来自仓位结构而非单笔质量，需谨慎归因。最终替换与否由老板签字。")
    return out


def main() -> int:
    today = _dt.datetime.now().astimezone().date().strftime("%Y%m%d")
    ap = argparse.ArgumentParser(description="C23 收紧方案 vs 现方案 · 资金约束下全量对比（T-024 后续）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金（默认 5600）")
    ap.add_argument("--risk-ratio", type=float, default=0.02,
                    help="单笔风险比例（默认 2.0%%——G9 实盘线定稿参数 2026-08-06 老板拍板，"
                         "与网格实验 T-023 2.0%%×3仓 同口径；对照实验显式传旧值 0.015）")
    ap.add_argument("--max-positions", type=int, default=3,
                    help="最多同时持仓数（默认 3，T-023 折中档：2 仓已验证错误、5 仓回撤极端）")
    ap.add_argument("--mom-threshold", type=float, default=MOM_MAX,
                    help=f"C23 动量阈值（默认 {MOM_MAX}，T-024 探索最优）")
    ap.add_argument("--hold", default=DEFAULT_HOLD, choices=["5d", "10d", "20d"])
    ap.add_argument("--grades", nargs="+", default=DEFAULT_GRADES,
                    help="评级（默认 S，与 sim_capital 验收一致）")
    ap.add_argument("--smoke", type=int, default=0, help="自检：只处理前 N 笔触发信号")
    ap.add_argument("--out", default=str(OUT_DIR / f"C23资金约束对比-{today}.md"),
                    help="报告输出路径")
    args = ap.parse_args()

    df = load_triggered(Path(args.signals), args.smoke)
    print(f"[C23 对比] 基线 {len(df)} 笔 20d 触发 | duckdb 复算 vol_ratio/mom20（tighten_compare）...")
    df = enrich(df)
    n_c23 = int(c23_mask(df, args.mom_threshold).sum())
    print(f"[C23 对比] 复算完成（失败 {len(df) - df['vol_ratio'].notna().sum()} 笔）| "
          f"C23 过滤后 {n_c23} 笔（{n_c23 / len(df):.1%} 留存）")

    (base, c23) = run_pair(df, args.mom_threshold, args.capital, args.risk_ratio,
                           args.max_positions, hold=args.hold, grades=args.grades)
    br, cr = base[0], c23[0]
    print(f"[C23 对比] 现方案 → 终值 {br['end_balance']:,.0f} 元（{br['total_ret']:+.1f}%）| "
          f"{br['n_exec']} 笔 | 回撤 {br['max_dd_pct']:.1f}%")
    print(f"[C23 对比] C23 方案 → 终值 {cr['end_balance']:,.0f} 元（{cr['total_ret']:+.1f}%）| "
          f"{cr['n_exec']} 笔 | 回撤 {cr['max_dd_pct']:.1f}%")

    report = render_report(base, c23, args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
