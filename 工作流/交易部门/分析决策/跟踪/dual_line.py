"""双线对照（2026-08-07 老板拍板"模拟策略的双线"· 实盘线 vs 虚拟盘线）

两线验证框架（trading-account-profile 记忆）：
  - 实盘线：真金白银（trade_journal.csv 的 r_multiple）——反映真实执行
  - 虚拟盘线：模拟盘（sim_journal.csv 的 r_multiple，status=closed）——反映策略本身
  - 双线并排：差距 = 执行层损耗（摩擦/人工/心理），策略本身质量看虚拟盘线

输出：
  - 双线累计 R 对照图（叠图）→ analysis/output/dual_line.png
  - 对照表：笔数/avgR/胜率/盈亏比/最大连败/执行一致性（两线差）
  - 白话结论：实盘 vs 模拟差距在哪（哪个环节损耗）

用法：
  python -m 分析决策.跟踪.dual_line            # 报告 + 图
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _load_csv(name: str) -> list[dict]:
    p = _JOURNAL_DIR / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _r_series(rows: list[dict], closed_only: bool = True) -> list[tuple[str, float]]:
    """(date, r) 序列：已平仓成交，按日期升序"""
    out = []
    for r in rows:
        if closed_only and r.get("status") not in (None, "", "closed"):
            continue
        try:
            rm = float(r.get("r_multiple") or 0)
        except (TypeError, ValueError):
            continue
        if rm == 0 and r.get("status") == "open":
            continue
        out.append((str(r.get("date", ""))[:10], rm))
    out.sort(key=lambda x: x[0])
    return out


def _stats(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0}
    gains = sum(x for x in rs if x > 0)
    loss = abs(sum(x for x in rs if x < 0))
    streak = cur = 0
    for x in rs:
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    return {
        "n": len(rs),
        "avg_r": float(np.mean(rs)),
        "win_rate": float(np.mean([x > 0 for x in rs])),
        "profit_factor": gains / loss if loss > 0 else float("inf"),
        "max_streak": streak,
        "cum_r": float(sum(rs)),
    }


def compare() -> dict:
    """双线对照计算：实盘线 vs 虚拟盘线"""
    live = _r_series(_load_csv("trade_journal.csv"))
    sim = _r_series(_load_csv("sim_journal.csv"), closed_only=True)
    live_rs = [r for _, r in live]
    sim_rs = [r for _, r in sim]
    ls, ss = _stats(live_rs), _stats(sim_rs)
    exec_gap = (ss["avg_r"] - ls["avg_r"]) if ls["n"] and ss["n"] else 0.0
    return {"live": ls, "sim": ss, "exec_gap": exec_gap,
            "n_live": ls["n"], "n_sim": ss["n"]}


def render_report(c: dict) -> str:
    out = [
        "═" * 46,
        "  双线对照（实盘线 vs 虚拟盘线 · 2026-08-07 起）",
        "═" * 46,
        f"  {'指标':<10}{'实盘线':>10}{'虚拟盘线':>12}{'差距':>10}",
        "─" * 46,
    ]
    def row(name, key, fmt="{:+.3f}"):
        lv, sv = c["live"].get(key), c["sim"].get(key)
        if lv is None:
            return
        out.append(f"  {name:<10}{fmt.format(lv):>10}{fmt.format(sv):>12}"
                   f"{fmt.format(sv - lv if sv is not None else 0):>10}")
    row("笔数", "n", "{:>9.0f}")
    row("avgR", "avg_r")
    row("胜率", "win_rate", "{:+.1%}")
    row("盈亏比", "profit_factor", "{:>9.2f}")
    row("最大连败", "max_streak", "{:>9.0f}")
    row("累计R", "cum_r")
    if c["n_live"] == 0 and c["n_sim"] == 0:
        out.append("  （两线均无已平仓记录——虚拟盘线随 sim_check 自动记账，实盘线随成交录入）")
    elif c["n_live"] == 0:
        out.append("  （实盘线暂无记录——首笔成交后录入；虚拟盘线已开始积累）")
    out.append("─" * 46)
    if c["n_live"] and c["n_sim"]:
        out.append(f"  执行一致性：虚拟盘线 avgR {c['sim']['avg_r']:+.3f} vs 实盘线 "
                   f"{c['live']['avg_r']:+.3f}，差 {c['exec_gap']:+.3f}R/笔"
                   "（负 = 实盘执行损耗，正 = 实盘选票更好）")
    out.append("═" * 46)
    return "\n".join(out)


def plot_dual_line(save: bool = True) -> str:
    """双线累计 R 叠图 → analysis/output/dual_line.png"""
    live = _r_series(_load_csv("trade_journal.csv"))
    sim = _r_series(_load_csv("sim_journal.csv"), closed_only=True)
    fig, ax = plt.subplots(figsize=(12, 6))
    if sim:
        cum = np.cumsum([r for _, r in sim])
        ax.plot(range(len(cum)), cum, color="#00a0e9", linewidth=2,
                label=f"虚拟盘线（{len(sim)} 笔，累计 {cum[-1]:+.1f}R）")
    if live:
        cum = np.cumsum([r for _, r in live])
        ax.plot(range(len(cum)), cum, color="#ff6a00", linewidth=2.4,
                label=f"实盘线（{len(live)} 笔，累计 {cum[-1]:+.1f}R）")
    ax.axhline(0, color="#999", linewidth=1, linestyle="--")
    ax.set_xlabel("已平仓笔数（按日期序）")
    ax.set_ylabel("累计 R")
    ax.set_title("双线对照：实盘线 vs 虚拟盘线（累计 R）", fontsize=14)
    if live or sim:
        ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "dual_line.png"
    if save:
        fig.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return str(path)
    plt.close(fig)
    return ""


def main() -> int:
    c = compare()
    print(render_report(c))
    p = plot_dual_line()
    print(f"双线图 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
