"""蒙特卡洛模拟 — 验证策略的长期稳定盈利能力

纯 numpy 实现，零额外依赖。

核心思路：
  1. 从历史交易中提取 R倍数序列
  2. 有放回重抽样生成大量可能的交易序列
  3. 模拟每条序列的资金曲线
  4. 输出置信区间（95%），评估长期稳定性
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

from tracker.trade_journal import get_all_trades

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def simulate(trades: list[dict] = None, n_simulations: int = 10000,
             n_trades_per_run: int = None,
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
        if r != 0:  # 过滤零R（避免偏差）
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

    # 最大回撤
    drawdowns = []
    for curve in equity_curves:
        peak = np.maximum.accumulate(curve)
        dd = (peak - curve) / (peak + 1e-10)
        drawdowns.append(float(np.max(dd)))
    max_drawdowns = np.array(drawdowns)

    return {
        "median": median,
        "lower95": lower95,
        "upper95": upper95,
        "lower99": lower99,
        "upper99": upper99,
        "final_equities": final_equities,
        "prob_profit": prob_profit,
        "max_drawdowns": max_drawdowns,
        "n_simulations": n_simulations,
        "n_trades": len(r_values),
        "avg_r": float(np.mean(r_values)),
        "std_r": float(np.std(r_values)),
    }


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
    sample_data = np.zeros((len(indices), n))
    for idx_in_sim, idx_in_curves in enumerate(indices):
        start = result["lower95"][0] if len(result["lower95"]) > 0 else 0
        # 生成模拟路径
        r_values = result.get("r_values", None)
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
             bbox=dict(boxstyle="round", facecolor="#141420", edgecolor="#333"))

    plt.tight_layout(rect=(0, 0.06, 1, 1))

    path = OUTPUT_DIR / "monte_carlo.png"
    if save:
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
        plt.close(fig)
        return str(path)
    plt.close(fig)
    return ""
