"""蒙特卡洛模拟 — 验证策略的长期稳定盈利能力

纯 numpy 实现，零额外依赖。

核心思路：
  1. 从历史交易中提取 R倍数序列
  2. 有放回重抽样生成大量可能的交易序列
  3. 模拟每条序列的资金曲线
  4. 输出置信区间（95%），评估长期稳定性
"""
import matplotlib
import numpy as np

matplotlib.use("Agg")
from pathlib import Path

import matplotlib.pyplot as plt

# 中文字体（Windows 微软雅黑；缺失时退回默认，仅图表显示问题不影响功能）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from 分析决策.跟踪.trade_journal import get_all_trades

try:
    import pandas as pd
except ImportError:
    pd = None  # 回测数据源需要 pandas，实盘 journal 路径不依赖

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "扫描输出"


def simulate(trades: list[dict] | None = None, n_simulations: int = 10000,
             n_trades_per_run: int | None = None,
             fee_per_trade_r: float = 0.02) -> dict:
    """蒙特卡洛模拟（含交易成本）

    Args:
        trades: 交易记录列表（None=从CSV读取）
        n_simulations: 模拟次数
        n_trades_per_run: 每次模拟的交易数（None=用实际交易数）

    Returns:
        {"median": np.ndarray,      # 中位数曲线
         "lower95": np.ndarray,     # 95%置信下限
         "upper95": np.ndarray,     # 95%置信上限
         "lower99": np.ndarray,     # 99%置信下限
         "upper99": np.ndarray,     # 99%置信上限
         "final_equities": np.ndarray,  # 所有模拟的终值
         "prob_profit": float,      # 盈利概率
         "max_drawdowns": np.ndarray}   # 每条模拟的最大回撤
    """
    if trades is None:
        trades = get_all_trades()

    if not trades:
        return {"error": "无交易记录"}

    # 提取 R倍数序列
    r_values = []
    for t in trades:
        try:
            r = float(t.get("r_multiple", 0) or 0)
        except (ValueError, TypeError):
            r = 0.0
        if r != 0:  # 过滤零R（避免偏差；实测 0 笔无影响，E-029 标注）
            r_values.append(r)

    r_values = np.array(r_values)
    if len(r_values) < 5:
        return {"error": f"有效交易不足 ({len(r_values)}笔)"}

    if n_trades_per_run is None:
        n_trades_per_run = len(r_values)

    # 使用新式 RNG
    rng = np.random.default_rng(2024)

    # 批量生成随机样本：形状 (n_simulations, n_trades_per_run)
    samples = rng.choice(r_values, size=(n_simulations, n_trades_per_run), replace=True)

    # 扣除交易成本（每笔交易扣除 fee_per_trade_r R）
    samples = samples - fee_per_trade_r

    # 计算资金曲线：逐笔累加
    equity_curves = np.cumsum(samples, axis=1)

    # 最终净值分布
    final_equities = equity_curves[:, -1]

    # 置信区间
    median = np.median(equity_curves, axis=0)
    lower95 = np.percentile(equity_curves, 2.5, axis=0)
    upper95 = np.percentile(equity_curves, 97.5, axis=0)
    lower99 = np.percentile(equity_curves, 0.5, axis=0)
    upper99 = np.percentile(equity_curves, 99.5, axis=0)

    # 盈利概率
    prob_profit = float(np.mean(final_equities > 0))

    # 最大回撤（R 单位：峰值到谷底的绝对回撤，而非百分比）
    drawdowns = []
    for curve in equity_curves:
        peak = np.maximum.accumulate(curve)
        dd = peak - curve
        drawdowns.append(float(np.max(dd)))
    max_drawdowns = np.array(drawdowns)

    # 连败统计：每条路径的最大连续亏损笔数（单笔 R 的负值连续段）
    neg = samples < 0
    streaks = np.zeros(n_simulations, dtype=int)
    for i, row in enumerate(neg):
        max_streak = 0
        cur = 0
        for is_neg in row:
            cur = cur + 1 if is_neg else 0
            max_streak = max(max_streak, cur)
        streaks[i] = max_streak

    return {
        "median": median,
        "lower95": lower95,
        "upper95": upper95,
        "lower99": lower99,
        "upper99": upper99,
        "final_equities": final_equities,
        "prob_profit": prob_profit,
        "max_drawdowns": max_drawdowns,
        "streaks": streaks,
        "samples": samples,          # 每条路径的交易序列（连败/破产重算用）
        "n_simulations": n_simulations,
        "n_trades": len(r_values),
        "avg_r": float(np.mean(r_values)),
        "std_r": float(np.std(r_values)),
    }


