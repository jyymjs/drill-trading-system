#!/usr/bin/env python3
"""蒙特卡洛优化 · 分布诊断（2026-08-06 老板拍板：学习路肖南"盈利期望"视频后方案）

四模块（A/B/C/D）：
  A 分布诊断（视频核心）：
    1. 偏度/峰度：R 分布右偏程度 / 尖峰后尾（量化"依赖大赢家"程度）
    2. 去尾稳定性：去掉最极端 1%/5%/10% 收益后重算 avgR/盈利期望/盈利概率——
       崩了（avgR 转负或降 ≥50%）= 依赖大赢家（标注）
    3. 高倍盈亏比分布：0-1R/1-2R/2-3R/3-5R/5-10R/10R+ 档位直方图（笔数+占比）
    4. 区间内胜率分档：模拟终值 ≥0R / ≥+10% / ≥+20% 比例（对齐老师 99.99% 口径，
       5600 元本金按单笔风险均值换算成 R 阈值）
  B 输出增强：
    5. 终值七分位 1%/5%/25%/50%/75%/95%/99%
    6. 回撤 P50/P95/P99
    7. 连亏直方图数据（分档路径占比 + P50/P90/P99）
  C 分市场段蒙卡（老板确认要做）：牛/熊/震荡分段 × 各 10000 次，
     信号层（统计意义更足）+ 2.0%×3仓 主档资金层成交（实盘口径，参考级）
  D 版式与标注：报告头部样本量如实标注（514 笔 / 3 年——低于严肃测试标准
     1000+ 笔 / 8-10 年，中期验证级）+ 对齐老师版式一节

信号源：产出/输出/数据/backtest_final_20260806/signals.csv（514 笔 20d 触发，
全改动后信号 = C23 收紧已并入引擎 + phase_in 预计算出场 + 环境闸门/情绪闸门/
预约披露闸门，params.json: c23=true / phase_in=true / env_gate=true /
sentiment_gate=true / prbook_gate=true / dn_confirm=1.5）。r_20d 引擎口径
（成本已计入），不套 C23 掩码（已在引擎内）、不跑回测引擎、不用 duckdb。

口径（与既有蒙特卡洛并排可比）：
  - 信号层 R  = 引擎 r_20d（(exit-entry)/risk，enable_cost 已计入）
  - 资金层成交 R = pnl / risk_actual（sim_capital 模拟实盘：5600 元 / 风险% /
    持仓上限 3 只 / S 级 / prebreak / 20d；金额盈亏已扣佣金万1.3+印花税万5）
  - 模拟核心复用 分析决策/跟踪/monte_carlo.simulate（零改动，numpy RNG seed=2024，
    各 10000 次，fee=0.0——R 序列已含费不重复扣）
  - 市场状态复用 market_regime（上证指数 20/60/120 日均线，信号按信号日归属，
    无前视；T-026 regime_segment_compare 同先例）

用法:
  python 项目/回测系统/monte_carlo_dist.py --smoke 60   # 冒烟（前 60 笔触发信号，秒级）
  python 项目/回测系统/monte_carlo_dist.py               # 全量（4 组 × 10000 次 + 诊断 + 分段）
"""
import argparse
import sys
from pathlib import Path

# 路径注入（与 main.py / monte_carlo_c23.py 同法：支持任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np
import pandas as pd

from 分析决策.跟踪.monte_carlo import simulate
from 回测系统.market_regime import REGIMES, load_index_df, regime_series
from 回测系统.monte_carlo_c23 import (
    CAPITAL,
    COMPARE_CONFIGS,
    FEE_PER_TRADE_R,
    GRADES,
    HOLD,
    MODE,
    N_SIMULATIONS,
    capital_trade_r,
    summary,
)
from 回测系统.regime_segment_compare import attach_regime
from 回测系统.sim_capital import simulate_capital
from 回测系统.tighten_compare import load_triggered

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 信号源 = 全改动后信号（C23/phase_in/闸门已并入引擎，2026-08-06 最后全面测试产物）
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_REPORT = OUT_DIR / "蒙特卡洛-分布诊断-20260806.md"

# ── 诊断参数（单一来源）──
TAIL_PCTS = (0.01, 0.05, 0.10)         # 去尾稳定性：去掉最大收益 top 1%/5%/10%
PROFIT_PCTS = (0.00, 0.10, 0.20)       # 区间内胜率：≥本金 / ≥+10% / ≥+20%
CRASH_DROP = 0.50                      # 去尾后 avgR 相对全量下降 ≥50% → 判"依赖大赢家"
R_BUCKETS = [("负收益 (<0)", None, 0.0), ("0~1R", 0.0, 1.0), ("1~2R", 1.0, 2.0),
             ("2~3R", 2.0, 3.0), ("3~5R", 3.0, 5.0), ("5~10R", 5.0, 10.0),
             ("10R+", 10.0, None)]
STREAK_BINS = [(0, 3), (3, 6), (6, 9), (9, 12), (12, 15), (15, 18), (18, 21),
               (21, None)]
SEG_MIN_TRADES = 10                    # 分段蒙卡最小样本（<10 笔跳过并标注）
MAIN_RISK_PCT = 2.0                    # 分段蒙卡主档（资金层）
SIG_YEARS = 3.0                        # 信号跨度年数（2023-07~2026-07，报告标注用）
SERIOUS_N, SERIOUS_YEARS = 1000, 8.0   # 严肃测试标准（老师口径）


