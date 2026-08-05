"""R 值曲线 — 策略表现跟踪（2026-08-06 老板拍板）

核心思路（与老师 24 节"盈亏比数列法"、回测 stats.py 的 1R 等权口径同构）：
  每笔交易记一个 R 值（盈亏 ÷ 单笔风险），曲线 = 累计 R 曲线（1R 等权）。
  R 值只反映策略本身，不依赖账户余额——外部资金注入不干扰曲线。

录入（两种模式，同一账本 journal/r_curve.csv）：
  1. record：录入入场/止损/出场价 → 自动算 R（可靠，可复核）
     R = (出场价 - 入场价) / (入场价 - 止损价)，亏损单 R ≈ -1（做多）
  2. record-r：直接录入 R 值（最省事）

统计口径（与回测 stats.py 对齐，保证可复现）：
  - 胜 = R>0；平均R = 累计R ÷ 笔数
  - 最大回撤 = 1R 等权累计 R 曲线的最大回撤（max(peak - cum)，全亏光也只到峰值）
  - 曲线按日期升序累积（同日按 id），不依赖录入顺序
"""
import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

# 数据文件与输出目录（测试时可通过模块级常量覆盖）
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"
R_CURVE_FILE = JOURNAL_DIR / "r_curve.csv"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

R_COLUMNS = ["id", "date", "r", "entry", "stop", "exit", "symbol", "note"]

# 亏损单 R 合理区间（用于直接录入 R 值时的告警提示，不做强校验）
LOSS_R_FLOOR = -1.5


# ══════════════════════════════════════════════════════════
# R 值计算
# ══════════════════════════════════════════════════════════

def calc_r(entry: float, stop: float, exit_price: float,
           direction: str = "long") -> float:
    """由价格三件套计算 R 值（老师 24 节口径）

    R = 单笔盈亏 ÷ 单笔风险
      做多: R = (出场价 - 入场价) / (入场价 - 止损价)，止损平仓时 R = -1
      做空: R = (入场价 - 出场价) / (止损价 - 入场价)

    Args:
        entry: 入场价
        stop: 止损价
        exit_price: 出场价
        direction: "long" 做多 / "short" 做空

    Returns:
        R 值（保留 3 位小数）

    Raises:
        ValueError: 参数非法（止损位与入场位相同等）
    """
    if entry <= 0 or stop <= 0 or exit_price <= 0:
        raise ValueError("价格必须为正数")
    if direction == "long":
        risk = entry - stop
        if risk == 0:
            raise ValueError("做多止损价必须低于入场价（风险为 0 无法计算 R）")
        if risk < 0:
            raise ValueError(f"做多止损价({stop})必须低于入场价({entry})")
        r = (exit_price - entry) / risk
    elif direction == "short":
        risk = stop - entry
        if risk <= 0:
            raise ValueError(f"做空止损价({stop})必须高于入场价({entry})")
        r = (entry - exit_price) / risk
    else:
        raise ValueError(f"未知方向: {direction}（仅支持 long/short）")
    return round(r, 3)


# ══════════════════════════════════════════════════════════
# 数据层：R 值记录
# ══════════════════════════════════════════════════════════

