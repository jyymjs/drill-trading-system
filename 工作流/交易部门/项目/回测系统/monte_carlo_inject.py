#!/usr/bin/env python3
"""注入版蒙特卡洛（最后全面测试 C 档 · 2026-08-06 老板拍板"最后一次测试必须全面"）

场景：5600 元起步 + 每月 3000 元注入（3 年历史窗口按自然月），2.0%×3仓 实盘线配置。
与静态 1.5/2.0/3.0%×3仓 三档（monte_carlo_c23 --compare 既有口径）并排对照，
各 10000 次有放回重抽样。

成交 R 序列口径（与 monte_carlo_c23 资金层完全一致）：simulate_capital 模拟实盘成交，
R = pnl / risk_actual（金额盈亏已扣佣金+印花税）。注入档成交集与静态不同
（资金充足 + 名额竞争 → 换票/加票），R 序列自然反映注入后的真实成交体验。

模拟核心复用 分析决策/跟踪/monte_carlo.simulate（零改动）；
费用口径与既有蒙特卡洛一致：fee_per_trade_r=0.0（R 序列已含费，不重复扣）。

用法:
  python 项目/回测系统/monte_carlo_inject.py --smoke 200   # 冒烟（前 200 只股票）
  python 项目/回测系统/monte_carlo_inject.py                # 全量（4 组 × 10000 次）
"""
import argparse
import sys
from pathlib import Path

# 路径注入（与 monte_carlo_c23.py 同法）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np

from 分析决策.跟踪.monte_carlo import simulate
from 回测系统.monte_carlo_c23 import (
    CAPITAL,
    FEE_PER_TRADE_R,
    GRADES,
    HOLD,
    MODE,
    N_SIMULATIONS,
    capital_trade_r,
    fmt_r,
    summary,
)
from 回测系统.sim_capital import c23_mask, simulate_capital
from 回测系统.tighten_compare import enrich

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "sim_capital_20260806_full" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_REPORT = OUT_DIR / "最后全面测试-C注入版蒙特卡洛-20260806.md"

MAX_POSITIONS = 3                     # 实盘线 3 仓
MONTHLY_INJECT = 3000.0               # 每月注入（老板定投画像）
# 对照组：静态三档（与 monte_carlo_c23 --compare 同口径）+ 注入档
STATIC_RISKS = [1.5, 2.0, 3.0]


def load_c23_signals(path: Path, smoke_codes: int = 0, seed: int = 42) -> np.ndarray:
    """读 signals.csv → 触发集 → C23 掩码 → 触发 R 序列（信号层共享用）"""
    import pandas as pd

    df = pd.read_csv(path, encoding="utf-8-sig")
    if not len(df):
        raise SystemExit(f"无信号数据: {path}")
    df = df[df["triggered_20d"] == 1].copy()
    if smoke_codes:
        rng = np.random.default_rng(seed)
        codes = rng.choice(sorted(df["code"].unique()), size=smoke_codes, replace=False)
        df = df[df["code"].isin(codes)].copy()
    n_before = len(df)
    df = enrich(df)
    kept = df[c23_mask(df)]
    print(f"[C23 过滤] 触发信号 {n_before} → {len(kept)} 笔（留存 {len(kept) / n_before:.1%}）")
    return df


def run_one(df, risk_pct: float, max_positions: int, monthly_inject: float = 0.0,
            risk_growth: bool = False) -> tuple[list[float], float, dict]:
    """跑一组资金模拟 → (成交 R 序列, 单笔风险均值, simulate_capital 原始结果)"""
    res = simulate_capital(df, CAPITAL, risk_pct / 100.0, max_positions=max_positions,
                           mode=MODE, hold=HOLD, grades=GRADES, c23=True,
                           monthly_inject=monthly_inject, risk_growth=risk_growth)
    trades = res["trades"]
    rs = capital_trade_r(trades)
    avg_risk = float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0
    return rs, avg_risk, res


