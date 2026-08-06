#!/usr/bin/env python3
"""蒙特卡洛输出标准①：复刻级文本版式（2026-08-06 老板拍板"精准复刻"）

标准 5 板块（老板版式一字不动，英文主名+中文注释）：
  CONFIGURATION / EQUITY PERFORMANCE / RISK PROFILE / DRAWDOWN DEPTH / STREAKS
扩展 7 板块（>>> EXT: 前缀，同风格可剥离——要纯标准砍扩展）：
  TAIL STABILITY（去尾稳定性）/ R BUCKETS（盈亏比档位）/ QUANTILES（终值七分位）/
  DRAWDOWN PERCENTILES（回撤分位）/ STREAK DISTRIBUTION（连败分位）/
  WIN RATE BUCKETS（区间胜率）/ MARKET REGIME（市场分段）

版式规范（老板提供，记忆 monte-carlo-report-style.md）：
  白底黑字等宽终端风格；全宽短横线分隔；三列 6:2.5:1.5（竖线前后 1 空格严格对齐）；
  标题 `SIMULATION REPORT: Middle 100.0% (N Scenarios)`；
  数值格式：金额千分位 2 位小数 / 百分比 1 位小数（收益带+ 回撤带-）/ 次数 "N x"。

数据源：分析决策/跟踪/monte_carlo.simulate 输出（final_equities/max_drawdowns/
streaks/samples）+ 可选的 R 序列（扩展板块用）。

用法:
  python 项目/回测系统/monte_carlo_style.py           # 自检（小样本演示）
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np

from 分析决策.跟踪.monte_carlo import _disp_w, _pad

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


W = 78                      # 总宽
C1, C2, C3 = 42, 20, 12     # 三列宽（约 6:2.5:1.5）
LINE = "-" * W


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _pct(v: float, signed: bool = True) -> str:
    return f"{'+' if signed and v >= 0 else ''}{v:.1f}%"


def _x(n: float) -> str:
    return f"{n:.1f} x" if abs(n - round(n)) > 1e-9 else f"{n:.0f} x"


def _hdr(name: str) -> str:
    return f">>> {name}"


def _row(name: str, value: str = "", ret: str = "") -> str:
    return (f"  {_pad(name, C1 - 4)} | {_pad(value, C2 - 2, 'r')}"
            f" | {_pad(ret, C3 - 2)}")


def _block_hdr(name: str) -> str:
    return _row(_hdr(name))


# ── 标准 5 板块数据准备 ──

def _scenario_stats(result: dict, initial_capital: float, risk_amt: float,
                    display_range: float, ruin_threshold_pct: float
                    ) -> dict:
    """从 simulate 输出计算标准板块所需全部数值

    口径：
      - 每笔风险金额 = risk_amt（平均单笔风险，R × risk_amt → 金额）
      - 权益金额 = 初始资金 + 累计 R × risk_amt
      - Display Range：保留中间 display_range% 场景（Cut 两端极端）
      - Best/Worst = P99/P1（标注口径；"上限/下限"语义）
      - 破产线 = ruin_threshold_pct × 初始资金（路径任意时刻跌破）
      - 回撤金额 = R 回撤 × risk_amt（总资产口径，铁律 08-06）
    """
    final_r = result["final_equities"]
    curves = np.cumsum(result["samples"], axis=1)
    min_r = curves.min(axis=1)
    ruin_r = (ruin_threshold_pct * initial_capital - initial_capital) / risk_amt \
        if risk_amt > 0 else 0.0

    cut = (100.0 - display_range) / 2.0
    lo = np.percentile(final_r, cut) if cut > 0 else float(final_r.min())
    hi = np.percentile(final_r, 100.0 - cut) if cut > 0 else float(final_r.max())
    keep = (final_r >= lo) & (final_r <= hi)
    fin = final_r[keep]

    eq = initial_capital + risk_amt * fin
    avg_eq, med_eq = float(eq.mean()), float(np.median(eq))
    best_eq = float(initial_capital + risk_amt * np.percentile(fin, 99))
    worst_eq = float(initial_capital + risk_amt * np.percentile(fin, 1))
    dds = result["max_drawdowns"][keep] * risk_amt
    avg_dd, worst_dd, best_dd = (float(dds.mean()), float(dds.max()),
                                 float(dds.min()))
    ruin_rate = float(np.mean(min_r < ruin_r)) * 100.0 if risk_amt > 0 else 0.0
    streaks = result["streaks"][keep]
    return {
        "n_scen": int(keep.sum()),
        "avg_eq": avg_eq, "med_eq": med_eq,
        "best_eq": best_eq, "worst_eq": worst_eq,
        "avg_dd": avg_dd, "worst_dd": worst_dd, "best_dd": best_dd,
        "ruin_rate": ruin_rate,
        "avg_streak": float(streaks.mean()),
        "worst_streak": float(streaks.max()),
        "best_streak": float(streaks.min()),
        "prob_profit": result["prob_profit"],
        "years": 3.0,
    }


# ── 标准 5 板块渲染 ──

def _render_standard(s: dict, initial_capital: float, display_range: float,
                     ruin_threshold_pct: float, years: float) -> list[str]:
    out = []
    ruin_th = ruin_threshold_pct * initial_capital
    ann_avg = (s["avg_eq"] / initial_capital) ** (1.0 / years) - 1.0 \
        if initial_capital > 0 else 0.0
    ann_med = (s["med_eq"] / initial_capital) ** (1.0 / years) - 1.0 \
        if initial_capital > 0 else 0.0

    # 板块 1：CONFIGURATION（参数配置）
    out.append(_block_hdr("CONFIGURATION（参数配置）"))
    out.append(_row("Initial Capital（初始资金）", _money(initial_capital)))
    out.append(_row("Display Range（显示范围）", f"Middle {display_range:.1f}%",
                    f"Cut {(100.0 - display_range) / 2:.2f}%"))
    out.append(_row("Ruin Threshold（破产线）", _money(ruin_th),
                    f"< {ruin_threshold_pct:.0%}"))
    out.append(LINE)

    # 板块 2：EQUITY PERFORMANCE（资金表现）
    out.append(_block_hdr("EQUITY PERFORMANCE（资金表现）"))
    out.append(_row("Average Final Equity（平均权益）", _money(s["avg_eq"]),
                    _pct((s["avg_eq"] / initial_capital - 1) * 100)))
    out.append(_row("Median Final Equity（中位权益）", _money(s["med_eq"]),
                    _pct((s["med_eq"] / initial_capital - 1) * 100)))
    out.append(_row("Best Case / High Bound（最佳/上限）", _money(s["best_eq"]),
                    _pct((s["best_eq"] / initial_capital - 1) * 100)))
    out.append(_row("Worst Case / Low Bound（最差/下限 P1）", _money(s["worst_eq"]),
                    _pct((s["worst_eq"] / initial_capital - 1) * 100)))
    out.append(_row("Average Annual Return（平均年化）", _pct(ann_avg * 100),
                    f"Compound {years:.1f}y"))
    out.append(_row("Median Annual Return（中位年化）", _pct(ann_med * 100),
                    f"Compound {years:.1f}y"))
    out.append(LINE)

    # 板块 3：RISK PROFILE（风险画像）
    out.append(_block_hdr("RISK PROFILE（风险画像）"))
    out.append(_row("Probability of Profit（区间内胜率）",
                    f"{s['prob_profit']:.1%}",
                    f"In {display_range:.1f}% Range"))
    out.append(_row("Risk of Ruin（全样本破产率）", f"{s['ruin_rate']:.1f}%",
                    "ALL SCENARIOS"))
    out.append(LINE)

    # 板块 4：DRAWDOWN DEPTH（回撤深度）
    out.append(_block_hdr("DRAWDOWN DEPTH（回撤深度）"))
    out.append(_row("Avg Max Drawdown（平均最大回撤）", _money(s["avg_dd"]),
                    f"-{(s['avg_dd'] / initial_capital * 100):.1f}%"))
    out.append(_row("Worst Max Drawdown（最差最大回撤）", _money(s["worst_dd"]),
                    f"-{(s['worst_dd'] / initial_capital * 100):.1f}%"))
    out.append(_row("Best Max Drawdown（最小最大回撤）", _money(s["best_dd"]),
                    f"-{(s['best_dd'] / initial_capital * 100):.1f}%"))
    out.append(LINE)

    # 板块 5：STREAKS（连败统计）
    out.append(_block_hdr("STREAKS（连败统计）"))
    out.append(_row("Avg Losing Streak（平均连败次数）", _x(s["avg_streak"])))
    out.append(_row("Worst Losing Streak（最大连败次数）", _x(s["worst_streak"]),
                    "Extreme Risk"))
    out.append(_row("Best Losing Streak（最小连败次数）", _x(s["best_streak"]),
                    "Best Luck"))
    out.append(LINE)
    return out


# ── 扩展 7 板块（>>> EXTENDED: 前缀，可剥离）──

def _render_extended(rs: list[float] | np.ndarray | None, result: dict,
                     initial_capital: float, risk_amt: float,
                     regimes: dict | None) -> list[str]:
    from 回测系统.monte_carlo_dist import (r_bucket_dist, r_stats,
                                            tail_stability)
    out = []

    # 扩展 1：TAIL STABILITY（去尾稳定性）
    if rs is not None:
        rs = np.asarray(rs, dtype=float)
        tail = tail_stability(rs.tolist())   # list[dict]，第 0 行 = 全量基准
        out.append(_block_hdr("EXT: TAIL STABILITY（去尾稳定性）"))
        for row in tail:
            label = "All (基准)" if row["pct"] == 0.0 else f"Top {row['pct']:.0%}"
            verdict = "依赖大赢家" if row["crashed"] else "稳定"
            out.append(_row(f"Drop {label} ({row['n_trim']} 笔)",
                            f"avgR {row['avg_r']:+.3f}", verdict))
        out.append(LINE)

        # 扩展 2：R BUCKETS（盈亏比档位）
        buckets = r_bucket_dist(rs.tolist())
        out.append(_block_hdr("EXT: R BUCKETS（盈亏比档位）"))
        for b in buckets:
            out.append(_row(f"{b['label']}", f"{b['n']} 笔 / {b['pct']:.1%}",
                            f"{b.get('total_r', 0):+.1f}R"))
        out.append(LINE)

        # 扩展 3：QUANTILES（终值七分位）——用模拟终值（final_equities，累计 R），
        # 非单笔 R 分布（2026-08-07 修复：原误用单笔分位，P50 恒≈0 与终值矛盾）
        out.append(_block_hdr("EXT: QUANTILES（终值七分位）"))
        fin_r = np.asarray(result["final_equities"], dtype=float)
        for q in (1, 5, 25, 50, 75, 95, 99):
            v = float(np.percentile(fin_r, q))
            out.append(_row(f"P{q}（{q}% 分位）",
                            f"{initial_capital + risk_amt * v:,.2f} 元",
                            f"{v:+.1f}R"))
        out.append(LINE)

    # 扩展 4：DRAWDOWN PERCENTILES（回撤分位）
    dds = result["max_drawdowns"] * risk_amt
    out.append(_block_hdr("EXT: DD PERCENTILES（回撤分位）"))
    for q in (50, 95, 99):
        v = float(np.percentile(dds, q))
        out.append(_row(f"Max DD P{q}（{q}% 分位）", _money(v),
                        f"-{(v / initial_capital * 100):.1f}%"))
    out.append(LINE)

    # 扩展 5：STREAK DISTRIBUTION（连败分布）
    streaks = result["streaks"].astype(float)
    out.append(_block_hdr("EXT: STREAKS（连败分布）"))
    for q in (50, 90, 99):
        out.append(_row(f"Losing Streak P{q}（{q}% 分位）",
                        _x(float(np.percentile(streaks, q)))))
    out.append(LINE)

    # 扩展 6：WIN RATE BUCKETS（区间胜率）
    fin = result["final_equities"]
    out.append(_block_hdr("EXT: WIN RATE BUCKETS（区间胜率）"))
    for label, th in (("Final >= 0R", 0.0), ("Final >= +10%",
                      initial_capital * 0.10 / risk_amt if risk_amt > 0 else 0.0),
                      ("Final >= +20%",
                      initial_capital * 0.20 / risk_amt if risk_amt > 0 else 0.0)):
        p = float(np.mean(fin >= th)) * 100.0
        out.append(_row(label, f"{p:.1f}%", ""))
    out.append(LINE)

    # 扩展 7：MARKET REGIME（市场分段）
    if regimes:
        out.append(_block_hdr("EXT: MARKET REGIME（市场分段）"))
        for r, v in regimes.items():
            # prob 为百分数口径（segment_mc 已 ×100），直接 .1f%
            # （2026-08-07 修复：原 :.1% 双重百分比显示 9820%）
            out.append(_row(f"{r}（{v['label']}）",
                            f"盈利概率 {v['prob']:.1f}%",
                            f"中位 {v['median']:+.1f}R / 最差5% {v['worst5']:+.1f}R"))
        out.append(LINE)
    return out


def render_scenario_report(result: dict, initial_capital: float = 5600.0,
                           risk_amt: float = 112.0, display_range: float = 100.0,
                           ruin_threshold_pct: float = 0.25, years: float = 3.0,
                           extended: bool = True,
                           rs: list[float] | np.ndarray | None = None,
                           regimes: dict | None = None) -> str:
    """复刻级版式蒙特卡洛报告（标准 5 板块 + 扩展 7 板块可剥离）

    Args:
        result: monte_carlo.simulate 输出
        initial_capital: 初始资金（元，默认 5600 实盘线配置）
        risk_amt: 每笔风险金额（元，默认 112 = 5600×2%——G9 定稿档）
        display_range: 保留中间比例（默认 100 = 全样本，标题 Middle 100.0%）
        ruin_threshold_pct: 破产线 = 初始资金 × 比例（默认 25%）
        years: 年化复利换算年限（默认 3 年回测期）
        extended: 是否输出扩展 7 板块（默认 True；False = 纯标准可剥离）
        rs: 成交 R 序列（扩展板块数据源；None → 跳过去尾/档位/七分位）
        regimes: 市场分段蒙卡结果 dict（扩展板块；None → 跳过）

    Returns:
        版式文本（终端风格，全宽短横线闭合）
    """
    if "error" in result:
        return f"SIMULATION ERROR: {result['error']}"

    s = _scenario_stats(result, initial_capital, risk_amt, display_range,
                        ruin_threshold_pct)
    n_scen = s["n_scen"]
    n_all = int(result["n_simulations"])

    out = [LINE]
    out.append(f"SIMULATION REPORT: Middle {display_range:.1f}% "
               f"({n_scen} Scenarios)".center(W))
    out.append(LINE)
    out.append(_row("METRIC（指标）", "VALUE（数值）", "RET/PCT"))
    out.append(LINE)
    out += _render_standard(s, initial_capital, display_range,
                            ruin_threshold_pct, years)
    if extended:
        out += _render_extended(rs, result, initial_capital, risk_amt, regimes)
    return "\n".join(out)


def main() -> int:
    """自检：小样本演示标准版式"""
    from 分析决策.跟踪.monte_carlo import simulate
    rng = np.random.default_rng(7)
    demo_rs = rng.normal(0.4, 1.2, 60).tolist()
    mc = simulate([{"r_multiple": r} for r in demo_rs], n_simulations=1000)
    print(render_scenario_report(mc, rs=demo_rs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
