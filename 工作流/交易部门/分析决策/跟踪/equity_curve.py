"""资金曲线生成与统计指标"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pathlib import Path

from 分析决策.跟踪.trade_journal import get_all_trades

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def compute_returns(trades: list[dict]) -> pd.Series:
    """从交易记录计算逐笔收益率序列

    每笔交易的 R 倍数作为一次独立风险回报事件。
    """
    if not trades:
        return pd.Series(dtype=float)

    r_values = []
    for t in trades:
        try:
            r = float(t.get("r_multiple", 0) or 0)
        except (ValueError, TypeError):
            r = 0.0
        r_values.append(r)

    return pd.Series(r_values, name="r_multiple")


def equity_curve_from_trades(trades: list[dict]) -> np.ndarray:
    """从交易记录生成资金曲线

    假设每笔交易承担 1R 风险，R倍数直接累加。
    """
    r_values = [float(t.get("r_multiple", 0) or 0) for t in trades]
    return np.cumsum(r_values)


def compute_sharpe(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
    """计算夏普比率

    基于 R倍数序列计算。年化假设：一年约 50 笔交易。
    """
    if len(returns) < 3 or returns.std() == 0:
        return 0.0
    excess = returns.mean() - risk_free_rate / 50  # 每笔风险调整
    return float(excess / returns.std() * np.sqrt(50))


def compute_max_drawdown(equity: np.ndarray) -> dict:
    """计算最大回撤

    Returns:
        {"max_dd": float, "start": int, "end": int}
    """
    if len(equity) < 2:
        return {"max_dd": 0.0, "start": 0, "end": 0}

    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / (peak + 1e-10)

    end_idx = np.argmax(dd)
    start_idx = np.argmax(equity[:end_idx]) if end_idx > 0 else 0

    return {
        "max_dd": float(dd[end_idx]),
        "start": int(start_idx),
        "end": int(end_idx),
    }


def plot_equity_curve(save: bool = True) -> str:
    """绘制资金曲线并保存

    Returns:
        图片路径
    """
    trades = get_all_trades()
    if not trades:
        return ""

    equity = equity_curve_from_trades(trades)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10),
                                         gridspec_kw={"height_ratios": [3, 1, 1]})

    # 资金曲线
    ax1.plot(equity, color="#00d4aa", linewidth=1.5, label="资金曲线")
    ax1.axhline(y=0, color="#555", linewidth=0.5, linestyle="--")
    ax1.fill_between(range(len(equity)), equity, 0, alpha=0.1, color="#00d4aa")
    ax1.set_title("资金曲线 (以R倍数累加)", fontsize=14, color="#ccc")
    ax1.set_ylabel("累计 R")
    ax1.grid(alpha=0.1)
    ax1.legend()

    # 回撤图
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / (peak + 1e-10) * 100
    ax2.fill_between(range(len(dd)), dd, 0, color="#ff4d4d", alpha=0.5)
    ax2.set_title("回撤 (%)", fontsize=14, color="#ccc")
    ax2.set_ylabel("回撤%")
    ax2.grid(alpha=0.1)

    # 滚动20笔胜率
    r_values = [float(t.get("r_multiple", 0) or 0) for t in trades]
    if len(r_values) >= 20:
        roll_win = []
        for i in range(20, len(r_values) + 1):
            chunk = r_values[i - 20:i]
            roll_win.append(sum(1 for r in chunk if r > 0) / 20)
        ax3.plot(range(19, len(r_values)), roll_win, color="#ffa726", linewidth=1.5, label="滚动20笔胜率")
        ax3.axhline(y=0.5, color="#555", linewidth=0.5, linestyle="--")
        ax3.set_title("滚动20笔胜率 (检测策略退化)", fontsize=14, color="#ccc")
        ax3.set_ylabel("胜率")
        ax3.set_ylim(0, 1)
        ax3.grid(alpha=0.1)
        ax3.legend()

    # 统计指标
    returns = compute_returns(trades)
    sharpe = compute_sharpe(returns)
    mdd = compute_max_drawdown(equity)
    stats_text = (
        f"总交易: {len(trades)} | "
        f"夏普: {sharpe:.2f} | "
        f"最大回撤: {mdd['max_dd']:.1%}"
    )
    fig.suptitle(stats_text, fontsize=10, color="#888")

    plt.tight_layout()

    path = OUTPUT_DIR / "equity_curve.png"
    if save:
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
        plt.close(fig)
        return str(path)
    else:
        plt.show()
        return ""
