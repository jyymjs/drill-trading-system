"""交易记录系统 — 记录每笔真实交易，计算统计指标"""
import csv
import os
from pathlib import Path

from 分析决策.风控.position import TradeRecord

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "交易日志"
TRADES_FILE = JOURNAL_DIR / "trade_journal.csv"

TRADE_COLUMNS = [
    "trade_id", "date", "symbol", "name", "direction",
    "entry_price", "exit_price", "volume",
    "stop_loss", "r_multiple", "pnl",
    "grade_at_entry", "exit_reason",
]


def _ensure_file():
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    if not TRADES_FILE.exists():
        with open(TRADES_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(TRADE_COLUMNS)


def add_trade(trade: TradeRecord) -> None:
    """添加一笔交易记录"""
    _ensure_file()
    with open(TRADES_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            trade.trade_id, trade.entry_date, trade.symbol, trade.name,
            trade.direction, trade.entry_price, trade.exit_price,
            trade.volume, trade.stop_loss, trade.r_multiple,
            trade.pnl, trade.grade_at_entry, trade.exit_reason,
        ])


def get_all_trades() -> list[dict]:
    """读取所有交易记录"""
    _ensure_file()
    trades = []
    try:
        with open(TRADES_FILE, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            trades = list(reader)
    except (FileNotFoundError, StopIteration):
        pass
    return trades


def trade_stats(trades: list[dict] | None = None) -> dict:
    """计算交易统计数据"""
    if trades is None:
        trades = get_all_trades()

    if not trades:
        return {"total_trades": 0}

    # 转为数值
    for t in trades:
        try:
            t["r_multiple"] = float(t.get("r_multiple", 0) or 0)
            t["pnl"] = float(t.get("pnl", 0) or 0)
        except (ValueError, TypeError):
            t["r_multiple"] = 0.0
            t["pnl"] = 0.0

    r_values = [t["r_multiple"] for t in trades if t["r_multiple"] != 0]
    pnls = [t["pnl"] for t in trades]

    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]

    stats = {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(r_values) if r_values else 0,
        "avg_r_win": sum(wins) / len(wins) if wins else 0,
        "avg_r_loss": sum(losses) / len(losses) if losses else 0,
        "avg_r_all": sum(r_values) / len(r_values) if r_values else 0,
        "total_pnl": sum(pnls),
        "max_r": max(r_values) if r_values else 0,
        "min_r": min(r_values) if r_values else 0,
    }

    # 期望值 = 胜率 × 平均赢R - 败率 × 平均输R
    win_rate = stats["win_rate"]
    avg_win = stats["avg_r_win"]
    avg_loss = abs(stats["avg_r_loss"])
    stats["expectancy"] = win_rate * avg_win - (1 - win_rate) * avg_loss

    # --- 滚动统计（最近20笔，检测策略退化） ---
    if len(r_values) >= 20:
        recent = r_values[-20:]
        recent_wins = sum(1 for r in recent if r > 0)
        stats["rolling_win_rate_20"] = recent_wins / 20
        sum(recent) / 20
        recent_win_avg = sum(r for r in recent if r > 0) / recent_wins if recent_wins > 0 else 0
        recent_loss_avg = sum(r for r in recent if r <= 0) / (20 - recent_wins) if 20 - recent_wins > 0 else 0
        stats["rolling_expectancy_20"] = (recent_wins/20)*recent_win_avg - ((20-recent_wins)/20)*abs(recent_loss_avg)
    else:
        stats["rolling_win_rate_20"] = None
        stats["rolling_expectancy_20"] = None

    # 连败统计
    stats["consecutive_losses"] = 0
    for r in reversed(r_values):
        if r <= 0:
            stats["consecutive_losses"] += 1
        else:
            break

    return stats


def format_stats(stats: dict) -> str:
    """格式化输出交易统计"""
    if stats["total_trades"] == 0:
        return "暂无交易记录"

    wl = stats['wins'] + stats['losses']
    lines = [
        f"  交易总笔数: {stats['total_trades']}",
        f"  胜率: {stats['win_rate']:.1%} ({stats['wins']}/{wl})",
        f"  平均盈R: +{stats['avg_r_win']:.2f}R",
        f"  平均亏R: {stats['avg_r_loss']:.2f}R",
        f"  期望值: {stats['expectancy']:.3f}R/笔",
        f"  总盈亏: ¥{stats['total_pnl']:+.0f}",
        f"  最大单笔: +{stats['max_r']:.1f}R / {stats['min_r']:.1f}R",
    ]
    # 滚动统计
    r20 = stats.get("rolling_win_rate_20")
    if r20 is not None:
        lines.append(f"  滚动20笔胜率: {r20:.1%}")
        lines.append(f"  滚动20笔期望: {stats['rolling_expectancy_20']:.3f}R/笔")
    # 连败
    cl = stats.get("consecutive_losses", 0)
    if cl >= 2:
        lines.append(f"  当前连败: {cl} 笔 ⚠️")
    else:
        lines.append(f"  当前连败: {cl} 笔")
    return "\n".join(lines)