def _ensure_file() -> None:
    """确保记录文件存在（含表头）"""
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    if not R_CURVE_FILE.exists():
        with open(R_CURVE_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(R_COLUMNS)


def _next_id(rows: list[dict]) -> int:
    """下一个自增 id"""
    ids = [int(r["id"]) for r in rows if str(r.get("id", "")).isdigit()]
    return max(ids) + 1 if ids else 1


def add_record(date_str: str, r_value: float, entry: float | None = None,
               stop: float | None = None, exit_price: float | None = None,
               symbol: str = "", note: str = "") -> dict:
    """新增一条 R 值记录（同日允许多笔，按 id 区分）

    Args:
        date_str: 交易日期 YYYY-MM-DD
        r_value: R 值
        entry/stop/exit_price: 价格三件套（直接录 R 时可空）
        symbol: 股票代码（可选）
        note: 备注（可选）

    Returns:
        {"id": int, "date": str, "r": float}
    """
    if r_value <= LOSS_R_FLOOR:
        # 亏损单 R 应接近 -1（止损平仓）；更亏说明中间没止损，提示但不阻断
        print(f"[R曲线] ⚠️ R={r_value} 低于常见亏损区间（{LOSS_R_FLOOR}），"
              "确认是止损执行滑点/跳空所致？")
    date.fromisoformat(date_str)  # 校验格式

    _ensure_file()
    rows = []
    with open(R_CURVE_FILE, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    def _f(v):
        return f"{v:.3f}" if v is not None else ""

    rec_id = _next_id(rows)
    rows.append({
        "id": str(rec_id), "date": date_str, "r": f"{r_value:.3f}",
        "entry": _f(entry), "stop": _f(stop), "exit": _f(exit_price),
        "symbol": symbol, "note": note,
    })

    with open(R_CURVE_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=R_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return {"id": rec_id, "date": date_str, "r": r_value}


def delete_record(rec_id: int) -> bool:
    """按 id 删除一条记录（录错时纠错用）"""
    _ensure_file()
    rows = []
    with open(R_CURVE_FILE, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    kept = [r for r in rows if int(r["id"]) != rec_id]
    if len(kept) == len(rows):
        return False

    with open(R_CURVE_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=R_COLUMNS)
        writer.writeheader()
        writer.writerows(kept)
    return True


def get_records() -> list[dict]:
    """读取全部 R 值记录（按日期升序、同日按 id 升序）

    Returns:
        [{"id", "date", "r", "entry", "stop", "exit", "symbol", "note"}, ...]
    """
    _ensure_file()
    rows = []
    try:
        with open(R_CURVE_FILE, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except (FileNotFoundError, StopIteration):
        return []

    recs = []
    for row in rows:
        try:
            r = float(row.get("r", 0) or 0)
        except (ValueError, TypeError):
            continue
        recs.append({
            "id": int(row["id"]),
            "date": row["date"],
            "r": r,
            "entry": row.get("entry", ""),
            "stop": row.get("stop", ""),
            "exit": row.get("exit", ""),
            "symbol": row.get("symbol", ""),
            "note": row.get("note", ""),
        })
    recs.sort(key=lambda x: (x["date"], x["id"]))
    return recs


# ══════════════════════════════════════════════════════════
# 统计层（口径与回测 stats.py 同构）
# ══════════════════════════════════════════════════════════

def max_drawdown_from_r(r_list: list[float]) -> float:
    """累计 R 曲线最大回撤（与回测 stats.py 同口径）

    max(peak - cum)：1R 等权累计曲线，组合口径不做仓位资金曲线；
    全亏光也只到峰值（不会跌破起点）。
    """
    peak, max_dd = 0.0, 0.0
    cum = 0.0
    for r in r_list:
        cum += r
        if cum > peak:
            peak = cum
        elif peak > cum:
            max_dd = max(max_dd, peak - cum)
    return round(max_dd, 4)


def compute_stats(records: list[dict] | None = None) -> dict:
    """R 值曲线统计（胜率/平均R/盈亏比/最大回撤/连亏）

    Args:
        records: 记录列表（None = 从 r_curve.csv 读取，已按日期排序）

    Returns:
        {
          "n_trades", "n_win", "win_rate", "avg_r", "total_r",
          "payoff_ratio", "max_drawdown", "max_loss_streak",
          "current_loss_streak", "expectancy", "cum_curve"
        }
        空账本时返回 {"n_trades": 0}
    """
    if records is None:
        records = get_records()
    if not records:
        return {"n_trades": 0}

    r_list = [r["r"] for r in records]  # 已按日期升序
    n = len(r_list)
    wins = [r for r in r_list if r > 0]
    losses = [r for r in r_list if r <= 0]

    total_r = round(sum(r_list), 4)
    avg_r = round(total_r / n, 4)
    win_rate = round(len(wins) / n, 4)

    # 盈亏比（payoff）= 平均盈R ÷ |平均亏R|（无亏损单时未定义）
    payoff_ratio = None
    if losses:
        avg_loss = abs(sum(losses) / len(losses))
        if wins:
            payoff_ratio = round((sum(wins) / len(wins)) / avg_loss, 4)

    # 期望值（单笔）：胜率×平均盈R - 败率×平均亏R
    expectancy = 0.0
    if wins and losses:
        expectancy = round(win_rate * (sum(wins) / len(wins))
                           - (1 - win_rate) * abs(sum(losses) / len(losses)), 4)

    # 连亏：最大连亏笔数 + 当前连亏笔数（R<=0 连续段）
    max_streak, cur_streak = 0, 0
    for r in r_list:
        if r <= 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0

    return {
        "n_trades": n,
        "n_win": len(wins),
        "win_rate": win_rate,
        "avg_r": avg_r,
        "total_r": total_r,
        "payoff_ratio": payoff_ratio,
        "max_drawdown": max_drawdown_from_r(r_list),
        "max_loss_streak": max_streak,
        "current_loss_streak": cur_streak,
        "expectancy": expectancy,
        "cum_curve": _cumsum(r_list),
    }


def _cumsum(values: list[float]) -> list[float]:
    """累计和（曲线绘制/报告用）"""
    out, acc = [], 0.0
    for v in values:
        acc += v
        out.append(round(acc, 4))
    return out


# ══════════════════════════════════════════════════════════
# 渲染层：文本报告（复刻蒙特卡洛版式）+ 图表
# ══════════════════════════════════════════════════════════

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


def _fmt_r(v: float, signed: bool = True) -> str:
    """R 值格式化：带正负号 2 位小数"""
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v:.2f}R"


def render_terminal_report(stats: dict, records: list[dict] | None = None) -> str:
    """渲染文本版 R 值曲线报告（版式复刻蒙特卡洛报告）

    白底黑字终端风格、全宽短横线分隔、三列竖线表格（6:2.5:1.5）、
    >>> 板块标题、百分比带正负号。
    """
    if not stats or stats.get("n_trades", 0) == 0:
        return ("暂无 R 值记录\n"
                "  录入：python -m 分析决策.跟踪.r_curve record <日期> <入场价> <止损价> <出场价>\n"
                "      或 python -m 分析决策.跟踪.r_curve record-r <日期> <R值>")

    W = 78
    C1, C2, C3 = 42, 20, 12
    line = "-" * W

    def hdr(name: str) -> str:
        return f">>> {name}"

    def row(name: str, value: str = "", ret: str = "") -> str:
        return f"  {_pad(name, C1 - 4)} | {_pad(value, C2 - 2, 'r')} | {_pad(ret, C3 - 2)}"

    s = stats
    out = []
    out.append(line)
    title = "R 值曲线报告（1R 等权 · 与回测口径同构）"
    out.append(title.center(W))
    out.append(line)
    out.append(row("指标", "数值", "收益/备注"))
    out.append(line)

    # 交易表现
    out.append(row(hdr("交易表现")))
    out.append(row("总笔数", f"{s['n_trades']} 笔"))
    out.append(row("胜率", f"{s['win_rate']:.1%}", f"{s['n_win']} 胜 / {s['n_trades'] - s['n_win']} 负"))
    out.append(row("平均 R", _fmt_r(s["avg_r"])))
    out.append(row("累计 R", _fmt_r(s["total_r"])))
    out.append(line)

    # 盈亏结构（老师 24 节盈亏比数列法）
    out.append(row(hdr("盈亏结构")))
    if s["payoff_ratio"] is None:
        payoff = "∞" if s["n_win"] == s["n_trades"] else "—"
    else:
        payoff = f"{s['payoff_ratio']:.2f}"
    out.append(row("盈亏比", payoff, "平均盈R ÷ 平均亏R"))
    out.append(row("期望值", _fmt_r(s["expectancy"]), "单笔期望"))
    out.append(line)

    # 风险画像
    out.append(row(hdr("风险画像")))
    out.append(row("最大回撤", f"{s['max_drawdown']:.2f}R", "累计R曲线峰值回撤"))
    out.append(row("最大连亏", f"{s['max_loss_streak']} 笔"))
    out.append(row("当前连亏", f"{s['current_loss_streak']} 笔",
                   "⚠️ 关注" if s["current_loss_streak"] >= 3 else ""))
    out.append(line)

    # 最近记录（尾部 5 条）
    if records:
        out.append(row(hdr("最近记录")))
        for rec in records[-5:]:
            note = f"  [{rec['note']}]" if rec["note"] else ""
            sym = f" {rec['symbol']}" if rec["symbol"] else ""
            out.append(row(f"{rec['date']}{sym}", _fmt_r(rec["r"]), note))
        out.append(line)

    return "\n".join(out)


def plot_r_curve(records: list[dict], save: bool = True) -> str:
    """绘制累计 R 曲线图（深色风格，与蒙特卡洛/资金曲线图一致）

    Returns:
        图片路径（记录不足或 save=False 时返回 ""）
    """
    if len(records) < 1:
        return ""

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    r_list = [rec["r"] for rec in records]
    cum = np.cumsum(r_list)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [3, 1]},
        sharex=True)
    fig.patch.set_facecolor("#0a0a0a")

    # 累计 R 曲线（1R 等权）
    ax1.plot(range(len(cum)), cum, color="#00d4aa", linewidth=1.8,
             label="累计 R")
    ax1.fill_between(range(len(cum)), cum, 0, alpha=0.1, color="#00d4aa")
    ax1.axhline(y=0, color="#888", linewidth=0.8, linestyle="--")
    ax1.set_facecolor("#141420")
    ax1.set_title("R 值曲线（累计 R · 1R 等权）", fontsize=14, color="#ccc")
    ax1.set_ylabel("累计 R")
    ax1.grid(alpha=0.1)
    ax1.legend(fontsize=9)
    ax1.set_xticks(range(len(records)))
    ax1.set_xticklabels([rec["date"][5:] for rec in records], rotation=45,
                        fontsize=8, color="#aaa")

    # 回撤（R 口径：peak - cum）
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    ax2.fill_between(range(len(dd)), dd, 0, color="#ff4d4d", alpha=0.6)
    ax2.set_facecolor("#141420")
    ax2.set_title("回撤 (R)", fontsize=12, color="#ccc")
    ax2.set_ylabel("回撤 R")
    ax2.grid(alpha=0.1)
    ax2.set_xticks(range(len(records)))
    ax2.set_xticklabels([rec["date"][5:] for rec in records], rotation=45,
                        fontsize=8, color="#aaa")

    plt.tight_layout()

    if not save:
        plt.close(fig)
        return ""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "r_curve.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
    plt.close(fig)
    return str(path)


# ══════════════════════════════════════════════════════════
# CLI：python -m 分析决策.跟踪.r_curve
# ══════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    """命令行入口

    用法:
        python -m 分析决策.跟踪.r_curve record <日期> <入场价> <止损价> <出场价> [--symbol 代码] [--note 备注]
            由价格自动算 R（做多为主；--short 做空）
        python -m 分析决策.跟踪.r_curve record-r <日期> <R值> [--symbol 代码] [--note 备注]
            直接录入 R 值（最省事）
        python -m 分析决策.跟踪.r_curve list
        python -m 分析决策.跟踪.r_curve stats [--plot]
        python -m 分析决策.跟踪.r_curve plot
        python -m 分析决策.跟踪.r_curve delete <id>
    """
    parser = argparse.ArgumentParser(prog="r_curve", description="R 值曲线：每笔交易 R 值跟踪与统计")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="录入价格三件套，自动计算 R")
    p_record.add_argument("date", type=str, help="交易日期 YYYY-MM-DD")
    p_record.add_argument("entry", type=float, help="入场价")
    p_record.add_argument("stop", type=float, help="止损价")
    p_record.add_argument("exit", type=float, help="出场价")
    p_record.add_argument("--short", action="store_true", help="做空方向（默认做多）")
    p_record.add_argument("--symbol", type=str, default="", help="股票代码")
    p_record.add_argument("--note", type=str, default="", help="备注")

    p_r = sub.add_parser("record-r", help="直接录入 R 值（最省事）")
    p_r.add_argument("date", type=str, help="交易日期 YYYY-MM-DD")
    p_r.add_argument("r", type=float, help="R 值（亏损单约 -1）")
    p_r.add_argument("--symbol", type=str, default="", help="股票代码")
    p_r.add_argument("--note", type=str, default="", help="备注")

    sub.add_parser("list", help="查看 R 值历史")
    p_stats = sub.add_parser("stats", help="统计指标 + 文本报告")
    p_stats.add_argument("--plot", action="store_true", help="同时生成图表")
    sub.add_parser("plot", help="仅生成累计 R 曲线图")
    p_del = sub.add_parser("delete", help="按 id 删除一条记录")
    p_del.add_argument("id", type=int, help="记录 id（见 list）")

    args = parser.parse_args(argv)

    if args.cmd == "record":
        direction = "short" if args.short else "long"
        try:
            r = calc_r(args.entry, args.stop, args.exit, direction)
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        res = add_record(args.date, r, entry=args.entry, stop=args.stop,
                         exit_price=args.exit, symbol=args.symbol, note=args.note)
        print(f"[R曲线] 已录入 #{res['id']} {res['date']} R={res['r']:+.3f} "
              f"({direction}, 入 {args.entry:g} / 止损 {args.stop:g} / 出 {args.exit:g})")
    elif args.cmd == "record-r":
        res = add_record(args.date, args.r, symbol=args.symbol, note=args.note)
        print(f"[R曲线] 已录入 #{res['id']} {res['date']} R={res['r']:+.3f}")
    elif args.cmd == "list":
        recs = get_records()
        if not recs:
            print("暂无 R 值记录")
            return 0
        print(f"\n=== R 值记录 ({len(recs)} 笔) ===")
        print(f"  {'id':>3}  {'日期':<12} {'R':>8}  明细")
        cum = 0.0
        for rec in recs:
            cum += rec["r"]
            detail = []
            if rec["symbol"]:
                detail.append(rec["symbol"])
            if rec["entry"]:
                detail.append(f"入{rec['entry']}/止{rec['stop']}/出{rec['exit']}")
            if rec["note"]:
                detail.append(f"[{rec['note']}]")
            print(f"  {rec['id']:>3}  {rec['date']:<12} {rec['r']:>+8.3f}  "
                  f"{' '.join(detail)}  (累计 {cum:+.2f}R)")
    elif args.cmd == "stats":
        recs = get_records()
        if not recs:
            print("暂无 R 值记录，先录入：\n"
                  "  python -m 分析决策.跟踪.r_curve record <日期> <入场价> <止损价> <出场价>\n"
                  "  python -m 分析决策.跟踪.r_curve record-r <日期> <R值>")
            return 0
        stats = compute_stats(recs)
        print()
        print(render_terminal_report(stats, recs))
        if args.plot:
            path = plot_r_curve(recs)
            print(f"\n图表已保存: {path}")
    elif args.cmd == "plot":
        recs = get_records()
        path = plot_r_curve(recs)
        if path:
            print(f"图表已保存: {path}")
        else:
            print("暂无 R 值记录，无法绘图")
    elif args.cmd == "delete":
        if delete_record(args.id):
            print(f"[R曲线] 已删除 #{args.id}")
        else:
            print(f"❌ 未找到 #{args.id}")
            return 1
    return 0


if __name__ == "__main__":
    # Windows GBK 终端编码保护（编码失败不影响功能）
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001, S110
            pass
    raise SystemExit(main())