import unicodedata as _ud


def _disp_w(s: str) -> int:
    """终端显示宽度：全角（中文等）按 2 字符算"""
    return sum(2 if _ud.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, w: int, align: str = "l") -> str:
    """按显示宽度填充对齐（中英文混排时竖线严格对齐）"""
    gap = w - _disp_w(s)
    if gap <= 0:
        return s
    if align == "r":
        return " " * gap + s
    if align == "c":
        return " " * (gap // 2) + s + " " * (gap - gap // 2)
    return s + " " * gap


def load_backtest_r_series(signals_path: str, mode: str = "prebreak",
                           hold: str = "20d", sample_n: int = 500,
                           seed: int = 42) -> list[dict]:
    """从回测 signals.csv 抽取 R 序列（作为蒙特卡洛数据源）

    Args:
        signals_path: 回测 signals.csv 路径
        mode: normal / prebreak（默认 prebreak——回测结论主模式）
        hold: 5d / 10d / 20d 观察窗（默认 20d——趋势跟踪特征最优）
        sample_n: 无放回抽样笔数（默认 500，与回测报告口径一致）
        seed: 抽样种子（默认 42，可复现）
    """
    if pd is None:
        raise RuntimeError("回测数据源需要 pandas，请安装后重试")
    df = pd.read_csv(signals_path, encoding="utf-8-sig")
    r_col = f"r_{hold}"
    tr_col = f"triggered_{hold}"
    if r_col not in df.columns or tr_col not in df.columns:
        raise ValueError(f"signals.csv 缺少 {r_col}/{tr_col} 列，结构不符预期")
    sub = df[(df["mode"] == mode) & (df[tr_col] == 1) & df[r_col].notna()]
    if len(sub) < sample_n:
        raise ValueError(f"{mode}/{hold} 触发信号仅 {len(sub)} 个，不足抽样 {sample_n} 笔")
    rng = np.random.default_rng(seed)
    r_vals = rng.choice(sub[r_col].values, sample_n, replace=False)
    return [{"r_multiple": float(r)} for r in r_vals]


def load_backtest_years(signals_path: str) -> float:
    """从回测 signals.csv 的信号日期跨度计算年数（年化收益率口径）"""
    if pd is None:
        return 3.0
    df = pd.read_csv(signals_path, encoding="utf-8-sig")
    if "date" not in df.columns:
        return 3.0
    d0, d1 = df["date"].min(), df["date"].max()
    try:
        span_days = (pd.to_datetime(d1) - pd.to_datetime(d0)).days
    except Exception:
        return 3.0
    return max(span_days / 365.25, 0.5)


def _fmt_money(v: float) -> str:
    """千分位 2 位小数（右对齐用）"""
    return f"{v:,.2f}"


def _fmt_pct(v: float, signed: bool = True) -> str:
    """百分比 1 位小数，带正负号"""
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v:.1f}%"