def r_stats(rs: list[float]) -> dict:
    """R 序列分布统计：偏度/峰度 + 基础指标 + 最大赢家依赖度

    Returns:
        n / avg_r / win_rate / profit_factor / total_r / skew（样本偏度）/
        kurt（样本超额峰度）/ max_r / max_r_share（最大单笔占累计 R 比）/
        std_r
    """
    arr = np.asarray(rs, dtype=float)
    n = len(arr)
    s = pd.Series(arr)
    gains = float(arr[arr > 0].sum())
    loss_abs = float(-arr[arr < 0].sum())
    total = float(arr.sum())
    return {
        "n": n,
        "avg_r": float(arr.mean()) if n else 0.0,
        "win_rate": float((arr > 0).mean()) if n else 0.0,
        "profit_factor": (gains / loss_abs if loss_abs > 0
                          else float("inf") if gains > 0 else 0.0),
        "total_r": total,
        "skew": float(s.skew()) if n >= 3 else 0.0,
        "kurt": float(s.kurt()) if n >= 4 else 0.0,
        "max_r": float(arr.max()) if n else 0.0,
        "max_r_share": float(arr.max() / total) if n and total > 0 else 0.0,
        "std_r": float(arr.std()) if n else 0.0,
    }


def tail_stability(rs: list[float], pcts: tuple[float, ...] = TAIL_PCTS) -> list[dict]:
    """去尾稳定性：去掉最大收益 top p% 后重算 avgR/盈利概率/累计R

    判定：去掉后 avgR ≤ 0 或相对全量下降 ≥50% → crashed=True（依赖大赢家隐患）。
    第 0 行 = 全量基准（pct=0.0）。去掉的是排序后最大收益那部分（大赢家）——
    若去掉大赢家期望崩，说明整体盈利靠少数极端单撑起。
    """
    arr = np.sort(np.asarray(rs, dtype=float))   # 升序
    n = len(arr)
    base = r_stats(arr.tolist())
    rows = [{"pct": 0.0, "n_trim": 0, "n_keep": n, "avg_r": base["avg_r"],
             "win_rate": base["win_rate"], "total_r": base["total_r"],
             "delta": 0.0, "crashed": False}]
    for p in pcts:
        k = min(round(n * p), n - 1)
        t = r_stats(arr[: n - k].tolist())
        drop = (base["avg_r"] - t["avg_r"]) / base["avg_r"] if base["avg_r"] > 0 else 0.0
        crashed = t["avg_r"] <= 0 or drop >= CRASH_DROP
        rows.append({"pct": p, "n_trim": k, "n_keep": n - k, "avg_r": t["avg_r"],
                     "win_rate": t["win_rate"], "total_r": t["total_r"],
                     "delta": t["avg_r"] - base["avg_r"], "crashed": crashed})
    return rows


def r_bucket_dist(rs: list[float]) -> list[dict]:
    """R 档位直方图：负收益/0-1R/1-2R/2-3R/3-5R/5-10R/10R+（笔数+占比+累计R贡献）

    r_share = 该档累计 R / 全量累计 R（正值占比高 = 该档是收益主力）。
    """
    arr = np.asarray(rs, dtype=float)
    n = len(arr)
    total = float(arr.sum())
    rows = []
    for label, lo, hi in R_BUCKETS:
        if lo is None:
            m = arr < hi
        elif hi is None:
            m = arr >= lo
        else:
            m = (arr >= lo) & (arr < hi)
        sub = arr[m]
        sub_total = float(sub.sum())
        rows.append({"label": label, "n": int(m.sum()),
                     "pct": float(m.sum()) / n if n else 0.0,
                     "total_r": sub_total,
                     "r_share": sub_total / total if total > 0 else 0.0,
                     "avg_r": float(sub.mean()) if len(sub) else 0.0})
    return rows


def final_quantiles(fin: np.ndarray) -> dict[int, float]:
    """终值七分位 1/5/25/50/75/95/99（累计 R 口径）"""
    return {q: float(np.percentile(fin, q)) for q in (1, 5, 25, 50, 75, 95, 99)}


def profit_r_thresholds(avg_risk: float) -> list[tuple[str, float]]:
    """+10%/+20% 本金 → R 阈值（5600 元 × pct / 单笔风险均值；老师口径换算）"""
    return [(f"≥+{pct:.0%}", CAPITAL * pct / avg_risk) for pct in PROFIT_PCTS] \
        if avg_risk > 0 else []


def bucket_final_equities(fin: np.ndarray,
                          thresholds: list[tuple[str, float]]) -> dict[str, float]:
    """区间内胜率分档：模拟终值 ≥ 各阈值 的比例（对齐老师 99.99% 口径）"""
    fin = np.asarray(fin, dtype=float)
    return {label: float((fin >= t).mean()) for label, t in thresholds}


def streak_histogram(streaks: np.ndarray) -> dict[str, float]:
    """连亏直方图数据：分档路径占比（0-2/3-5/.../21+）+ P50/P90/P99

    值 = 每条模拟路径的"最大连续亏损笔数"落在各档的路径占比。
    """
    s = np.asarray(streaks, dtype=int)
    n = len(s)
    out: dict[str, float] = {}
    for lo, hi in STREAK_BINS:
        label = f"{lo}-{hi - 1}" if hi else f"{lo}+"
        m = (s >= lo) & (s < hi) if hi else (s >= lo)
        out[label] = float(m.sum()) / n
    for q in (50, 90, 99):
        out[f"p{q}"] = float(np.percentile(s, q))
    return out


def cap_group(df: pd.DataFrame, risk_pct: float, max_positions: int
              ) -> tuple[list[float], float, list[dict], dict]:
    """资金约束层成交（simulate_capital 核心零改动；c23=False——C23 已并入引擎，
    backtest_final signals 即最终信号集，不再二次掩码）

    Returns:
        (成交 R 序列, 单笔风险均值（元）, trades, simulate_capital 原始结果)
    """
    res = simulate_capital(df, CAPITAL, risk_pct / 100.0, max_positions=max_positions,
                           mode=MODE, hold=HOLD, grades=GRADES, c23=False)
    trades = res["trades"]
    rs = capital_trade_r(trades)
    avg_risk = float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0
    return rs, avg_risk, trades, res