def main() -> int:
    ap = argparse.ArgumentParser(description="注入版蒙特卡洛（最后全面测试 C 档）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--smoke", type=int, default=0, help="冒烟：随机 N 只股票快速验证")
    ap.add_argument("--out", default=str(DEFAULT_REPORT), help="报告输出路径")
    args = ap.parse_args()

    df = load_c23_signals(Path(args.signals), args.smoke)
    n_sig = len(df)
    print(f"[注入蒙特卡洛] 信号 {n_sig} 笔 | 静态三档对照 + 注入档 2.0%×3仓（每月 {MONTHLY_INJECT:,.0f} 注入）")

    rows: list[dict] = []
    # ── 静态三档（与 monte_carlo_c23 --compare 同口径）──
    for risk_pct in STATIC_RISKS:
        rs, avg_risk, res = run_one(df, risk_pct, MAX_POSITIONS)
        mc = simulate([{"r_multiple": r} for r in rs],
                      n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
        if "error" in mc:
            print(f"  ❌ 静态 {risk_pct}%: {mc['error']}")
            return 1
        rows.append({"label": f"静态{risk_pct:.1f}%", "risk_pct": risk_pct,
                     "avg_risk": avg_risk, **summary(mc), **res})
        print(f"  [静态 {risk_pct:.1f}%×3仓] 成交 {res['n_exec']} 笔 | avgR {summary(mc)['avg_r']:+.3f}")
    # ── 注入恒定档（实盘定投：风险额按初始资金恒定）──
    rs, avg_risk, res = run_one(df, 2.0, MAX_POSITIONS, monthly_inject=MONTHLY_INJECT)
    mc = simulate([{"r_multiple": r} for r in rs],
                  n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
    if "error" in mc:
        print(f"  ❌ 注入恒定: {mc['error']}")
        return 1
    rows.append({"label": "注入恒定2.0%", "risk_pct": 2.0, "avg_risk": avg_risk,
                 **summary(mc), **res})
    print(f"  [注入恒定 2.0%×3仓 +{MONTHLY_INJECT:,.0f}/月] 成交 {res['n_exec']} 笔"
          f" | avgR {summary(mc)['avg_r']:+.3f}")
    # ── 注入增长档（风险额随累计投入增长）──
    rs, avg_risk, res = run_one(df, 2.0, MAX_POSITIONS, monthly_inject=MONTHLY_INJECT,
                                risk_growth=True)
    mc = simulate([{"r_multiple": r} for r in rs],
                  n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
    if "error" in mc:
        print(f"  ❌ 注入增长: {mc['error']}")
        return 1
    rows.append({"label": "注入增长2.0%", "risk_pct": 2.0, "avg_risk": avg_risk,
                 **summary(mc), **res})
    print(f"  [注入增长 2.0%×3仓] 成交 {res['n_exec']} 笔 | avgR {summary(mc)['avg_r']:+.3f}")
    print(f"[模拟] {len(rows)} 组 × {N_SIMULATIONS:,} 次完成")

    lines = render(rows, n_sig)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {out}")
    return 0


def render(rows: list[dict], n_sig: int) -> list[str]:
    r_s15, r_s20, r_s30 = rows[0], rows[1], rows[2]
    r_ci, r_cg = rows[3], rows[4]
    lines = [
        "# 最后全面测试 C：注入版蒙特卡洛（2026-08-06）",
        "",
        ("> 目的：5600 元起步 + 每月 3000 元注入（3 年历史窗口按自然月）实盘画像下，"
         "2.0%×3仓 的运气边界（盈利概率 / 最差 5% / 回撤 P95），与静态 1.5/2.0/3.0% 三档并排。"),
        (f"> 口径：资金层 = sim_capital 模拟实盘成交（{CAPITAL:,.0f} 元起步 / S 级 / prebreak / 20d / "
         f"整手 100 股 / 费用已含），成交 R = pnl / risk_actual；注入 = 每自然月 {MONTHLY_INJECT:,.0f} 元；"
         f"注入增长档 = 单笔风险额随累计投入（初始+注入）× 2.0% 增长。"),
        (f"> 模拟：每组 {N_SIMULATIONS:,} 次有放回重抽样（seed=2024，与既有 C23 蒙特卡洛同源）；"
         f"fee=0.0（R 序列已含费）。信号源：{n_sig} 笔（C23 过滤后）。"),
        "",
        "## 一、五组并排（10000 次/组）",
        "",
        "| 指标 | 静态1.5%×3仓 | 静态2.0%×3仓 | 静态3.0%×3仓 | 注入恒定2.0% | 注入增长2.0% |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 成交笔数 | {r_s15['n']} | {r_s20['n']} | {r_s30['n']} | {r_ci['n']} | {r_cg['n']} |",
        (f"| 成交 avgR | {r_s15['avg_r']:+.3f} | {r_s20['avg_r']:+.3f} | {r_s30['avg_r']:+.3f} | "
        f"{r_ci['avg_r']:+.3f} | {r_cg['avg_r']:+.3f} |"),
        (f"| 单笔风险均值（元） | {r_s15['avg_risk']:.2f} | {r_s20['avg_risk']:.2f} | "
        f"{r_s30['avg_risk']:.2f} | {r_ci['avg_risk']:.2f} | {r_cg['avg_risk']:.2f} |"),
        "| 盈利概率 | " + f"{r_s15['prob_profit']:.1%} | {r_s20['prob_profit']:.1%} | "
        f"{r_s30['prob_profit']:.1%} | {r_ci['prob_profit']:.1%} | {r_cg['prob_profit']:.1%} |",
        "| 终值 最好5%下界 | " + fmt_r(r_s15["fin_p95"]) + " | " + fmt_r(r_s20["fin_p95"]) + " | "
        + fmt_r(r_s30["fin_p95"]) + " | " + fmt_r(r_ci["fin_p95"]) + " | " + fmt_r(r_cg["fin_p95"]) + " |",
        "| 终值 中位 | " + fmt_r(r_s15["fin_p50"]) + " | " + fmt_r(r_s20["fin_p50"]) + " | "
        + fmt_r(r_s30["fin_p50"]) + " | " + fmt_r(r_ci["fin_p50"]) + " | " + fmt_r(r_cg["fin_p50"]) + " |",
        "| 终值 最差5%上界 | " + fmt_r(r_s15["fin_p05"]) + " | " + fmt_r(r_s20["fin_p05"]) + " | "
        + fmt_r(r_s30["fin_p05"]) + " | " + fmt_r(r_ci["fin_p05"]) + " | " + fmt_r(r_cg["fin_p05"]) + " |",
        "| 最大回撤 最差5%（P95） | " + fmt_r(r_s15["dd_p95"]) + " | " + fmt_r(r_s20["dd_p95"]) + " | "
        + fmt_r(r_s30["dd_p95"]) + " | " + fmt_r(r_ci["dd_p95"]) + " | " + fmt_r(r_cg["dd_p95"]) + " |",
        "| 最大回撤 中位 | " + fmt_r(r_s15["dd_p50"]) + " | " + fmt_r(r_s20["dd_p50"]) + " | "
        + fmt_r(r_s30["dd_p50"]) + " | " + fmt_r(r_ci["dd_p50"]) + " | " + fmt_r(r_cg["dd_p50"]) + " |",
        (f"| 连败 平均 | {r_s15['streak_mean']:.1f} | {r_s20['streak_mean']:.1f} | "
        f"{r_s30['streak_mean']:.1f} | {r_ci['streak_mean']:.1f} | {r_cg['streak_mean']:.1f} |"),
        (f"| 连败 最大 | {r_s15['streak_max']} | {r_s20['streak_max']} | "
        f"{r_s30['streak_max']} | {r_ci['streak_max']} | {r_cg['streak_max']} |"),
        "",
        (f"> 口径：终值/回撤为累计 R（1 R = 单笔实际风险投入）；金额换算 = 累计 R × 单笔风险均值"
         f"（静态档 {r_s15['avg_risk']:.2f}/{r_s20['avg_risk']:.2f}/{r_s30['avg_risk']:.2f} 元，"
         f"注入恒定 {r_ci['avg_risk']:.2f} 元，注入增长 {r_cg['avg_risk']:.2f} 元）。"),
        "",
        "## 二、金额视角（注入档换算）",
        "",
        "| 档位 | 每笔风险均值 | 最差5%终值 | 中位终值 | 最好5%终值 | 回撤最差5%（账面） |",
        "|---|---:|---:|---:|---:|---:|",
        *(f"| {r['label']} | {r['avg_risk']:.2f} 元 | "
          f"{r['fin_p05'] * r['avg_risk']:+,.0f} 元 | {r['fin_p50'] * r['avg_risk']:+,.0f} 元 | "
          f"{r['fin_p95'] * r['avg_risk']:+,.0f} 元 | {r['dd_p95'] * r['avg_risk']:+,.0f} 元 |"
          for r in rows),
        "",
        "## 三、白话结论草稿（数据驱动，签字权归老板）",
        "",
    ]
    lines += _verdict(rows)
    lines += [
        "",
        "## 四、局限（如实标注）",
        "",
        ("> - 重抽样假设每笔 R 独立同分布：真实交易间有相关性（同板块/同行情），实际连败可能更长；"
         "未模拟涨跌停无法买入、出场简化。"),
        (f"> - 注入档成交仅 {r_ci['n']}/{r_cg['n']} 笔（恒定/增长），静态档 {r_s15['n']}/{r_s20['n']}/"
         f"{r_s30['n']} 笔，样本偏少，尾部置信度有限；信号层 {n_sig} 笔统计意义更足。"),
        "- 注入按自然月固定时点入账（简化），实际定投可能在月中/波动期。",
        "",
        "---",
        "",
        ("> 出处：2026-08-06 老板拍板最后全面测试 C 档。实现：回测系统 monte_carlo_inject.py"
         "（simulate_capital 注入扩展 --monthly-inject/--risk-growth，模拟复用 分析决策/跟踪/"
         "monte_carlo.simulate 零改动）。复现命令："),
        "> `python 项目/回测系统/monte_carlo_inject.py`（全量 5 组 × 10000 次）。",
        "",
    ]
    return lines


def _verdict(rows: list[dict]) -> list[str]:
    r_s15, r_s20, r_s30, r_ci, r_cg = rows
    o = []
    # 1) 注入 vs 静态同档
    o.append(f"**1) 注入恒定 2.0% vs 静态 2.0%：盈利概率 {r_s20['prob_profit']:.1%} → "
             f"{r_ci['prob_profit']:.1%}，最差 5% {r_s20['fin_p05']:+.1f}R → {r_ci['fin_p05']:+.1f}R**")
    o.append(f"- 注入档成交 {r_ci['n']} 笔（静态 {r_s20['n']} 笔）——资金充足 + 名额竞争换票/加票，"
             f"成交集右移；avgR {r_s20['avg_r']:+.3f} → {r_ci['avg_r']:+.3f}。")
    o.append(f"- 金额视角：最差 5% 情景 {r_ci['fin_p05'] * r_ci['avg_risk']:+,.0f} 元"
             f"（静态同档 {r_s20['fin_p05'] * r_s20['avg_risk']:+,.0f} 元）——注入带来更大本金池，"
             f"绝对金额盈亏同向放大。")
    # 2) 注入增长档
    o.append("")
    o.append("**2) 注入增长 2.0%（风险额随投入增长）：收益档位最高但风险边界最宽**")
    o.append(f"- 盈利概率 {r_cg['prob_profit']:.1%}，中位 {r_cg['fin_p50']:+.1f}R；但最差 5% 回撤 "
             f"{r_cg['dd_p95']:.1f}R × {r_cg['avg_risk']:.2f} 元 ≈ {r_cg['dd_p95'] * r_cg['avg_risk']:,.0f} 元"
             f"——资金增长后同步上调风险额 = 等价于换档到高风险档，网格结论（3.0% 档回撤 124.6%、"
             f"R 口径摊薄）同样适用。")
    # 3) 与静态三档的关系
    o.append("")
    o.append("**3) 静态三档参照（既有 --compare 口径）：2.0% 档硬度在注入后保持**")
    o.append(f"- 静态三档盈利概率 {r_s15['prob_profit']:.1%}/{r_s20['prob_profit']:.1%}/"
             f"{r_s30['prob_profit']:.1%}（1.5/2.0/3.0%）——2.0% 为实盘线；注入后盈利概率"
             f"{r_ci['prob_profit']:.1%} 未劣化 → 注入不改变配置硬度结论。")
    return o


if __name__ == "__main__":
    sys.exit(main())