def render_terminal_report(result: dict, initial_capital: float = 100_000.0,
                           risk_per_trade: float = 0.01,
                           display_range: float = 100.0,
                           ruin_threshold_pct: float = 0.25,
                           years: float = 3.0) -> str:
    """复刻级终端版式蒙特卡洛报告（纯文本等宽字符风格）

    版式（按老板提供的版式描述）：
      白底黑字终端风格、全宽短横线分隔、三列竖线表格（6:2.5:1.5）、
      5 板块 >>> 标题 + 指标行，金额千分位、百分比带正负号、次数带 x。

    口径：
      - 每笔风险 = risk_per_trade × initial_capital（R 单位 × 每笔风险额 → 权益金额）
      - 破产线 = ruin_threshold_pct × initial_capital（路径任意时刻跌破即破产）
      - Display Range：保留中间 display_range% 的场景（裁剪两端极端），
        标题 "Middle X%"；Cut = (100-X)/2
      - 年化收益 = (终值/初始资金)^(1/years) - 1（复利口径）
    """
    if "error" in result:
        return f"SIMULATION ERROR: {result['error']}"

    result["n_simulations"]
    rng_r = risk_per_trade * initial_capital      # 每笔风险金额
    final_r = result["final_equities"]
    curves = np.cumsum(result["samples"], axis=1)  # 累计 R 曲线（含费用）
    np.maximum.accumulate(curves, axis=1)
    min_equity_r = curves.min(axis=1)              # 每条路径最低累计 R
    ruin_r = (ruin_threshold_pct * initial_capital - initial_capital) / rng_r  # 破产 R 线

    # 场景裁剪（Display Range）
    cut = (100.0 - display_range) / 2.0
    lo = np.percentile(final_r, cut) if cut > 0 else final_r.min()
    hi = np.percentile(final_r, 100.0 - cut) if cut > 0 else final_r.max()
    keep = (final_r >= lo) & (final_r <= hi)
    fin = final_r[keep]
    n_scen = int(keep.sum())

    # ── 各板块数值 ──
    eq = initial_capital + rng_r * fin
    avg_eq, med_eq = eq.mean(), np.median(eq)
    best_eq, worst_eq = eq.max(), eq.min()
    # 回撤（金额）：R 回撤 × 每笔风险
    dds = result["max_drawdowns"][keep] * rng_r
    avg_dd, worst_dd, best_dd = dds.mean(), dds.max(), dds.min()
    # 破产率（全样本，按 Display Range 口径同口径计算）
    ruin_rate = float(np.mean(min_equity_r < ruin_r)) * 100
    # 连败（保留场景内）
    streaks = result["streaks"][keep]
    avg_streak, worst_streak, best_streak = (streaks.mean(), streaks.max(), streaks.min())

    # ── 版式渲染 ──
    W = 78                      # 总宽
    C1, C2, C3 = 42, 20, 12     # 三列宽（约 6:2.5:1.5 于总宽比例内）
    line = "-" * W

    def hdr(name: str) -> str:
        return f">>> {name}"

    def row(name: str, value: str = "", ret: str = "") -> str:
        return f"  {_pad(name, C1 - 4)} | {_pad(value, C2 - 2, 'r')} | {_pad(ret, C3 - 2)}"

    out = []
    out.append(line)
    title = f"蒙特卡洛模拟报告（中段 {display_range:.1f}% · {n_scen} 个场景）"
    out.append(title.center(W))
    out.append(line)
    out.append(row("指标", "数值", "收益/备注"))
    out.append(line)

    # 板块 1：参数配置
    out.append(row(hdr("参数配置")))
    out.append(row("初始资金", _fmt_money(initial_capital)))
    out.append(row("显示范围", f"{display_range:.1f}%", f"裁剪 {cut:.2f}%"))
    out.append(row("破产线", _fmt_money(ruin_threshold_pct * initial_capital),
                   f"< {ruin_threshold_pct:.0%}"))
    out.append(line)

    # 年化（复利）：(终值/初始)^(1/years) - 1
    ann_avg = (avg_eq / initial_capital) ** (1.0 / years) - 1.0
    ann_med = (med_eq / initial_capital) ** (1.0 / years) - 1.0

    # 板块 2：资金表现
    out.append(row(hdr("资金表现")))
    out.append(row("平均终值权益", _fmt_money(avg_eq),
                   _fmt_pct((avg_eq / initial_capital - 1) * 100)))
    out.append(row("中位终值权益", _fmt_money(med_eq),
                   _fmt_pct((med_eq / initial_capital - 1) * 100)))
    out.append(row("最佳情景 / 上限", _fmt_money(best_eq),
                   _fmt_pct((best_eq / initial_capital - 1) * 100)))
    out.append(row("最差情景 / 下限", _fmt_money(worst_eq),
                   _fmt_pct((worst_eq / initial_capital - 1) * 100)))
    out.append(row("平均年化收益", _fmt_pct(ann_avg * 100), f"复利 {years:.1f} 年"))
    out.append(row("中位年化收益", _fmt_pct(ann_med * 100), f"复利 {years:.1f} 年"))
    out.append(line)

    # 板块 3：风险画像
    out.append(row(hdr("风险画像")))
    out.append(row("盈利概率", f"{result['prob_profit']:.1%}",
                   f"区间内 {display_range:.1f}%"))
    out.append(row("破产风险", f"{ruin_rate:.1f}%", "全样本"))
    out.append(line)

    # 板块 4：回撤深度
    out.append(row(hdr("回撤深度")))
    out.append(row("平均最大回撤", _fmt_money(avg_dd),
                   f"-{(avg_dd / initial_capital * 100):.1f}%"))
    out.append(row("最差最大回撤", _fmt_money(worst_dd),
                   f"-{(worst_dd / initial_capital * 100):.1f}%"))
    out.append(row("最小最大回撤", _fmt_money(best_dd),
                   f"-{(best_dd / initial_capital * 100):.1f}%"))
    out.append(line)

    # 板块 5：连败统计
    out.append(row(hdr("连败统计")))
    out.append(row("平均连败次数", f"{avg_streak:.1f} x"))
    out.append(row("最大连败次数", f"{worst_streak:.0f} x", "极端风险"))
    out.append(row("最小连败次数", f"{best_streak:.0f} x", "最佳运气"))
    out.append(line)

    return "\n".join(out)