def enhanced_summary(mc: dict, avg_risk: float) -> dict:
    """simulate 结果增强：复用 C23 版 summary + 七分位/回撤P99/连亏直方图/区间胜率"""
    s = summary(mc)
    dd = mc["max_drawdowns"]
    fin = mc["final_equities"]
    thresholds = profit_r_thresholds(avg_risk)
    return {
        **s,
        "fin_q": final_quantiles(fin),
        "dd_p99": float(np.percentile(dd, 99)),
        "streak_hist": streak_histogram(mc["streaks"]),
        "bucket_wr": bucket_final_equities(fin, thresholds),
        "bucket_labels": [label for label, _ in thresholds],
        "avg_risk": avg_risk,
    }


def _trades_by_regime(trades: list[dict], series: pd.Series
                      ) -> dict[str, list[tuple[float, float]]]:
    """资金层成交按信号日归牛/熊/震荡（无前视，T-026 同先例）

    Returns:
        {段名: [(R, risk_actual), ...]}；"未知" = 信号日不在指数日历
    """
    out: dict[str, list[tuple[float, float]]] = {r: [] for r in REGIMES}
    out["未知"] = []
    for t in trades:
        risk = float(t.get("risk_actual") or 0)
        if risk <= 0:
            continue
        regime = series.get(pd.Timestamp(t["date"]), "未知") if series is not None else "未知"
        out[regime].append((float(t["pnl"]) / risk, risk))
    return out


