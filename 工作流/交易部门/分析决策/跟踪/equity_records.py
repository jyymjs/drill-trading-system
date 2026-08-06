"""实盘净值记录（2026-08-07 老板拍板"从今天起记录曲线"· T-030 落地）

实盘线净值登记：
  - 每日净值登记（账户净值 = 现金 + 持仓市值，总资产口径——口径铁律）
  - 注入登记（每月 3000 工资注入，capital.json 无历史 → 本账本承载注入历史）
  - 修正收益率：剔除注入后的真实收益率（净值曲线扣注入事件）

账本：journal/equity_records.csv（id, date, equity, cash, market_value, inject, note）

口径（铁律 08-06）：
  - 净值一律总资产口径（现金 + 持仓市值）
  - 修正收益率 = (终值净值 − Σ注入) / 初始资金 − 1（注入不虚增收益）
  - 未修正收益率 = 终值净值 / 初始资金 − 1（含注入，仅供对照）

用法（main.py track 接入）:
  track equity-add 2026-08-07 5600          # 每日净值登记
  track inject 2026-08-05 3000              # 注入登记
  track equity-report                       # 修正收益率报告（含图）
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"
EQUITY_CSV = _JOURNAL_DIR / "equity_records.csv"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
EQUITY_COLUMNS = ["id", "date", "equity", "cash", "market_value", "inject", "note"]

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _ensure_table() -> None:
    _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    if not EQUITY_CSV.exists():
        with open(EQUITY_CSV, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(EQUITY_COLUMNS)


def add_record(date: str, equity: float, cash: float | None = None,
               market_value: float | None = None, inject: float = 0.0,
               note: str = "") -> int:
    """登记一条净值记录（同日已有 → 覆盖更新）

    Args:
        date: 日期 YYYY-MM-DD
        equity: 账户净值（总资产口径，现金+持仓市值）
        cash/market_value: 可选明细（现金/市值分解）
        inject: 当日注入金额（默认 0；>0 = 注入登记日）
        note: 备注

    Returns:
        记录 id（自增）
    """
    _ensure_table()
    rows = get_records()
    nxt = max([int(r["id"]) for r in rows], default=0) + 1
    rec = {"id": str(nxt), "date": date,
           "equity": f"{float(equity):.2f}",
           "cash": f"{float(cash):.2f}" if cash is not None else "",
           "market_value": f"{float(market_value):.2f}" if market_value is not None else "",
           "inject": f"{float(inject):.2f}" if inject else "0.00",
           "note": note}
    # 同日覆盖（防重复登记）——保留既有注入（2026-08-07 修复：原实现丢同日注入）
    old_inject = sum(float(r.get("inject") or 0) for r in rows if r["date"] == date)
    rows = [r for r in rows if r["date"] != date]
    rec["inject"] = f"{float(inject) + old_inject:.2f}"
    rows.append(rec)
    rows.sort(key=lambda r: r["date"])
    with open(EQUITY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EQUITY_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return nxt


def add_inject(date: str, amount: float, note: str = "每月工资注入") -> int:
    """注入登记（独立注入事件；equity 留空待当日净值补记）"""
    _ensure_table()
    rows = get_records()
    nxt = max([int(r["id"]) for r in rows], default=0) + 1
    rec = {"id": str(nxt), "date": date, "equity": "",
           "cash": "", "market_value": "",
           "inject": f"{float(amount):.2f}", "note": note}
    # 同日已有净值记录 → 合并注入到该行；否则独立注入行
    for r in rows:
        if r["date"] == date:
            r["inject"] = f"{float(r['inject'] or 0) + float(amount):.2f}"
            rec = None
            break
    if rec is not None:
        rows.append(rec)
    rows.sort(key=lambda r: r["date"])
    with open(EQUITY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EQUITY_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return nxt


def get_records() -> list[dict]:
    """全部净值记录（按日期升序）"""
    _ensure_table()
    with open(EQUITY_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _initial_capital() -> float:
    """初始资金 = 首条净值记录的 equity；无记录 → capital.json（5600）"""
    rows = [r for r in get_records() if r["equity"]]
    if rows:
        return float(rows[0]["equity"])
    try:
        from 分析决策.风控.capital import get_capital
        return float(get_capital())
    except Exception:  # noqa: BLE001
        return 5600.0


def corrected_return() -> dict:
    """修正收益率（剔除注入）与对照指标

    口径：
      - 初始 = 首条净值记录 equity（或 5600）
      - 注入合计 = Σ inject
      - 修正终值 = 最后净值 − 注入合计（注入剔除）
      - 修正收益率 = 修正终值 / 初始 − 1（真实策略收益）
      - 未修正收益率 = 最后净值 / 初始 − 1（含注入，对照）
    """
    rows = [r for r in get_records() if r["equity"]]
    if not rows:
        return {"error": "无净值记录"}
    initial = _initial_capital()
    last_equity = float(rows[-1]["equity"])
    total_inject = sum(float(r.get("inject") or 0) for r in get_records())
    corrected = (last_equity - total_inject) / initial - 1.0
    uncorrected = last_equity / initial - 1.0
    return {
        "initial": initial,
        "last_equity": last_equity,
        "total_inject": total_inject,
        "corrected_ret": corrected,
        "uncorrected_ret": uncorrected,
        "n_records": len(rows),
    }


def render_report() -> str:
    """净值/修正收益率文本报告"""
    cr = corrected_return()
    if "error" in cr:
        return f"净值记录为空——先 `track equity-add <日期> <净值>` 登记"
    out = [
        "═" * 46,
        "  实盘净值曲线报告（总资产口径 · 口径铁律 08-06）",
        "═" * 46,
        f"  初始资金:      {cr['initial']:>10,.2f} 元",
        f"  当前净值:      {cr['last_equity']:>10,.2f} 元",
        f"  累计注入:      {cr['total_inject']:>10,.2f} 元",
        f"  修正收益率:    {cr['corrected_ret']:>+9.1%}  （剔除注入，真实策略收益）",
        f"  未修正收益率:  {cr['uncorrected_ret']:>+9.1%}  （含注入，对照）",
        "─" * 46,
        "  净值记录明细:",
    ]
    for r in get_records():
        eq = f"{float(r['equity']):>10,.2f}" if r.get("equity") else "         —"
        inj = f"{float(r['inject']):>9,.2f}" if float(r.get("inject") or 0) else "      —"
        out.append(f"    {r['date']}  净值 {eq}  注入 {inj}  {r.get('note', '')}")
    out.append("═" * 46)
    return "\n".join(out)


def plot_equity_curve(save: bool = True) -> str:
    """净值曲线图（含注入标记点）→ analysis/output/equity_curve_live.png"""
    rows = [r for r in get_records() if r["equity"]]
    if not rows:
        return ""
    dates = [r["date"] for r in rows]
    equities = [float(r["equity"]) for r in rows]
    injects = [float(r.get("inject") or 0) for r in rows]

    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(rows))
    ax.plot(x, equities, color="#00a0e9", linewidth=2, marker="o", markersize=4,
            label="账户净值（总资产口径）")
    inj_idx = [i for i, v in enumerate(injects) if v > 0]
    if inj_idx:
        ax.scatter([x[i] for i in inj_idx], [equities[i] for i in inj_idx],
                   color="#ff4d4d", s=60, zorder=5, label="注入日")
    ax.axhline(5600, color="#999", linewidth=1, linestyle="--", label="初始 5,600")
    ax.set_xticks(x)
    ax.set_xticklabels(dates, rotation=45, fontsize=8)
    ax.set_ylabel("净值（元）")
    ax.set_title("实盘净值曲线（总资产口径）", fontsize=14)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "equity_curve_live.png"
    if save:
        fig.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return str(path)
    plt.close(fig)
    return ""


def main() -> int:
    """CLI：python -m 分析决策.跟踪.equity_records <date> <equity> [--inject N]"""
    args = sys.argv[1:]
    if not args or args[0] in ("report", "stats"):
        print(render_report())
        return 0
    if args[0] == "plot":
        print(plot_equity_curve())
        return 0
    if len(args) >= 2:
        date, equity = args[0], float(args[1])
        inject = 0.0
        if "--inject" in args:
            inject = float(args[args.index("--inject") + 1])
        rid = add_record(date, equity, inject=inject)
        print(f"已登记 #{rid} {date} 净值 {equity:,.2f} 元"
              + (f"（含注入 {inject:,.2f}）" if inject else ""))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