def plot_simulation(result: dict, save: bool = True) -> str:
    """绘制蒙特卡洛模拟结果

    Returns:
        图片路径
    """
    if "error" in result:
        return ""

    n = len(result["median"])

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.patch.set_facecolor("#0a0a0a")

    # 1. 模拟路径（采样显示）
    ax = axes[0, 0]
    final = result["final_equities"]
    # 随机采样 100 条路径显示
    rng = np.random.default_rng(42)
    indices = rng.choice(len(final), min(100, len(final)), replace=False)
    np.zeros((len(indices), n))
    for idx_in_sim, idx_in_curves in enumerate(indices):
        result["lower95"][0] if len(result["lower95"]) > 0 else 0
        # 生成模拟路径
        result.get("r_values", None)
    ax.set_facecolor("#141420")
    ax.set_title("模拟路径 (随机100条)", color="#ccc")
    ax.grid(alpha=0.1)

    # 2. 置信区间
    ax = axes[0, 1]
    x = range(n)
    ax.plot(x, result["median"], color="#00d4aa", linewidth=2, label="中位数")
    ax.fill_between(x, result["lower95"], result["upper95"],
                    alpha=0.3, color="#00d4aa", label="95% CI")
    ax.fill_between(x, result["lower99"], result["upper99"],
                    alpha=0.15, color="#00d4aa", label="99% CI")
    ax.axhline(y=0, color="#555", linewidth=0.5, linestyle="--")
    ax.set_facecolor("#141420")
    ax.set_title("资金曲线置信区间", color="#ccc")
    ax.set_ylabel("累计 R")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.1)

    # 3. 最终净值分布直方图
    ax = axes[1, 0]
    ax.hist(final, bins=50, color="#00d4aa", alpha=0.7, edgecolor="none")
    ax.axvline(x=0, color="#ff4d4d", linewidth=1, linestyle="--")
    ax.axvline(x=np.median(final), color="#fff", linewidth=1, linestyle="--", label=f"中位数: {np.median(final):.1f}R")
    ax.set_facecolor("#141420")
    ax.set_title(f"最终净值分布 (盈利概率: {result['prob_profit']:.1%})", color="#ccc")
    ax.set_xlabel("累计 R")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.1)

    # 4. 最大回撤分布
    ax = axes[1, 1]
    dd = result["max_drawdowns"]
    ax.hist(dd, bins=50, color="#ff4d4d", alpha=0.7, edgecolor="none")
    ax.axvline(x=np.median(dd), color="#fff", linewidth=1, linestyle="--",
               label=f"中位数: {np.median(dd):.1%}")
    ax.set_facecolor("#141420")
    ax.set_title("最大回撤分布", color="#ccc")
    ax.set_xlabel("最大回撤")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.1)

    # 统计信息
    stats_text = (
        f"基础统计:\n"
        f"  交易笔数: {result['n_trades']}\n"
        f"  平均R: {result['avg_r']:.3f}R\n"
        f"  R标准差: {result['std_r']:.2f}\n"
        f"  95% CI: {np.percentile(final, 2.5):.1f}R ~ {np.percentile(final, 97.5):.1f}R\n"
        f"  盈利概率: {result['prob_profit']:.1%}"
    )
    fig.text(0.5, 0.01, stats_text, ha="center", fontsize=9, color="#888",
             bbox={"boxstyle": "round", "facecolor": "#141420", "edgecolor": "#333"})

    plt.tight_layout(rect=(0, 0.06, 1, 1))

    path = OUTPUT_DIR / "monte_carlo.png"
    if save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
        plt.close(fig)
        return str(path)
    plt.close(fig)
    return ""