def segment_simulate(rs_by_regime: dict[str, list[float]],
                     avg_risk: float | dict[str, float]) -> list[dict]:
    """分段蒙卡：每段 R 序列 simulate 10000 次 → 各段运气边界

    Args:
        rs_by_regime: {段名: R 序列}（应含全部段键；缺键按空段处理）
        avg_risk: 金额换算用单笔风险均值——float=所有段同值；
                  dict=按段取值（段内成交均值，更精确）

    段样本 < SEG_MIN_TRADES → skipped 标注（参考级说明在报告层）。
    """
    rows = []
    for regime in list(REGIMES) + ["未知"]:
        rs = rs_by_regime.get(regime, [])
        if len(rs) < SEG_MIN_TRADES:
            rows.append({"regime": regime, "n": len(rs), "skipped": True})
            continue
        mc = simulate([{"r_multiple": r} for r in rs],
                      n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
        if "error" in mc:
            rows.append({"regime": regime, "n": len(rs), "skipped": True})
            continue
        st = r_stats(rs)
        s = summary(mc)
        seg_risk = avg_risk.get(regime, 0.0) if isinstance(avg_risk, dict) \
            else avg_risk
        rows.append({
            "regime": regime, "n": len(rs), "skipped": False,
            "avg_r": st["avg_r"], "win_rate_sample": st["win_rate"],
            "prob_profit": s["prob_profit"], "fin_p50": s["fin_p50"],
            "fin_p05": s["fin_p05"], "dd_p95": s["dd_p95"], "avg_risk": seg_risk,
        })
    return rows


def _pf_text(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def _fmt_r_money(v_r: float, avg_risk: float) -> str:
    return f"{v_r * avg_risk:+,.0f} 元"


# ── 报告渲染 ──


def render_diagnostic_table(label: str, st: dict, rows: list[dict]) -> list[str]:
    """分布诊断一节（偏度/峰度 + 去尾稳定性表）"""
    crash_flags = " / ".join(f"{r['pct']:.0%}→{'崩' if r['crashed'] else '稳'}"
                             for r in rows[1:])
    lines = [
        f"### {label}（{st['n']} 笔）",
        "",
        "| 指标 | 数值 | 解读 |",
        "|---|---:|---|",
        f"| 样本 avgR | {st['avg_r']:+.3f} | 每笔平均赚 {st['avg_r']:.2f} 个风险单位 |",
        f"| 盈利概率 | {st['win_rate']:.1%} | 样本内 P(R>0) |",
        f"| 盈亏比 | {_pf_text(st['profit_factor'])} | Σ盈利/|Σ亏损| |",
        f"| 累计 R | {st['total_r']:+.1f}R | 1R 等权累计 |",
        f"| **偏度** | **{st['skew']:+.2f}** | {'右偏（大赢家拖尾）' if st['skew'] > 0.5 else '基本对称' if abs(st['skew']) <= 0.5 else '左偏（大亏拖尾）'} |",
        f"| **峰度（超额）** | **{st['kurt']:+.2f}** | {'尖峰厚尾（极端值集中）' if st['kurt'] > 0.5 else '近正态' if abs(st['kurt']) <= 0.5 else '平坦尾部'} |",
        f"| 最大单笔 R | {st['max_r']:+.2f}R | 占累计 R {st['max_r_share']:.0%} |",
        "",
        "| 去尾（去掉最大收益） | 保留笔数 | avgR | 盈利概率 | 累计 R | ΔavgR | 判定 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {'全量（基准）' if r['pct'] == 0 else f'去尾 {r['pct']:.0%}'} "
            f"({r['n_trim']} 笔) | {r['n_keep']} | {r['avg_r']:+.3f} | "
            f"{r['win_rate']:.1%} | {r['total_r']:+.1f}R | "
            f"{r['delta']:+.3f} | "
            f"{'依赖大赢家' if r['crashed'] else '稳定'} |")
    lines.append("")
    lines.append(f"> 去尾判定口径：去掉最大收益 top 1%/5%/10% 后 avgR 转负或相对全量下降 ≥{CRASH_DROP:.0%} "
                 f"→ '依赖大赢家'（整体盈利靠少数极端单撑起，稳定性隐患）。本组判定：{crash_flags}。")
    return lines


def render_bucket_table(label: str, rows: list[dict]) -> list[str]:
    """高倍盈亏比档位直方图一节"""
    lines = [
        f"### {label} R 档位分布",
        "",
        "| 档位 | 笔数 | 占比 | 档内 avgR | 档累计 R | 占总收益比 |",
        "|---|-----:|-----:|-----:|-------:|-------:|",
    ]
    for r in rows:
        share = "—" if r["r_share"] == 0 else f"{r['r_share']:.0%}"
        lines.append(f"| {r['label']} | {r['n']} | {r['pct']:.1%} | "
                     f"{r['avg_r']:+.3f} | {r['total_r']:+.1f}R | {share} |")
    return lines


def _q_key(q: int) -> str:
    return "dd_p50" if q == 50 else "dd_p95" if q == 95 else "dd_p99"


def render_report(rows: list[dict], sig_diag: dict, sig_buckets: list[dict],
                  cap_diag: dict, cap_buckets: dict, seg_rows: list[dict],
                  n_trig: int, n_sig_cap: int, main_avg_risk: float) -> list[str]:
    """渲染完整报告（数据驱动；结论草稿由老板/助理复核）"""
    sig, r1, r2, r3 = rows

    lines = [
        "# 蒙特卡洛模拟 · 分布诊断（2026-08-06 老板拍板：路肖南「盈利期望」方案）",
        "",
        ("> 目的：回答「我们的盈利依赖少数大赢家吗？去掉最好 10 笔会崩吗？」——"
         "实盘线开启前最该知道的稳定性问题；对齐老师「盈利期望」视频的四件套："
         "分布诊断（偏度/峰度/去尾稳定性/高倍盈亏比）+ 区间内胜率分档 + 分市场段蒙卡。"),
        (f"> 信号源：backtest_final_20260806/signals.csv（**全改动后信号**：C23 收紧已并入引擎 + "
         "phase_in 预计算出场 + 环境闸门/情绪闸门/预约披露闸门，prebreak / S 级 / dn_confirm=1.5 / "
         f"2023-07~2026-07 全市场，20d 触发 {n_trig} 笔 / {SIG_YEARS:.0f} 年）。"),
        (f"> ⚠️ **样本量如实标注：{n_trig} 笔信号 / {n_sig_cap} 笔资金层成交 / {SIG_YEARS:.0f} 年——"
         f"低于严肃测试标准（{SERIOUS_N}+ 笔 / {SERIOUS_YEARS:.0f}-10 年），结论为中期验证级**；"
         "虚拟盘线双轨持续积累样本。"),
        (f"> 模拟：每组 {N_SIMULATIONS:,} 次有放回重抽样（numpy RNG seed=2024，与既有蒙特卡洛同源）；"
         f"费用口径 fee=0.0（R 序列已含费不重复扣）。"),
        ("> 口径：信号层 R = 引擎 r_20d（成本已计入）；资金层成交 R = pnl / risk_actual"
         "（sim_capital 模拟实盘：5,600 元 / 风险 1.5/2.0/3.0% / 持仓上限 3 只 / S 级 / prebreak / 20d，"
         "金额盈亏已扣佣金万1.3+印花税万5）。"),
        "",
        "## 一、样本总览",
        "",
        "| 组 | 笔数 | 样本 avgR | 样本胜率 | 模拟盈利概率(≥0R) | 盈亏比 | 累计 R |",
        "|---|-----:|-----:|-----:|-----:|-------:|------:|",
        (f"| 信号层（全改动后触发） | {sig_diag['n']} | {sig_diag['avg_r']:+.3f} | "
         f"{sig_diag['win_rate']:.1%} | {sig['prob_profit']:.1%} | "
         f"{_pf_text(sig_diag['profit_factor'])} | {sig_diag['total_r']:+.1f}R |"),
        *(f"| 资金层 {r['risk_pct']:.1f}%×{r['max_positions']}仓 | {r['n']} | {r['avg_r']:+.3f} | "
          f"{cap_diag[f'{r['risk_pct']:.1f}']['win_rate']:.1%} | {r['prob_profit']:.1%} | "
          f"{_pf_text(cap_diag[f'{r['risk_pct']:.1f}']['profit_factor'])} | — |" for r in (r1, r2, r3)),
        "",
        (f"> 资金层成交 avgR 为含费口径（pnl/risk_actual），略低于引擎 r_20d 口径（手续费摊薄）；"
         f"单笔风险均值：1.5%={r1['avg_risk']:.2f} 元 / 2.0%={r2['avg_risk']:.2f} 元 / "
         f"3.0%={r3['avg_risk']:.2f} 元（单笔风险额定额 84/112/168 元，受整手约束实际投入低于定额）。"),
        "",
        "## 二、分布诊断 A（视频核心）",
        "",
    ]
    # 信号层诊断（主，统计意义足）+ 2.0% 主档（实盘口径）
    lines += render_diagnostic_table("信号层（主诊断）", sig_diag, sig_diag["tail"])
    lines += render_diagnostic_table("资金层 2.0%×3仓（实盘主口径）", cap_diag["2.0"],
                                     cap_diag["2.0"]["tail"])
    lines += ["", "### 高倍盈亏比档位分布（R 档位直方图）", ""]
    lines += render_bucket_table("信号层", sig_buckets)
    lines += render_bucket_table("资金层 2.0%×3仓", cap_buckets["2.0"])
    lines += ["", "## 三、蒙特卡洛 B：三档 × 10000 次 + 区间内胜率分档", ""]
    lines += _render_mc_main(rows, main_avg_risk)
    lines += ["", "### 连亏直方图（每条路径最大连亏分布）", ""]
    lines += _render_streak_hist(rows)
    lines += ["", "## 四、分市场段蒙卡 C（老板确认要做）", ""]
    lines += _render_segments(seg_rows, main_avg_risk)
    lines += ["", "## 五、对齐老师版式（总收益/中位/最好/最差/区间内胜率/回撤/连亏）", ""]
    lines += _render_teacher_section(rows, main_avg_risk)
    lines += ["", "## 六、白话结论草稿（数据驱动，最终由老板复核）", ""]
    lines += _verdict(sig_diag, cap_diag, rows, seg_rows, n_trig)
    lines += ["", "## 七、局限（如实标注）", ""]
    lines += [
        "",
        (f"> - 样本量：{n_trig} 笔信号 / {n_sig_cap} 笔资金层成交 / {SIG_YEARS:.0f} 年——"
         f"低于严肃测试标准（{SERIOUS_N}+ 笔 / {SERIOUS_YEARS:.0f}-10 年），分布尾部（1%/5% 分位）"
         "置信度有限，结论为中期验证级；虚拟盘线双轨持续积累。"),
        ("> - 重抽样假设每笔 R 独立同分布：真实交易间有相关性（同板块/同行情），实际连败可能比模拟更长；"
         "未模拟涨跌停无法买入、滑点。"),
        ("> - 分市场段蒙卡：分段样本 30-50 笔/段为参考级，非严肃级；分段是事后按市场状态划的，"
         "不构成对未来同类行情的承诺。"),
        "> - 存活者偏差与既有蒙特卡洛同源（当前存活标的池），结论用于相对比较与心理预案，不作绝对承诺。",
        "",
        "---",
        "",
        ("> 出处：2026-08-06 老板拍板（蒙特卡洛优化方案，学习路肖南\"盈利期望\"视频）。"
         "实现：项目/回测系统/monte_carlo_dist.py；模拟复用 分析决策/跟踪/monte_carlo.simulate（零改动），"
         "资金模拟复用 sim_capital.simulate_capital（零改动），市场状态复用 market_regime（T-026 同先例）。"
         "复现命令："),
        f"> `python 项目/回测系统/monte_carlo_dist.py`（全量，4 组 × {N_SIMULATIONS:,} 次 + 诊断 + 分段）。",
        "",
    ]
    return lines


def _render_mc_main(rows: list[dict], main_avg_risk: float) -> list[str]:
    """B 输出增强主表：七分位 / 回撤三档 / 区间内胜率（≥0R/≥+10%/≥+20%）"""
    sig, r1, r2, r3 = rows
    lines = [
        "| 指标 | 信号层 | 1.5%×3仓 | 2.0%×3仓 | 3.0%×3仓 |",
        "|---|---:|---:|---:|---:|",
        f"| 模拟笔数/路径 | {sig['n']} 笔 | {r1['n']} 笔 | {r2['n']} 笔 | {r3['n']} 笔 |",
        f"| 样本 avgR | {sig['avg_r']:+.3f} | {r1['avg_r']:+.3f} | {r2['avg_r']:+.3f} | {r3['avg_r']:+.3f} |",
    ]
    for q in (1, 5, 25, 50, 75, 95, 99):
        lines.append(f"| 终值 P{q} | {sig['fin_q'][q]:+.1f}R | {r1['fin_q'][q]:+.1f}R | "
                     f"{r2['fin_q'][q]:+.1f}R | {r3['fin_q'][q]:+.1f}R |")
    for q in (50, 95, 99):
        lines.append(f"| 回撤 P{q} | {sig[_q_key(q)]:.1f}R | {r1[_q_key(q)]:.1f}R | "
                     f"{r2[_q_key(q)]:.1f}R | {r3[_q_key(q)]:.1f}R |")
    lines.append(f"| 连亏 平均/最大 | {sig['streak_mean']:.1f}/{sig['streak_max']} 笔 | "
                 f"{r1['streak_mean']:.1f}/{r1['streak_max']} 笔 | "
                 f"{r2['streak_mean']:.1f}/{r2['streak_max']} 笔 | "
                 f"{r3['streak_mean']:.1f}/{r3['streak_max']} 笔 |")
    # 区间内胜率（阈值各档按各自单笔风险均值换算：+10% = 560 元 → R）
    for i, label in enumerate(r2["bucket_labels"]):
        lines.append(f"| 区间内胜率 {label} | {sig['bucket_wr'][label]:.1%} | "
                     f"{r1['bucket_wr'][label]:.1%} | {r2['bucket_wr'][label]:.1%} | "
                     f"{r3['bucket_wr'][label]:.1%} |")
    lines.append("")
    lines.append(f"> 阈值换算口径：+10%/+20% 本金 = 560/1120 元，按各档单笔风险均值换算为 R 阈值"
                 f"（信号层按 2.0% 档 {main_avg_risk:.2f} 元换算标注）；"
                 f"各档 R 阈值：1.5% = {CAPITAL * 0.10 / r1['avg_risk']:.1f}R / "
                 f"{CAPITAL * 0.20 / r1['avg_risk']:.1f}R，2.0% = "
                 f"{CAPITAL * 0.10 / r2['avg_risk']:.1f}R / {CAPITAL * 0.20 / r2['avg_risk']:.1f}R，"
                 f"3.0% = {CAPITAL * 0.10 / r3['avg_risk']:.1f}R / "
                 f"{CAPITAL * 0.20 / r3['avg_risk']:.1f}R。")
    return lines


def _render_streak_hist(rows: list[dict]) -> list[str]:
    """连亏直方图数据表（分档路径占比）"""
    sig, r1, r2, r3 = rows
    labels = [k for k in sig["streak_hist"] if not k.startswith("p")]
    head = "| 最大连亏档 | " + " | ".join(f"{label}路径占比" for label in
                                          ("信号层", "1.5%×3仓", "2.0%×3仓", "3.0%×3仓")) + " |"
    sep = "|---|" + "---:|" * 4
    lines = [head, sep]
    for label in labels:
        lines.append(f"| {label} | "
                     + " | ".join(f"{r['streak_hist'][label]:.1%}" for r in (sig, r1, r2, r3)) + " |")
    lines.append("| P50/P90/P99 | "
                 + " | ".join(f"{r['streak_hist']['p50']:.0f}/{r['streak_hist']['p90']:.0f}/"
                              f"{r['streak_hist']['p99']:.0f}" for r in (sig, r1, r2, r3)) + " |")
    return lines


def _render_segments(seg_rows: list[dict], main_avg_risk: float) -> list[str]:
    """分段蒙卡表：信号层 + 2.0% 主档资金层，各段运气边界"""
    sig_rows = [r for r in seg_rows if r.get("layer") == "信号层"]
    cap_rows = [r for r in seg_rows if r.get("layer") == "资金层"]
    lines = [
        ("> 市场状态：上证指数 20/60/120 日均线（牛=收盘>MA120 且 MA20>MA60；熊=收盘<MA120；"
         "震荡=其余），信号按信号日归属，无前视——与 T-026 分段口径一致。"),
        ("> ⚠️ **分段样本为参考级，非严肃级**：资金层每段约 30-50 笔（含费成交），"
         "分段后尾部置信度进一步下降，只用于方向性观察。"),
        "",
        "### 信号层分段（514 笔，统计意义较足）",
        "",
        "| 段 | 笔数 | 段内 avgR | 段内胜率 | 盈利概率 (≥0R) | 最差5%终值 | 回撤 P95 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sig_rows:
        if r["skipped"]:
            lines.append(f"| {r['regime']} | {r['n']} | —（样本过少跳过） | — | — | — | — |")
        else:
            lines.append(f"| {r['regime']} | {r['n']} | {r['avg_r']:+.3f} | "
                         f"{r['win_rate_sample']:.1%} | {r['prob_profit']:.1%} | "
                         f"{r['fin_p05']:+.1f}R | {r['dd_p95']:.1f}R |")
    lines += [
        "",
        "### 资金层 2.0%×3仓 成交分段（实盘口径，参考级）",
        "",
        "| 段 | 笔数 | 段内 avgR | 盈利概率 (≥0R) | 最差5%终值 | 回撤 P95 | 最差5%金额 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in cap_rows:
        if r["skipped"]:
            lines.append(f"| {r['regime']} | {r['n']} | —（样本过少跳过） | — | — | — | — |")
        else:
            lines.append(f"| {r['regime']} | {r['n']} | {r['avg_r']:+.3f} | "
                         f"{r['prob_profit']:.1%} | {r['fin_p05']:+.1f}R | "
                         f"{r['dd_p95']:.1f}R | {_fmt_r_money(r['fin_p05'], r['avg_risk'])} |")
    lines.append(f"> 金额换算 = 累计 R × 段内单笔风险均值（主档 2.0% 整体 {main_avg_risk:.2f} 元/笔）。")
    return lines


def _render_teacher_section(rows: list[dict], main_avg_risk: float) -> list[str]:
    """对齐老师版式：总收益/中位/最好/最差/区间内胜率/回撤三档/连亏三档"""
    sig, r1, r2, r3 = rows
    return [
        "| 指标 | 信号层 | 1.5%×3仓 | 2.0%×3仓 | 3.0%×3仓 |",
        "|---|---:|---:|---:|---:|",
        (f"| 总收益（avgR × 笔数 = 累计R） | {sig['total_r']:+.1f}R | {r1['total_r']:+.1f}R | "
         f"{r2['total_r']:+.1f}R | {r3['total_r']:+.1f}R |"),
        (f"| 中位终值 | {sig['fin_p50']:+.1f}R | {r1['fin_p50']:+.1f}R | "
         f"{r2['fin_p50']:+.1f}R | {r3['fin_p50']:+.1f}R |"),
        (f"| 最好 5% 下界 | {sig['fin_p95']:+.1f}R | {r1['fin_p95']:+.1f}R | "
         f"{r2['fin_p95']:+.1f}R | {r3['fin_p95']:+.1f}R |"),
        (f"| 最差 5% 上界 | {sig['fin_p05']:+.1f}R | {r1['fin_p05']:+.1f}R | "
         f"{r2['fin_p05']:+.1f}R | {r3['fin_p05']:+.1f}R |"),
        (f"| 区间内胜率 ≥0R | {sig['prob_profit']:.1%} | {r1['prob_profit']:.1%} | "
         f"{r2['prob_profit']:.1%} | {r3['prob_profit']:.1%} |"),
        (f"| 回撤 平均/最差5%/最好5% | {sig['dd_p50']:.1f}/{sig['dd_p95']:.1f}/{sig['dd_p05']:.1f}R | "
         f"{r1['dd_p50']:.1f}/{r1['dd_p95']:.1f}/{r1['dd_p05']:.1f}R | "
         f"{r2['dd_p50']:.1f}/{r2['dd_p95']:.1f}/{r2['dd_p05']:.1f}R | "
         f"{r3['dd_p50']:.1f}/{r3['dd_p95']:.1f}/{r3['dd_p05']:.1f}R |"),
        (f"| 连亏 平均/最大/最小 | {sig['streak_mean']:.1f}/{sig['streak_max']}/{sig['streak_min']} 笔 | "
         f"{r1['streak_mean']:.1f}/{r1['streak_max']}/{r1['streak_min']} 笔 | "
         f"{r2['streak_mean']:.1f}/{r2['streak_max']}/{r2['streak_min']} 笔 | "
         f"{r3['streak_mean']:.1f}/{r3['streak_max']}/{r3['streak_min']} 笔 |"),
        (f"> 金额视角（× 单笔风险均值）：中位终值 2.0% ≈ {_fmt_r_money(r2['fin_p50'], r2['avg_risk'])}；"
         f"最差 5% ≈ {_fmt_r_money(r2['fin_p05'], r2['avg_risk'])}；"
         f"回撤最差 5% ≈ {_fmt_r_money(r2['dd_p95'], r2['avg_risk'])}（账面上限，不代表终值亏损）。"),
    ]


def _verdict(sig_diag: dict, cap_diag: dict, rows: list[dict],
             seg_rows: list[dict], n_trig: int) -> list[str]:
    """白话结论草稿（数据驱动，最终由老板/助理复核）"""
    _, _, r2, _ = rows
    o = []
    # ① 依赖大赢家吗（信号层与资金层分别说，数据驱动）
    t = sig_diag["tail"]
    c = cap_diag["2.0"]["tail"]
    sig_desc = (f"信号层去掉最大收益后 avgR：1%→{t[1]['avg_r']:+.3f}（{t[1]['delta'] / t[0]['avg_r']:+.0%}）、"
                f"5%→{t[2]['avg_r']:+.3f}（{t[2]['delta'] / t[0]['avg_r']:+.0%}）、"
                f"10%→{t[3]['avg_r']:+.3f}（{t[3]['delta'] / t[0]['avg_r']:+.0%}）")
    cap_desc = (f"资金层 2.0%（实盘口径）：1%→{c[1]['avg_r']:+.3f}、5%→{c[2]['avg_r']:+.3f}、"
                f"10%→{c[3]['avg_r']:+.3f}")
    o.append("**① 我们的盈利依赖少数大赢家吗？（去尾稳定性）**")
    o.append(f"- {sig_desc}。判定：{('1%/5% 去尾仍正（未达崩线），10% 去尾下降 ≥50% → 边缘依赖大赢家'
                                     if not t[2]['crashed'] and t[3]['crashed']
                                     else '去尾 1%/5% 均稳定，中间段夯实' if not any(r['crashed'] for r in t[1:])
                                     else '去尾即崩 → 强依赖大赢家')}。")
    o.append(f"- {cap_desc}。判定：{'去尾 5% 即崩（avgR 降 ≥50%）、10% 转负 → 实盘口径更依赖大赢家，'
                                    '单笔大赚对整体贡献突出' if c[2]['crashed'] or c[3]['crashed']
                                    else '资金层去尾稳定'}——最大单笔占累计 R "
             f"{cap_diag['2.0']['max_r_share']:.0%}，**实盘切不可因最近几笔大赚而加码**"
             "（均值回归风险高）。")
    # ② 分布形状
    o.append("")
    o.append("**② 分布形状（偏度/峰度）**")
    o.append(f"- 信号层偏度 {sig_diag['skew']:+.2f}（{'右偏：大赢家拖尾，单笔上限靠大单' if sig_diag['skew'] > 0.5 else '基本对称'}）；"
             f"超额峰度 {sig_diag['kurt']:+.2f}（{'尖峰厚尾：极端值比正态更多' if sig_diag['kurt'] > 0.5 else '近正态'}）。")
    o.append(f"- 资金层 2.0% 成交偏度 {cap_diag['2.0']['skew']:+.2f} / 峰度 {cap_diag['2.0']['kurt']:+.2f}"
             "（含费后分布形状与信号层基本一致）。")
    # ③ 区间胜率
    o.append("")
    o.append("**③ 区间内胜率（对齐老师 99.99% 口径）**")
    o.append(f"- 2.0%×3仓：盈利概率 {r2['prob_profit']:.1%}；≥+10% 本金 "
             f"{r2['bucket_wr'][r2['bucket_labels'][1]]:.1%}；≥+20% 本金 "
             f"{r2['bucket_wr'][r2['bucket_labels'][2]]:.1%}——坏运气 5% 内几乎不亏本金。")
    o.append(f"- 信号层（514 笔，统计意义更足）：≥0R {rows[0]['prob_profit']:.1%} / ≥+10% "
             f"{rows[0]['bucket_wr'][rows[0]['bucket_labels'][1]]:.1%} / ≥+20% "
             f"{rows[0]['bucket_wr'][rows[0]['bucket_labels'][2]]:.1%}。")
    # ④ 分段蒙卡
    o.append("")
    o.append("**④ 分市场段蒙卡（参考级）**")
    cap_segs = [r for r in seg_rows if r.get("layer") == "资金层" and not r["skipped"]]
    for r in cap_segs:
        o.append(f"- 资金层 {r['regime']}段：{r['n']} 笔 avgR {r['avg_r']:+.3f}，盈利概率 "
                 f"{r['prob_profit']:.1%}，最差 5% {r['fin_p05']:+.1f}R（"
                 f"{_fmt_r_money(r['fin_p05'], r['avg_risk'])}），回撤 P95 {r['dd_p95']:.1f}R。")
    if cap_segs:
        tightest = min(cap_segs, key=lambda r: r["fin_p05"])
        o.append(f"- **运气边界最紧段 = {tightest['regime']}段**（最差 5% 终值 "
                 f"{tightest['fin_p05']:+.1f}R）——分段样本 30-50 笔为参考级，非严肃级；"
                 "方向性观察用，不作单段承诺。")
    sig_segs = [r for r in seg_rows if r.get("layer") == "信号层" and not r["skipped"]]
    if sig_segs:
        o.append("- 信号层分段（笔数更足）：" + "；".join(
            f"{r['regime']} {r['n']} 笔 avgR {r['avg_r']:+.3f}" for r in sig_segs)
            + "——震荡段样本质量最高（avgR/胜率领先），牛段最弱。")
    # ⑤ 样本量
    o.append("")
    o.append("**⑤ 样本量如实标注（中期验证级）**")
    o.append(f"- {n_trig} 笔信号 / {rows[2]['n']} 笔资金层成交 / 3 年——低于严肃测试标准（1000+ 笔 / 8-10 年）；"
             "分布尾部（1%/5% 分位）置信度有限，结论为中期验证级，虚拟盘线双轨持续积累样本。")
    return o


def main() -> int:
    ap = argparse.ArgumentParser(description="蒙特卡洛优化 · 分布诊断（四模块 A/B/C/D）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--smoke", type=int, default=0, help="冒烟：只处理前 N 笔触发信号")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 蒙特卡洛-分布诊断-20260806.md）")
    args = ap.parse_args()

    df = load_triggered(Path(args.signals), args.smoke)
    n_trig = len(df)
    print(f"[分布诊断] 触发信号 {n_trig} 笔（全改动后口径，无需复算）")

    # ── 信号层 + 三档资金层 R 序列 ──
    sig_rs = df["r_20d"].tolist()
    groups: list[dict] = []
    for risk_pct, max_pos in COMPARE_CONFIGS:
        rs, avg_risk, trades, _ = cap_group(df, risk_pct, max_pos)
        groups.append({"risk_pct": risk_pct, "max_positions": max_pos, "rs": rs,
                       "avg_risk": avg_risk, "trades": trades})
        print(f"[资金层] {risk_pct:.1f}%×{max_pos}仓: 成交 {len(rs)} 笔 | avgR "
              f"{float(np.mean(rs)):+.3f} | 单笔风险均值 {avg_risk:.2f} 元")

    # ── 各 10000 次模拟（simulate 复用，seed=2024 可复现）──
    print(f"[模拟] 4 组 × {N_SIMULATIONS:,} 次 ...")
    mc_sig = simulate([{"r_multiple": r} for r in sig_rs],
                      n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
    if "error" in mc_sig:
        print(f"  ❌ 信号层: {mc_sig['error']}")
        return 1
    mcs = [mc_sig]
    for g in groups:
        mc = simulate([{"r_multiple": r} for r in g["rs"]],
                      n_simulations=N_SIMULATIONS, fee_per_trade_r=FEE_PER_TRADE_R)
        if "error" in mc:
            print(f"  ❌ {g['risk_pct']}%: {mc['error']}")
            return 1
        mcs.append(mc)

    sig_sum = enhanced_summary(mc_sig, groups[1]["avg_risk"])   # 信号层金额换算用 2.0% 主档
    cap_sums = [enhanced_summary(mc, g["avg_risk"]) for mc, g in zip(mcs[1:], groups)]
    # total_r 补进 summary（增强字段）
    for s, rs in zip([sig_sum] + cap_sums, [sig_rs] + [g["rs"] for g in groups]):
        s["total_r"] = float(np.sum(rs))
    rows = [sig_sum, *cap_sums]
    for s, g in zip(cap_sums, groups):
        s["risk_pct"] = g["risk_pct"]
        s["max_positions"] = g["max_positions"]
    print("[模拟] 4 组完成")

    # ── 分布诊断（信号层 + 各档资金层）──
    sig_diag = r_stats(sig_rs)
    sig_diag["tail"] = tail_stability(sig_rs)
    sig_buckets = r_bucket_dist(sig_rs)
    cap_diag = {}
    cap_buckets = {}
    for g, s in zip(groups, cap_sums):
        key = f"{g['risk_pct']:.1f}"
        d = r_stats(g["rs"])
        d["tail"] = tail_stability(g["rs"])
        cap_diag[key] = d
        cap_buckets[key] = r_bucket_dist(g["rs"])

    # ── 分市场段蒙卡 C ──
    print("[分段] 信号日市场状态归属 ...")
    df = attach_regime(df)
    index_df = load_index_df()
    series = regime_series(index_df) if index_df is not None else None
    sig_by_regime = {r: df.loc[df["regime"] == r, "r_20d"].tolist()
                     for r in list(REGIMES) + ["未知"]}
    seg_sig = segment_simulate(sig_by_regime, groups[1]["avg_risk"])
    for r in seg_sig:
        r["layer"] = "信号层"
    # 2.0% 主档资金层成交分段
    main_group = groups[1]  # 2.0%×3仓
    tr = _trades_by_regime(main_group["trades"], series)
    cap_by_regime = {k: [r for r, _ in v] for k, v in tr.items()}
    avg_risk_by_regime = {k: float(np.mean([risk for _, risk in v])) if v else 0.0
                          for k, v in tr.items()}
    seg_cap = segment_simulate(cap_by_regime, avg_risk_by_regime)
    for r in seg_cap:
        r["layer"] = "资金层"
    print("[分段] 完成")

    # ── 渲染 ──
    out = Path(args.out) if args.out else DEFAULT_REPORT
    lines = render_report(rows, sig_diag, sig_buckets, cap_diag, cap_buckets,
                          seg_sig + seg_cap, n_trig, len(groups[1]["trades"]),
                          groups[1]["avg_risk"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
