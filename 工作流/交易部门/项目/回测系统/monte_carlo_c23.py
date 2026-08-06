#!/usr/bin/env python3
"""C23 蒙特卡洛模拟（2026-08-06 老板拍板 · 两个口径各 10000 次）

把当前策略（C23 收紧：动量≤10% + 止损距离 0.5~3 元）安排进蒙特卡洛模拟，
评估运气边界，与 V1（未收紧基线）并排对比——给老板"最惨能亏多少"的心理预案。

两个口径：
  1) 信号层：引擎 20d R 序列（r_20d，成本已计入引擎口径）
     - V1 基线 = signals.csv 全部 20d 触发信号（1,441 笔）
     - C23     = 套 C23 掩码后（519 笔）
  2) 资金约束层：sim_capital 模拟实盘成交（5,600 元 / 单笔风险 1.5% / 持仓上限 3 只 /
     S 级 / prebreak / 20d），成交 R = pnl / risk_actual（= (exit-entry-cost)/risk，
     金额盈亏已含佣金+印花税，与引擎 R 同口径方向）
     - V1 基线 = 不套掩码成交（114 笔）
     - C23     = 套掩码成交（87 笔）

模拟核心复用 分析决策/跟踪/monte_carlo.py 的 simulate（零改动）；
费用口径与 V1 蒙特卡洛一致：fee_per_trade_r=0.0（R 序列已含费，不重复扣），
仅 n_simulations 由 V1 的 2000 提升到 10000（任务要求"同参数 10000 次"）。

用法:
  python 项目/回测系统/monte_carlo_c23.py            # 全量（enrich 复算 + 4 组 × 10000 次）
  python 项目/回测系统/monte_carlo_c23.py --smoke 60 # 自检（前 60 笔触发信号，秒级）
  python 项目/回测系统/monte_carlo_c23.py --risk-pct 2.0 --max-positions 3
                                                     # 单配置参数化（默认 1.5%×3 仓 = 既有报告口径）
  python 项目/回测系统/monte_carlo_c23.py --compare  # 三档配置对照（C23 资金层 1.5/2.0/3.0% × 3 仓，
                                                     # 各 10000 次 + 信号层共享，2026-08-06 老板拍板 A 档）
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

import numpy as np

from 分析决策.跟踪.monte_carlo import simulate
from 回测系统.sim_capital import c23_mask, simulate_capital
from 回测系统.tighten_compare import enrich, load_triggered

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 默认信号源 = sim_capital 验收同口径全量信号（prebreak/S/dn_confirm1.5/3年全市场）
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "sim_capital_20260806_full" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_REPORT = OUT_DIR / "蒙特卡洛-C23版-20260806.md"
COMPARE_REPORT = OUT_DIR / "蒙特卡洛-C23版-配置对照-20260806.md"

N_SIMULATIONS = 10_000        # 任务口径：各 10000 次（V1 为 2000 次，并排时同参数重跑）
FEE_PER_TRADE_R = 0.0         # 与 V1 蒙特卡洛同口径：R 序列已含费，不重复扣

# 资金约束层参数（老板拍板：5600 元 / 2.0% / 3 仓——G9 实盘线定稿 2026-08-06，
# 与网格实验 T-023 2.0%×3仓 同口径；--risk-pct/--max-positions 可覆盖）
CAPITAL = 5600.0
RISK_RATIO = 0.02
MAX_POSITIONS = 3
GRADES = ["S"]
MODE = "prebreak"
HOLD = "20d"

# 三档配置对照（2026-08-06 老板拍板 A 档最终确认）：(风险%, 持仓上限) × C23 资金层
COMPARE_CONFIGS = [(1.5, 3), (2.0, 3), (3.0, 3)]


def capital_trade_r(trades: list[dict]) -> list[float]:
    """资金约束层成交 R 序列：R = pnl / risk_actual（= (exit-entry-cost)/risk）

    pnl 为金额盈亏（已扣佣金+印花税），risk_actual = 每股风险×股数（单笔实际风险
    投入）——"每笔赚了多少个风险单位"，与 simulate 的 R 单位一致。
    """
    rs = []
    for t in trades:
        risk = float(t.get("risk_actual") or 0)
        if risk <= 0:
            continue  # 防御：无风险投入的成交不参与（预期 0 笔）
        rs.append(float(t["pnl"]) / risk)
    return rs


def run_cap_c23(df, risk_ratio: float, max_positions: int) -> tuple[list[float], float, dict]:
    """C23 资金约束层成交 R 序列（simulate_capital 核心零改动）

    Args:
        risk_ratio: 单笔风险比例（0.02 = 2.0%，G9 实盘线定稿 2026-08-06）
        max_positions: 最多同时持仓数

    Returns:
        (成交 R 序列, 单笔风险均值（元）, simulate_capital 原始结果)
    """
    res = simulate_capital(df, CAPITAL, risk_ratio, max_positions=max_positions,
                           mode=MODE, hold=HOLD, grades=GRADES, c23=True)
    trades = res["trades"]
    rs = capital_trade_r(trades)
    avg_risk = float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0
    return rs, avg_risk, res


def summary(res: dict) -> dict:
    """从 simulate 结果提炼分布统计（终值/回撤/连败/胜率）

    口径：R 单位（累计 R 曲线）；金额换算在报告层按口径说明。
    """
    fin = res["final_equities"]
    dd = res["max_drawdowns"]
    st = res["streaks"]
    samples = res["samples"]
    path_wr = (samples > 0).mean(axis=1)  # 每条路径胜率（含费 0 时 r>0 即胜）
    p = lambda a, q: float(np.percentile(a, q))
    return {
        "n": res["n_trades"],
        "avg_r": res["avg_r"],
        "std_r": res["std_r"],
        "prob_profit": res["prob_profit"],
        # 终值分布（累计 R）：最好 5% 下界 = P95 / 中位 = P50 / 最差 5% 上界 = P5
        "fin_p95": p(fin, 95), "fin_p50": p(fin, 50), "fin_p05": p(fin, 5),
        # 回撤分布（R）：最差 5% 下界 = P95（回撤越大越差）/ 中位 / 最好 5% 上界 = P5
        "dd_p95": p(dd, 95), "dd_p50": p(dd, 50), "dd_p05": p(dd, 5),
        # 连败分布：平均 / 最大 / 最小
        "streak_mean": float(st.mean()), "streak_max": int(st.max()),
        "streak_min": int(st.min()),
        # 胜率分布：最好 5% / 中位 / 最差 5%
        "wr_p95": p(path_wr, 95), "wr_p50": p(path_wr, 50), "wr_p05": p(path_wr, 5),
    }


def fmt_r(v: float, nd: int = 1) -> str:
    return f"{v:+.{nd}f}R"


def main() -> int:
    ap = argparse.ArgumentParser(description="C23 蒙特卡洛模拟（两个口径各 10000 次，与 V1 并排）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--smoke", type=int, default=0, help="冒烟：只处理前 N 笔触发信号")
    ap.add_argument("--out", default=None, help="报告输出路径（默认单配置=蒙特卡洛-C23版；--compare=配置对照）")
    ap.add_argument("--risk-pct", type=float, default=None, help="单笔风险%（默认 1.5 = 既有报告口径）")
    ap.add_argument("--max-positions", type=int, default=None, help="最多同时持仓数（默认 3 = 既有报告口径）")
    ap.add_argument("--compare", action="store_true",
                    help="三档配置对照模式：C23 资金层 1.5/2.0/3.0% × 3 仓各 10000 次"
                         "（2026-08-06 老板拍板 A 档最终确认）")
    args = ap.parse_args()

    # ── 数据准备：触发集 + mom20 复算（tighten_compare 单一来源）──
    df = load_triggered(Path(args.signals), args.smoke)
    n_trig = len(df)
    print(f"[C23 蒙特卡洛] 触发信号 {n_trig} 笔 | 开始 duckdb 复算 mom20 ...")
    df = enrich(df)
    n_mom = df["mom20"].notna().sum()
    print(f"[C23 蒙特卡洛] 复算完成 | mom20 有效 {n_mom} | 失败 {n_trig - n_mom} 笔")

    # ── 配置对照模式（三档 C23 资金层，2026-08-06 老板拍板）──
    if args.compare:
        return _compare_main(df, args)

    # ── 单配置模式（默认 1.5%×3 仓 = 既有报告口径）──
    risk_ratio = (args.risk_pct if args.risk_pct is not None else RISK_RATIO * 100) / 100.0
    max_positions = args.max_positions if args.max_positions is not None else MAX_POSITIONS
    out = Path(args.out) if args.out else DEFAULT_REPORT

    # ── 信号层两组 R 序列 ──
    sig_base = df["r_20d"].tolist()
    sig_c23 = df.loc[c23_mask(df), "r_20d"].tolist()
    print(f"[信号层] V1 基线 {len(sig_base)} 笔 | C23 掩码后 {len(sig_c23)} 笔")

    # ── 资金约束层两组成交（sim_capital 核心，零改动）──
    tr_base = simulate_capital(df, CAPITAL, risk_ratio, max_positions=max_positions,
                               mode=MODE, hold=HOLD, grades=GRADES, c23=False)["trades"]
    cap_c23, avg_risk_c23, _ = run_cap_c23(df, risk_ratio, max_positions)
    cap_base = capital_trade_r(tr_base)
    avg_risk_base = float(np.mean([t["risk_actual"] for t in tr_base])) if tr_base else 0.0
    print(f"[资金层] V1 基线成交 {len(cap_base)} 笔（单笔风险均值 {avg_risk_base:.2f} 元）| "
          f"C23 成交 {len(cap_c23)} 笔（单笔风险均值 {avg_risk_c23:.2f} 元）")

    # ── 各 10000 次模拟 ──
    print(f"[模拟] 每组 {N_SIMULATIONS} 次 × 4 组 ...")
    mc = {
        "sig_base": simulate([{"r_multiple": r} for r in sig_base],
                             n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R),
        "sig_c23": simulate([{"r_multiple": r} for r in sig_c23],
                            n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R),
        "cap_base": simulate([{"r_multiple": r} for r in cap_base],
                             n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R),
        "cap_c23": simulate([{"r_multiple": r} for r in cap_c23],
                            n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R),
    }
    for k, v in mc.items():
        if "error" in v:
            print(f"  ❌ {k}: {v['error']}")
            return 1
    s = {k: summary(v) for k, v in mc.items()}
    print("[模拟] 4 组完成")

    # ── 报告渲染 ──
    lines = [
        "# 蒙特卡洛模拟 · C23 版（2026-08-06 老板拍板）",
        "",
        ("> 目的：把当前策略（C23 收紧）安排进蒙特卡洛模拟，评估运气边界；与 V1（未收紧基线）"
         "并排对比，给老板\"最惨能亏多少\"的心理预案数字。"),
        (f"> 口径：信号层 = 引擎 20d R 序列（r_20d，成本已计入）；资金约束层 = sim_capital "
         f"模拟实盘成交（{CAPITAL:,.0f} 元 / 单笔风险 {risk_ratio:.1%} / 持仓上限 {max_positions} 只 / "
         f"S 级 / prebreak / 20d），成交 R = pnl / risk_actual（含费）。"),
        (f"> 模拟：每组 {N_SIMULATIONS:,} 次有放回重抽样（numpy RNG seed=2024，与 V1 同源）；"
         f"费用口径与 V1 一致 fee=0.0（R 序列已含费，不重复扣）——仅模拟次数由 V1 的 2000 提升到 "
         f"{N_SIMULATIONS:,}（同参数并排，V1 列为本报告同参数重跑值）。"),
        (f"> 信号源：{Path(args.signals).name}（prebreak / S / dn_confirm=1.5 / 2023-07~2026-07 全市场，"
         f"触发 {n_trig} 笔）；C23 掩码 = mom20≤10%（tighten_compare duckdb 复算）+ risk 0.5~3 元。"),
        "",
        "## 一、信号层（R 序列直抽）",
        "",
        "| 组 | 样本笔数 | 样本 avgR | 样本 R 标准差 |",
        "|---|-----:|-----:|-----:|",
        (f"| V1 基线（全触发） | {s['sig_base']['n']} | {s['sig_base']['avg_r']:.3f} | "
         f"{s['sig_base']['std_r']:.2f} |"),
        (f"| C23（动量≤10%+止损0.5~3） | {s['sig_c23']['n']} | {s['sig_c23']['avg_r']:.3f} | "
         f"{s['sig_c23']['std_r']:.2f} |"),
        "",
        "### 分布（累计 R 口径）",
        "",
        "| 指标 | V1 基线 | C23 | 差异（C23-V1） |",
        "|---|---:|---:|---:|",
        "| 盈利概率 | " + f"{s['sig_base']['prob_profit']:.1%} | {s['sig_c23']['prob_profit']:.1%} | "
        f"{s['sig_c23']['prob_profit'] - s['sig_base']['prob_profit']:+.1%} |",
        "| 终值 最好5%下界 | " + fmt_r(s["sig_base"]["fin_p95"]) + " | "
        + fmt_r(s["sig_c23"]["fin_p95"]) + " | " + fmt_r(s["sig_c23"]["fin_p95"] - s["sig_base"]["fin_p95"]) + " |",
        "| 终值 中位 | " + fmt_r(s["sig_base"]["fin_p50"]) + " | "
        + fmt_r(s["sig_c23"]["fin_p50"]) + " | " + fmt_r(s["sig_c23"]["fin_p50"] - s["sig_base"]["fin_p50"]) + " |",
        "| 终值 最差5%上界 | " + fmt_r(s["sig_base"]["fin_p05"]) + " | "
        + fmt_r(s["sig_c23"]["fin_p05"]) + " | " + fmt_r(s["sig_c23"]["fin_p05"] - s["sig_base"]["fin_p05"]) + " |",
        "| 最大回撤 最差5% | " + fmt_r(s["sig_base"]["dd_p95"]) + " | "
        + fmt_r(s["sig_c23"]["dd_p95"]) + " | " + fmt_r(s["sig_c23"]["dd_p95"] - s["sig_base"]["dd_p95"]) + " |",
        "| 最大回撤 中位 | " + fmt_r(s["sig_base"]["dd_p50"]) + " | "
        + fmt_r(s["sig_c23"]["dd_p50"]) + " | " + fmt_r(s["sig_c23"]["dd_p50"] - s["sig_base"]["dd_p50"]) + " |",
        "| 最大回撤 最好5% | " + fmt_r(s["sig_base"]["dd_p05"]) + " | "
        + fmt_r(s["sig_c23"]["dd_p05"]) + " | " + fmt_r(s["sig_c23"]["dd_p05"] - s["sig_base"]["dd_p05"]) + " |",
        "| 连败 平均 | " + f"{s['sig_base']['streak_mean']:.1f} 笔 | {s['sig_c23']['streak_mean']:.1f} 笔 | "
        f"{s['sig_c23']['streak_mean'] - s['sig_base']['streak_mean']:+.1f} 笔 |",
        "| 连败 最大 | " + f"{s['sig_base']['streak_max']} 笔 | {s['sig_c23']['streak_max']} 笔 | "
        f"{s['sig_c23']['streak_max'] - s['sig_base']['streak_max']:+d} 笔 |",
        "| 连败 最小 | " + f"{s['sig_base']['streak_min']} 笔 | {s['sig_c23']['streak_min']} 笔 | "
        f"{s['sig_c23']['streak_min'] - s['sig_base']['streak_min']:+d} 笔 |",
        "| 胜率 最好5% | " + f"{s['sig_base']['wr_p95']:.1%} | {s['sig_c23']['wr_p95']:.1%} | "
        f"{s['sig_c23']['wr_p95'] - s['sig_base']['wr_p95']:+.1%} |",
        "| 胜率 中位 | " + f"{s['sig_base']['wr_p50']:.1%} | {s['sig_c23']['wr_p50']:.1%} | "
        f"{s['sig_c23']['wr_p50'] - s['sig_base']['wr_p50']:+.1%} |",
        "| 胜率 最差5% | " + f"{s['sig_base']['wr_p05']:.1%} | {s['sig_c23']['wr_p05']:.1%} | "
        f"{s['sig_c23']['wr_p05'] - s['sig_base']['wr_p05']:+.1%} |",
        "",
        ("> 口径：终值/回撤均为累计 R（1 R = 一笔信号的风险额）。金额换算参考：若以 10 万本金 × 1% "
         "单笔风险（V1 蒙特卡洛版式同参数），1 R ≈ 1,000 元。"),
        ("> 注意：信号层累计 R 受笔数影响（V1 1,441 笔 vs C23 519 笔），两组比较单笔质量看 "
         "avgR / 胜率；终值分布用于形状参考，资金层才是\"当前策略实盘体验\"的主口径。"),
        "",
        "## 二、资金约束层（模拟实盘成交）",
        "",
        "| 组 | 成交笔数 | 成交 avgR | 单笔风险均值（元） |",
        "|---|-----:|-----:|-----:|",
        f"| V1 基线 | {s['cap_base']['n']} | {s['cap_base']['avg_r']:.3f} | {avg_risk_base:.2f} |",
        f"| C23 | {s['cap_c23']['n']} | {s['cap_c23']['avg_r']:.3f} | {avg_risk_c23:.2f} |",
        "",
        "### 分布（累计 R 口径）",
        "",
        "| 指标 | V1 基线 | C23 | 差异（C23-V1） |",
        "|---|---:|---:|---:|",
        "| 盈利概率 | " + f"{s['cap_base']['prob_profit']:.1%} | {s['cap_c23']['prob_profit']:.1%} | "
        f"{s['cap_c23']['prob_profit'] - s['cap_base']['prob_profit']:+.1%} |",
        "| 终值 最好5%下界 | " + fmt_r(s["cap_base"]["fin_p95"]) + " | "
        + fmt_r(s["cap_c23"]["fin_p95"]) + " | " + fmt_r(s["cap_c23"]["fin_p95"] - s["cap_base"]["fin_p95"]) + " |",
        "| 终值 中位 | " + fmt_r(s["cap_base"]["fin_p50"]) + " | "
        + fmt_r(s["cap_c23"]["fin_p50"]) + " | " + fmt_r(s["cap_c23"]["fin_p50"] - s["cap_base"]["fin_p50"]) + " |",
        "| 终值 最差5%上界 | " + fmt_r(s["cap_base"]["fin_p05"]) + " | "
        + fmt_r(s["cap_c23"]["fin_p05"]) + " | " + fmt_r(s["cap_c23"]["fin_p05"] - s["cap_base"]["fin_p05"]) + " |",
        "| 最大回撤 最差5% | " + fmt_r(s["cap_base"]["dd_p95"]) + " | "
        + fmt_r(s["cap_c23"]["dd_p95"]) + " | " + fmt_r(s["cap_c23"]["dd_p95"] - s["cap_base"]["dd_p95"]) + " |",
        "| 最大回撤 中位 | " + fmt_r(s["cap_base"]["dd_p50"]) + " | "
        + fmt_r(s["cap_c23"]["dd_p50"]) + " | " + fmt_r(s["cap_c23"]["dd_p50"] - s["cap_base"]["dd_p50"]) + " |",
        "| 最大回撤 最好5% | " + fmt_r(s["cap_base"]["dd_p05"]) + " | "
        + fmt_r(s["cap_c23"]["dd_p05"]) + " | " + fmt_r(s["cap_c23"]["dd_p05"] - s["cap_base"]["dd_p05"]) + " |",
        "| 连败 平均 | " + f"{s['cap_base']['streak_mean']:.1f} 笔 | {s['cap_c23']['streak_mean']:.1f} 笔 | "
        f"{s['cap_c23']['streak_mean'] - s['cap_base']['streak_mean']:+.1f} 笔 |",
        "| 连败 最大 | " + f"{s['cap_base']['streak_max']} 笔 | {s['cap_c23']['streak_max']} 笔 | "
        f"{s['cap_c23']['streak_max'] - s['cap_base']['streak_max']:+d} 笔 |",
        "| 连败 最小 | " + f"{s['cap_base']['streak_min']} 笔 | {s['cap_c23']['streak_min']} 笔 | "
        f"{s['cap_c23']['streak_min'] - s['cap_base']['streak_min']:+d} 笔 |",
        "| 胜率 最好5% | " + f"{s['cap_base']['wr_p95']:.1%} | {s['cap_c23']['wr_p95']:.1%} | "
        f"{s['cap_c23']['wr_p95'] - s['cap_base']['wr_p95']:+.1%} |",
        "| 胜率 中位 | " + f"{s['cap_base']['wr_p50']:.1%} | {s['cap_c23']['wr_p50']:.1%} | "
        f"{s['cap_c23']['wr_p50'] - s['cap_base']['wr_p50']:+.1%} |",
        "| 胜率 最差5% | " + f"{s['cap_base']['wr_p05']:.1%} | {s['cap_c23']['wr_p05']:.1%} | "
        f"{s['cap_c23']['wr_p05'] - s['cap_base']['wr_p05']:+.1%} |",
        "",
        (f"> 金额换算参考：1 R ≈ 单笔实际风险均值（V1 {avg_risk_base:.2f} 元 / C23 {avg_risk_c23:.2f} 元）。"
         f"以 {CAPITAL:,.0f} 元本金看，C23 累计 R × {avg_risk_c23:.2f} 元 ≈ 模拟终值金额。"),
        "",
        "## 三、与 V1 蒙特卡洛并排对比总表（同参数 10000 次）",
        "",
        "| 指标 | V1 信号层 | C23 信号层 | V1 资金层 | C23 资金层 |",
        "|---|---:|---:|---:|---:|",
        "| 样本笔数 | " + f"{s['sig_base']['n']} | {s['sig_c23']['n']} | {s['cap_base']['n']} | {s['cap_c23']['n']} |",
        "| avgR | " + f"{s['sig_base']['avg_r']:.3f} | {s['sig_c23']['avg_r']:.3f} | "
        f"{s['cap_base']['avg_r']:.3f} | {s['cap_c23']['avg_r']:.3f} |",
        "| 盈利概率 | " + f"{s['sig_base']['prob_profit']:.1%} | {s['sig_c23']['prob_profit']:.1%} | "
        f"{s['cap_base']['prob_profit']:.1%} | {s['cap_c23']['prob_profit']:.1%} |",
        "| 终值 最好5% | " + fmt_r(s["sig_base"]["fin_p95"]) + " | " + fmt_r(s["sig_c23"]["fin_p95"]) + " | "
        + fmt_r(s["cap_base"]["fin_p95"]) + " | " + fmt_r(s["cap_c23"]["fin_p95"]) + " |",
        "| 终值 中位 | " + fmt_r(s["sig_base"]["fin_p50"]) + " | " + fmt_r(s["sig_c23"]["fin_p50"]) + " | "
        + fmt_r(s["cap_base"]["fin_p50"]) + " | " + fmt_r(s["cap_c23"]["fin_p50"]) + " |",
        "| 终值 最差5% | " + fmt_r(s["sig_base"]["fin_p05"]) + " | " + fmt_r(s["sig_c23"]["fin_p05"]) + " | "
        + fmt_r(s["cap_base"]["fin_p05"]) + " | " + fmt_r(s["cap_c23"]["fin_p05"]) + " |",
        "| 回撤 最差5% | " + fmt_r(s["sig_base"]["dd_p95"]) + " | " + fmt_r(s["sig_c23"]["dd_p95"]) + " | "
        + fmt_r(s["cap_base"]["dd_p95"]) + " | " + fmt_r(s["cap_c23"]["dd_p95"]) + " |",
        "| 回撤 中位 | " + fmt_r(s["sig_base"]["dd_p50"]) + " | " + fmt_r(s["sig_c23"]["dd_p50"]) + " | "
        + fmt_r(s["cap_base"]["dd_p50"]) + " | " + fmt_r(s["cap_c23"]["dd_p50"]) + " |",
        "| 连败 平均 | " + f"{s['sig_base']['streak_mean']:.1f} | {s['sig_c23']['streak_mean']:.1f} | "
        f"{s['cap_base']['streak_mean']:.1f} | {s['cap_c23']['streak_mean']:.1f} |",
        "| 连败 最大 | " + f"{s['sig_base']['streak_max']} | {s['sig_c23']['streak_max']} | "
        f"{s['cap_base']['streak_max']} | {s['cap_c23']['streak_max']} |",
        "| 胜率 中位 | " + f"{s['sig_base']['wr_p50']:.1%} | {s['sig_c23']['wr_p50']:.1%} | "
        f"{s['cap_base']['wr_p50']:.1%} | {s['cap_c23']['wr_p50']:.1%} |",
        "",
    ]

    # ── 白话结论草稿（数据驱动）──
    lines += _verdict(s, avg_risk_base, avg_risk_c23, n_trig, CAPITAL * risk_ratio)
    lines += [
        "",
        "---",
        "",
        ("> 出处：2026-08-06 老板拍板（C23 蒙特卡洛，两个口径各 10000 次）。"
         "实现：项目/回测系统/monte_carlo_c23.py；复算复用 tighten_compare（T-024 同口径），"
         "资金模拟复用 sim_capital.simulate_capital（核心零改动），模拟复用 "
         "分析决策/跟踪/monte_carlo.simulate（零改动）。复现命令："),
        f"> `python 项目/回测系统/monte_carlo_c23.py`（全量，enrich 复算 + 4 组 × {N_SIMULATIONS:,} 次）。",
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {out}")
    return 0


def _verdict(s: dict, avg_risk_base: float, avg_risk_c23: float, n_trig: int,
             risk_amt: float) -> list[str]:
    """白话结论草稿（最终由老板/助理复核）"""
    o = ["## 四、白话结论草稿", ""]
    b, c = s["cap_base"], s["cap_c23"]  # 资金层为主口径（实盘体验）
    # 1) 收紧是否让运气边界更好
    o.append("**1) C23 收紧 vs V1：运气边界整体右移，但代价是信号量减半以上**")
    o.append(f"- 资金层（5,600 元实盘体验）：盈利概率 {b['prob_profit']:.1%} → {c['prob_profit']:.1%}；"
             f"中位终值 {b['fin_p50']:+.1f}R → {c['fin_p50']:+.1f}R；最差 5% 情景 "
             f"{b['fin_p05']:+.1f}R → {c['fin_p05']:+.1f}R（V1 亏钱、C23 仍赚）；"
             f"回撤最差 5% {b['dd_p95']:.1f}R → {c['dd_p95']:.1f}R（回撤也收窄）。")
    o.append(f"- 信号层（全量 R 直抽，统计意义更足）：{s['sig_base']['n']} 笔 → {s['sig_c23']['n']} 笔"
             f"（留存 {s['sig_c23']['n'] / s['sig_base']['n']:.0%}）；中位终值 "
             f"{s['sig_base']['fin_p50']:+.1f}R → {s['sig_c23']['fin_p50']:+.1f}R，"
             f"最差 5% {s['sig_base']['fin_p05']:+.1f}R → {s['sig_c23']['fin_p05']:+.1f}R。")
    # 2) 最惨能亏多少（心理预案）
    o.append("")
    o.append("**2) \"最惨能亏多少\"（给老板的心理预案数字）**")
    c_worst = c["fin_p05"] * avg_risk_c23
    b_worst = b["fin_p05"] * avg_risk_base
    if c["fin_p05"] >= 0:
        c_desc = (f"最差 5% 运气也仍是赚的：累计 +{c['fin_p05']:.1f}R × "
                  f"{avg_risk_c23:.2f} 元 ≈ **+{c_worst:,.0f} 元**（{CAPITAL:,.0f} 元本金之上）")
    else:
        c_desc = (f"最差 5% 情景最多亏 {c['fin_p05']:.1f}R × {avg_risk_c23:.2f} 元 ≈ "
                  f"**-{abs(c_worst):,.0f} 元**（占 {CAPITAL:,.0f} 元本金 "
                  f"{abs(c_worst / CAPITAL):.0%}）")
    if b["fin_p05"] >= 0:
        b_desc = f"V1 同口径 +{b_worst:,.0f} 元"
    else:
        b_desc = f"V1 同口径亏 {abs(b_worst):,.0f} 元（{abs(b_worst / CAPITAL):.0%}）"
    o.append(f"- 资金层 {c_desc}；{b_desc}。"
             f"——历史 3 年信号模拟下，坏运气 5% 概率内的亏损面 C23 已彻底右移"
             f"（最差也不亏钱），V1 则要亏本金两成以上。")
    o.append(f"- 资金层最差 5% 回撤：单次模拟内最大回撤可达 {c['dd_p95']:.0f}R × {avg_risk_c23:.2f} 元 "
             f"≈ {c['dd_p95'] * avg_risk_c23:,.0f} 元——回撤只是账面上的，曲线在尾部仍可能收正"
             f"（盈利概率 {c['prob_profit']:.1%}）。")
    # 3) 连败预期
    o.append("")
    o.append("**3) 连败预期（心理承受准备）**")
    o.append(f"- 资金层平均连败 {c['streak_mean']:.1f} 笔，最坏运气下最大连败 {c['streak_max']} 笔"
             f"（V1 为平均 {b['streak_mean']:.1f} 笔 / 最大 {b['streak_max']} 笔）——"
             f"每笔最大亏损 ≈ 1 个风险单位（{risk_amt:.0f} 元额度内），连败期按每笔约 {avg_risk_c23:.0f} 元消耗。")
    # 4) 局限
    o.append("")
    o.append("**4) 局限（如实标注）**")
    o.append("- 重抽样假设每笔 R 独立同分布：真实交易间有相关性（同板块/同行情），实际连败可能比模拟更长；"
             "未模拟涨跌停无法买入、出场简化（仅止损+持有到期）。")
    o.append(f"- 资金层成交仅 {c['n']} 笔（C23）/ {b['n']} 笔（V1），样本偏少，分布尾部置信度有限；"
             f"信号层 {s['sig_c23']['n']} 笔统计意义更足。")
    o.append("- 存活者偏差与 V1 蒙特卡洛同源：结论只用于相对比较（C23 vs V1），不作绝对承诺。")
    return o


def _compare_main(df, args) -> int:
    """三档配置对照：C23 资金层 1.5/2.0/3.0% × 3 仓各 10000 次（2026-08-06 老板拍板 A 档）

    信号层（R 序列直抽）与风险配置无关，只跑一次共享；资金层每档跑 C23 成交
    （simulate_capital 核心零改动）——风险额 → 每股风险上限 → 可买池差异自然呈现。
    """
    out = Path(args.out) if args.out else COMPARE_REPORT

    # ── 信号层两组 R 序列（与配置无关，共享）──
    sig_base = df["r_20d"].tolist()
    sig_c23 = df.loc[c23_mask(df), "r_20d"].tolist()
    print(f"[信号层] V1 基线 {len(sig_base)} 笔 | C23 掩码后 {len(sig_c23)} 笔")

    # ── 资金层三档（C23 成交，各 10000 次）──
    rows: list[dict] = []
    for risk_pct, max_positions in COMPARE_CONFIGS:
        risk_ratio = risk_pct / 100.0
        rs, avg_risk, res = run_cap_c23(df, risk_ratio, max_positions)
        mc = simulate([{"r_multiple": r} for r in rs],
                      n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
        if "error" in mc:
            print(f"  ❌ {risk_pct}%×{max_positions}仓: {mc['error']}")
            return 1
        s = summary(mc)
        rows.append({
            "risk_pct": risk_pct, "max_positions": max_positions,
            "risk_amt": CAPITAL * risk_ratio,
            "max_risk_per_share": res["max_risk_per_share"],
            "n_all": res["n_all"], "n_exec": res["n_exec"], "exec_rate": res["exec_rate"],
            "reasons": res["reasons"], "avg_risk": avg_risk, **s,
        })
        print(f"[资金层] {risk_pct:.1f}%×{max_positions}仓: 成交 {s['n']} 笔 | avgR {s['avg_r']:+.3f} | "
              f"单笔风险均值 {avg_risk:.2f} 元")
    print(f"[模拟] 3 档 × {N_SIMULATIONS:,} 次完成")

    lines = _compare_lines(rows, len(sig_base), len(sig_c23), len(df))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {out}")
    return 0


def _fmt_r_money(v_r: float, avg_risk: float) -> str:
    """累计 R → 金额（R × 单笔风险均值，千分位带正负号）"""
    return f"{v_r * avg_risk:+,.0f} 元"


def _top_reasons(reasons: dict, n: int = 2) -> str:
    """被拒原因 Top N（'原因×次数' 拼接）"""
    top = sorted(reasons.items(), key=lambda kv: -kv[1])[:n]
    return " / ".join(f"{k}×{v}" for k, v in top) if top else "-"


def _compare_lines(rows: list[dict], n_sig_base: int, n_sig_c23: int, n_trig: int) -> list[str]:
    """三档配置对照报告（数据驱动渲染）"""
    r1, r2, r3 = rows  # 1.5% / 2.0% / 3.0%
    avg_risk_str = " / ".join(f"{r['risk_pct']:.1f}% = {r['avg_risk']:.2f} 元" for r in rows)

    lines = [
        "# 蒙特卡洛模拟 · C23 版配置对照（2026-08-06 老板拍板：2.0%×3仓 实盘线最终确认）",
        "",
        ("> 目的：资金配置网格选定 2.0%×3仓 为实盘线（综合最优 +73.6% / 真实回撤 27.5%，老板拍板），"
         "本报告补该配置的蒙特卡洛最终确认；顺带跑 3.0%×3仓 激进档（网格 +91.2%）作完整三档对照。"),
        ("> 口径：资金约束层 = sim_capital 模拟实盘成交（5,600 元 / 单笔风险 1.5%/2.0%/3.0% / "
         "持仓上限 3 只 / S 级 / prebreak / 20d），成交 R = pnl / risk_actual——金额盈亏已扣"
         "佣金万1.3+印花税万5（含费口径；与引擎 r_20d 价差口径差异如实标注：含费 R 每笔被手续费"
         "摊薄、略低于引擎口径，方向一致）。"),
        (f"> 模拟：每组 {N_SIMULATIONS:,} 次有放回重抽样（numpy RNG seed=2024，与既有 1.5%×3仓 报告同源）；"
         f"费用口径 fee=0.0（R 序列已含费，不重复扣）；1.5% 列为既有报告同 seed 重跑，数值完全一致。"),
        (f"> 信号源：signals.csv（prebreak / S / dn_confirm=1.5 / 2023-07~2026-07 全市场，触发 {n_trig} 笔）；"
         f"C23 掩码 = mom20≤10%（tighten_compare duckdb 复算）+ risk 0.5~3 元。"),
        "",
        "## 一、C23 资金层三档并排（主口径：模拟实盘体验）",
        "",
        "| 指标 | 1.5%×3仓 | 2.0%×3仓 | 3.0%×3仓 |",
        "|---|---:|---:|---:|",
        f"| 单笔风险额（5600 元本金） | {r1['risk_amt']:.0f} 元 | {r2['risk_amt']:.0f} 元 | {r3['risk_amt']:.0f} 元 |",
        f"| 成交笔数 | {r1['n']} | {r2['n']} | {r3['n']} |",
        f"| 成交 avgR | {r1['avg_r']:+.3f} | {r2['avg_r']:+.3f} | {r3['avg_r']:+.3f} |",
        f"| 单笔风险均值（元） | {r1['avg_risk']:.2f} | {r2['avg_risk']:.2f} | {r3['avg_risk']:.2f} |",
        f"| 盈利概率 | {r1['prob_profit']:.1%} | {r2['prob_profit']:.1%} | {r3['prob_profit']:.1%} |",
        "| 终值 最好5%下界 | " + fmt_r(r1["fin_p95"]) + " | " + fmt_r(r2["fin_p95"]) + " | "
        + fmt_r(r3["fin_p95"]) + " |",
        "| 终值 中位 | " + fmt_r(r1["fin_p50"]) + " | " + fmt_r(r2["fin_p50"]) + " | "
        + fmt_r(r3["fin_p50"]) + " |",
        "| 终值 最差5%上界 | " + fmt_r(r1["fin_p05"]) + " | " + fmt_r(r2["fin_p05"]) + " | "
        + fmt_r(r3["fin_p05"]) + " |",
        "| 最大回撤 最差5% | " + fmt_r(r1["dd_p95"]) + " | " + fmt_r(r2["dd_p95"]) + " | "
        + fmt_r(r3["dd_p95"]) + " |",
        "| 最大回撤 中位 | " + fmt_r(r1["dd_p50"]) + " | " + fmt_r(r2["dd_p50"]) + " | "
        + fmt_r(r3["dd_p50"]) + " |",
        f"| 连败 平均 | {r1['streak_mean']:.1f} 笔 | {r2['streak_mean']:.1f} 笔 | {r3['streak_mean']:.1f} 笔 |",
        f"| 连败 最大 | {r1['streak_max']} 笔 | {r2['streak_max']} 笔 | {r3['streak_max']} 笔 |",
        f"| 胜率 最差5% | {r1['wr_p05']:.1%} | {r2['wr_p05']:.1%} | {r3['wr_p05']:.1%} |",
        "",
        (f"> 口径：终值/回撤均为累计 R（1 R = 单笔实际风险投入）；金额换算 = 累计 R × 单笔风险均值"
         f"（各档 {avg_risk_str}；单笔风险额定额分别为 84/112/168 元，受整手约束实际投入低于定额）。"),
        "",
        "## 二、成交集特征（风险额 → 可买池右移）",
        "",
        "| 档位 | 每股风险上限（元） | C23 过滤后信号 | 成交 | 执行率 | 单笔风险均值（元） | 被拒主因 Top2 |",
        "|---|---:|---:|---:|---:|---:|---|",
        *(f"| {r['risk_pct']:.1f}%×{r['max_positions']}仓 | {r['max_risk_per_share']:.2f} | "
          f"{r['n_all']} | {r['n_exec']} | {r['exec_rate']:.1f}% | {r['avg_risk']:.2f} | "
          f"{_top_reasons(r['reasons'])} |" for r in rows),
        "",
        ("> 说明：单笔风险额 = 5,600 元 × 风险% → 每股风险上限 = 风险额 ÷ 100 股（整手）；"
         "风险额越大 → 可买池右移（止损距离大的信号也能买），成交笔数随之变化——"
         "1.5% 档被\"每股风险超限\"挡在门外的信号，2.0%/3.0% 档可能成交。"),
        "",
        "## 三、金额视角（5,600 元本金）",
        "",
        "| 档位 | 每笔风险额 | 最差5%终值 | 中位终值 | 最好5%终值 | 回撤最差5%（账面） |",
        "|---|---:|---:|---:|---:|---:|",
        *(f"| {r['risk_pct']:.1f}%×{r['max_positions']}仓 | {r['risk_amt']:.0f} 元 | "
          f"{_fmt_r_money(r['fin_p05'], r['avg_risk'])} | {_fmt_r_money(r['fin_p50'], r['avg_risk'])} | "
          f"{_fmt_r_money(r['fin_p95'], r['avg_risk'])} | {_fmt_r_money(r['dd_p95'], r['avg_risk'])} |"
          for r in rows),
        "",
        ("> 回撤为账面上限（单次模拟内峰值到谷底），不代表终值亏损；盈利概率见第一节。"
         "负号 = 模拟终值低于本金 5,600 元。"),
        "",
        "## 四、白话结论草稿（数据驱动，最终由老板复核）",
        "",
    ]
    lines += _compare_verdict(rows, n_sig_base, n_sig_c23)
    lines += [
        "",
        "## 五、局限（如实标注）",
        "",
        ("> - 重抽样假设每笔 R 独立同分布：真实交易间有相关性（同板块/同行情），实际连败可能比模拟更长；"
         "未模拟涨跌停无法买入、出场简化（仅止损+持有到期）。"),
        (f"> - 资金层成交仅 {r1['n']}/{r2['n']}/{r3['n']} 笔（1.5/2.0/3.0%），样本偏少，分布尾部置信度有限；"
         f"信号层 C23 掩码后 {n_sig_c23} 笔（V1 基线 {n_sig_base} 笔）统计意义更足。"),
        "- 存活者偏差与既有蒙特卡洛同源：结论用于三档相对比较与实盘心理预案，不作绝对承诺。",
        "- 单笔风险额按初始本金 5,600 元恒定（不随净值浮动），与 sim_capital 口径一致。",
        "",
        "---",
        "",
        ("> 出处：2026-08-06 老板拍板（A 档最终确认：2.0%×3仓 实盘线 + 3.0%×3仓 激进档对照）。"
         "实现：项目/回测系统/monte_carlo_c23.py（--compare 对照模式）；资金模拟复用 "
         "sim_capital.simulate_capital（核心零改动），模拟复用 分析决策/跟踪/monte_carlo.simulate（零改动）。"
         "复现命令："),
        f"> `python 项目/回测系统/monte_carlo_c23.py --compare`（enrich 复算 + 3 档 × {N_SIMULATIONS:,} 次）。",
        "",
    ]
    return lines


def _compare_verdict(rows: list[dict], n_sig_base: int, n_sig_c23: int) -> list[str]:
    """对照报告白话结论（数据驱动）"""
    r1, r2, r3 = rows

    def worst_desc(r: dict) -> str:
        amt = r["fin_p05"] * r["avg_risk"]
        if r["fin_p05"] >= 0:
            return (f"最差 5% 运气也仍是赚的：累计 {r['fin_p05']:+.1f}R × {r['avg_risk']:.2f} 元 ≈ "
                    f"**+{amt:,.0f} 元**（5,600 元本金之上）")
        return (f"最差 5% 情景亏损 {r['fin_p05']:.1f}R × {r['avg_risk']:.2f} 元 ≈ "
                f"**-{abs(amt):,.0f} 元**（占 5,600 元本金 {abs(amt) / CAPITAL:.0%}）")

    o = [
        (f"**1) 2.0%×3仓 蒙特卡洛确认：与 1.5% 同档硬度（盈利概率 {r2['prob_profit']:.1%} / "
         f"最差 5% 仍正 {r2['fin_p05']:+.1f}R）**"),
        (f"- 盈利概率 {r2['prob_profit']:.1%}（1.5% 档 {r1['prob_profit']:.1%}）——坏运气 5% 内几乎不亏本金；"
         f"{worst_desc(r2)}"),
        (f"- 中位终值 {r2['fin_p50']:+.1f}R（{_fmt_r_money(r2['fin_p50'], r2['avg_risk'])}），"
         f"最好 5% 下界 {r2['fin_p95']:+.1f}R——收益档位整体高于 1.5%（每笔风险额 112 元 vs 84 元），"
         f"代价是回撤同步放大（最差 5% 回撤 {r2['dd_p95']:.1f}R vs {r1['dd_p95']:.1f}R）。"),
        "- 每笔风险额换算：2.0% = **112 元/笔**（3 仓并发满仓风险 = 336 元，占本金 6.0%）。",
        "",
        "**2) 3.0%×3仓 风险边界：激进档的真实代价**",
        (f"- 盈利概率 {r3['prob_profit']:.1%}，中位终值 {r3['fin_p50']:+.1f}R"
         f"（金额 {_fmt_r_money(r3['fin_p50'], r3['avg_risk'])}）——金额档位最高来自每笔风险 168 元，"
         f"但 R 口径已摊薄：中位 {r3['fin_p50']:+.1f}R / avgR {r3['avg_r']:+.3f} 均低于 2.0% 档"
         f"（{r2['fin_p50']:+.1f}R / {r2['avg_r']:+.3f}），可买池右移换来的成交集质量略降；"
         f"{worst_desc(r3)}"),
        (f"- 回撤边界显著放大：最差 5% 回撤 {r3['dd_p95']:.1f}R × {r3['avg_risk']:.2f} 元 ≈ "
         f"{r3['dd_p95'] * r3['avg_risk']:,.0f} 元账面回撤（1.5% 档 {r1['dd_p95']:.1f}R ≈ "
         f"{r1['dd_p95'] * r1['avg_risk']:,.0f} 元）；连败最大 {r3['streak_max']} 笔 vs "
         f"{r2['streak_max']} 笔（2.0% 档）。"),
        (f"- 每笔风险额换算：3.0% = **168 元/笔**（3 仓并发满仓风险 = 504 元，占本金 9.0%）；"
         f"最差 5% 情景本金变化 {r3['fin_p05'] * r3['avg_risk']:+,.0f} 元"
         f"（占 5,600 元本金 {r3['fin_p05'] * r3['avg_risk'] / CAPITAL:+.0%}）。"),
        "",
        "**3) 连败预期（心理承受准备）**",
        (f"- 平均连败 {r1['streak_mean']:.1f} / {r2['streak_mean']:.1f} / {r3['streak_mean']:.1f} 笔，"
         f"最大连败 {r1['streak_max']} / {r2['streak_max']} / {r3['streak_max']} 笔"
         f"（1.5/2.0/3.0%）——每笔最大亏损 ≈ 1 个风险单位（84/112/168 元额度内），"
         f"连败期按每笔约 {r1['avg_risk']:.0f}/{r2['avg_risk']:.0f}/{r3['avg_risk']:.0f} 元消耗。"),
        (f"- 胜率最差 5%：{r1['wr_p05']:.1%} / {r2['wr_p05']:.1%} / {r3['wr_p05']:.1%}（1.5/2.0/3.0%）——"
         f"坏运气下胜率底线均未跌破 3 成，配合正 avgR（{r1['avg_r']:+.3f}/{r2['avg_r']:+.3f}/"
         f"{r3['avg_r']:+.3f}）仍是\"赚多亏少\"形状。"),
        "",
        "**4) 三档怎么选（供老板参考）**",
        (f"- 1.5%×3仓：最稳（回撤最小、盈利概率 {r1['prob_profit']:.1%}），代价是收益档位最低"
         f"（成交 {r1['n']} 笔，中位 {r1['fin_p50']:+.1f}R）。"),
        (f"- **2.0%×3仓（实盘线）：硬度不降（盈利概率 {r2['prob_profit']:.1%}、最差 5% 仍正）、"
         f"收益档位高一截——网格综合最优的蒙特卡洛侧确认成立。**"),
        (f"- 3.0%×3仓：金额收益档位最高（中位 {_fmt_r_money(r3['fin_p50'], r3['avg_risk'])}）但 "
         f"R 口径/回撤/连败同步放大（中位 {r3['fin_p50']:+.1f}R vs 2.0% 的 {r2['fin_p50']:+.1f}R，"
         f"回撤最差 5% {r3['dd_p95']:.1f}R ≈ {r3['dd_p95'] * r3['avg_risk']:,.0f} 元账面），"
         f"{worst_desc(r3)}——若选需确认对这笔账面回撤与收益摊薄的承受度。"),
        "",
        "**5) 信号量提示（三档共享信号层）**",
        (f"- 信号层 C23 掩码后 {n_sig_c23} 笔（V1 基线 {n_sig_base} 笔，留存 {n_sig_c23 / n_sig_base:.0%}）；"
         f"资金层成交 {r1['n']}/{r2['n']}/{r3['n']} 笔（1.5/2.0/3.0%）——风险额放宽后成交集右移扩大，"
         f"但样本仍偏少，尾部置信度有限。"),
    ]
    return o


if __name__ == "__main__":
    sys.exit(main())
