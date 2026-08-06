"""统一可视化仪表盘（2026-08-07 老板指令：回测/蒙卡/资金曲线全图表化）

深色统一主题（老板版式家族）——回测 + 蒙特卡洛 + 实盘全部图表：

A. 回测图表（8 张，数据 = 当前策略生产链路模拟）：
   资金曲线 / 回撤时间序列 / 成交 R 分布 / 月度收益 / 市场分段 / 量比分桶 /
   去尾稳定性 / R 档位分布
B. 蒙特卡洛补充（3 张，simulate 结果）：
   终值分布 / 回撤分布 / 连败分布（净值路径堆叠 = 双件套已有，不重造）
C. 实盘图表（3 张，journal 账本）：
   实盘净值曲线 / 双线对照 / R 值曲线

分组：
  - plot_backtest_group()：A+B（3 年数据固定，生成一次即可）
  - plot_live_group()：C（轻量秒级，每日随扫描更新）
  - main()：全量（python -m 分析决策.可视化.dashboard）

输出：产出/输出/图表/图表-{名}.png + 总览报告
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (_ROOT, _ROOT / "项目"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# ── 统一深色主题（老板版式家族）──
BG = "#121212"
PANEL = "#1a1a1a"
TEXT = "#e8e8e8"
GRID = "#3a3a3a"
BLUE = "#00a0e9"      # 主系列
ORANGE = "#ff6a00"    # 实盘/对照
RED = "#ff4d4d"       # 负/风险
GREEN = "#00d48a"     # 正/盈利
CYAN = "#00d4aa"
PURPLE = "#b07cff"

CHART_DIR = _ROOT / "产出" / "输出" / "图表"
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"
CAPITAL = 5600.0
RISK_RATIO = 0.02
MAX_POS = 3


def _style_ax(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_facecolor(PANEL)
    ax.set_title(title, color=TEXT, fontsize=13, pad=10)
    ax.set_xlabel(xlabel, color=TEXT, fontsize=10)
    ax.set_ylabel(ylabel, color=TEXT, fontsize=10)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.grid(color=GRID, linewidth=0.4, linestyle=":", alpha=0.6)
    for spine in ax.spines.values():
        spine.set_color("#555555")


def _new_fig(w=12, h=6):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(BG)
    return fig, ax


_SUBDIR: str | None = None   # 测试归档子目录（--subdir 设置）


def _save(fig, name: str) -> str:
    target = CHART_DIR / _SUBDIR if _SUBDIR else CHART_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"图表-{name}.png"
    fig.savefig(path, dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return str(path)


def _load_signal_df(smoke: int | None = None):
    import pandas as pd
    df = pd.read_csv(DEFAULT_SIGNALS, encoding="utf-8-sig")
    tr = df[(df["mode"] == "prebreak") & (df["triggered_20d"] == 1)]
    if smoke:
        tr = tr.head(smoke)
    return tr


def _capital_sim(tr, klines=None):
    """生产链路资金模拟（rebuild delay2 + half_phase + risk_mid）"""
    from 回测系统.confirm_replay import (load_kline_cache, make_confirm_fn,
                                          rebuild_exit_for_mode)
    from 回测系统.sim_capital import simulate_capital
    if klines is None:
        klines = load_kline_cache([str(c) for c in tr["code"].unique()])
    sig, _ = rebuild_exit_for_mode(tr, klines, "delay2", mode="prebreak",
                                   hold="20d")
    sim = simulate_capital(sig, CAPITAL, RISK_RATIO, max_positions=MAX_POS,
                           mode="prebreak", hold="20d", grades=["S"],
                           half_phase=True, confirm_fn=make_confirm_fn("delay2"),
                           same_day_order="risk_mid")
    return sim, sig, klines


# ═══ A. 回测图表（8 张）═══

def plot_equity_curve_backtest(sim) -> str:
    """1. 资金曲线（模拟账户，含注入标记）"""
    eq = sim["equity"]
    fig, ax = _new_fig()
    dates = [str(d)[:10] for d in eq["date"]]
    bal = eq["balance"].astype(float)
    x = np.arange(len(eq))
    ax.plot(x, bal, color=BLUE, linewidth=2, marker="o", markersize=3,
            label="账户余额（已实现）")
    if "inject" in eq.columns:
        inj = [i for i, v in enumerate(eq["inject"]) if v]
        if inj:
            ax.scatter(inj, [bal[i] for i in inj], color=RED, s=50, zorder=5,
                       label="注入日")
    ax.axhline(CAPITAL, color="#999", linewidth=1, linestyle="--",
               label=f"初始 {CAPITAL:,.0f}")
    ax.set_xticks(x[:: max(1, len(x) // 12)])
    ax.set_xticklabels([dates[i] for i in range(0, len(x), max(1, len(x) // 12))],
                       rotation=45, fontsize=8)
    _style_ax(ax, "回测资金曲线（5600×2.0%×3仓 · risk_mid 排序）", "时间", "余额（元）")
    ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, "回测资金曲线")


def plot_drawdown_series(sim) -> str:
    """2. 回撤时间序列（总资产口径快照回撤）"""
    eq = sim["equity"]
    bal = eq["balance"].astype(float).to_numpy()
    peak = np.maximum.accumulate(bal)
    dd = (bal - peak) / peak * 100
    fig, ax = _new_fig()
    x = np.arange(len(eq))
    ax.fill_between(x, dd, 0, color=RED, alpha=0.55)
    ax.plot(x, dd, color=RED, linewidth=1.2)
    ax.axhline(0, color="#999", linewidth=1)
    ax.set_ylim(dd.min() * 1.15, 1)
    dates = [str(d)[:10] for d in eq["date"]]
    ax.set_xticks(x[:: max(1, len(x) // 12)])
    ax.set_xticklabels([dates[i] for i in range(0, len(x), max(1, len(x) // 12))],
                       rotation=45, fontsize=8)
    _style_ax(ax, f"回撤时间序列（最深 {dd.min():.1f}%）", "时间", "回撤（%）")
    return _save(fig, "回撤时间序列")


def plot_r_distribution(sim) -> str:
    """3. 成交 R 分布直方图"""
    rs = [float(t["r"]) for t in sim["trades"]]
    fig, ax = _new_fig()
    ax.hist(rs, bins=40, color=BLUE, alpha=0.85, edgecolor=BG)
    avg = float(np.mean(rs))
    ax.axvline(0, color=RED, linewidth=1.2, linestyle="--")
    ax.axvline(avg, color=GREEN, linewidth=1.6, linestyle="--",
               label=f"avgR {avg:+.3f}")
    ax.axvline(np.percentile(rs, 90), color=ORANGE, linewidth=1.2,
               linestyle=":", label=f"P90 {np.percentile(rs, 90):+.1f}R")
    _style_ax(ax, f"成交 R 分布（{len(rs)} 笔 · 偏度 {float(__import__('pandas').Series(rs).skew()):+.2f}）",
              "R", "笔数")
    ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, "成交R分布")


def plot_monthly_pnl(sim) -> str:
    """4. 月度收益柱状图（成交 pnl 按月）"""
    import pandas as pd
    rows = [{"m": str(t["exit_date"])[:7], "pnl": float(t["pnl"])}
            for t in sim["trades"] if t.get("exit_date")]
    df = pd.DataFrame(rows)
    if df.empty:
        return ""
    m = df.groupby("m")["pnl"].sum()
    fig, ax = _new_fig()
    colors = [GREEN if v >= 0 else RED for v in m.values]
    ax.bar(range(len(m)), m.values, color=colors, width=0.7)
    ax.axhline(0, color="#999", linewidth=1)
    ax.set_xticks(range(len(m)))
    ax.set_xticklabels(m.index, rotation=60, fontsize=8)
    win_m = int((m > 0).sum())
    _style_ax(ax, f"月度收益（{len(m)} 个月 · 盈利月 {win_m} 个）", "月份", "盈亏（元）")
    return _save(fig, "月度收益")


def plot_market_regime(tr) -> str:
    """5. 市场分段表现（牛/熊/震荡 avgR + 胜率双轴）"""
    from 回测系统.regime_segment_compare import attach_regime
    df = attach_regime(tr.copy())
    segs = ["牛", "熊", "震荡"]
    avg_r, win = [], []
    for s in segs:
        sub = df.loc[df["regime"] == s, "r_20d"].astype(float)
        avg_r.append(float(sub.mean()) if len(sub) else 0.0)
        win.append(float((sub > 0).mean()) if len(sub) else 0.0)
    fig, ax = _new_fig()
    x = np.arange(len(segs))
    bars = ax.bar(x - 0.18, avg_r, width=0.36, color=BLUE, label="avgR")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s}\n({int((df['regime']==s).sum())} 笔)" for s in segs])
    ax2 = ax.twinx()
    ax2.plot(x + 0.18, win, color=ORANGE, marker="o", linewidth=2,
             label="胜率")
    ax2.set_ylim(0, 1)
    ax2.tick_params(colors=ORANGE)
    for b, v in zip(bars, avg_r):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:+.2f}",
                ha="center", color=TEXT, fontsize=9)
    _style_ax(ax, "市场分段表现（信号层）", "市场段", "avgR")
    lines = [ax.get_legend_handles_labels()[0][0], ax2.get_legend_handles_labels()[0][0]]
    ax.legend(lines, ["avgR", "胜率"], facecolor="#000000", labelcolor=TEXT,
              framealpha=0.6)
    return _save(fig, "市场分段")


def plot_vol_buckets(tr, klines) -> str:
    """6. 量比分桶（vol_ratio 复算）"""
    from 回测系统.sort_compare import enrich_sort_cols
    df = enrich_sort_cols(tr.copy(), klines)
    labels, avgs, ns = [], [], []
    for lo, hi in ((0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, np.inf)):
        m = df["vol_ratio"].notna() & (df["vol_ratio"] >= lo) & (df["vol_ratio"] < hi)
        if not m.any():
            continue
        sub = df.loc[m, "r_20d"].astype(float)
        labels.append(f"{lo}~{hi if np.isfinite(hi) else '+'}")
        avgs.append(float(sub.mean()))
        ns.append(int(m.sum()))
    fig, ax = _new_fig()
    x = np.arange(len(labels))
    bars = ax.bar(x, avgs, color=BLUE, width=0.55)
    for b, v, n in zip(bars, avgs, ns):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:+.3f}（{n}笔）",
                ha="center", color=TEXT, fontsize=9)
    ax.axhline(0, color="#999", linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    _style_ax(ax, "量比分桶（触发日量比 vs avgR）", "量比区间", "avgR")
    return _save(fig, "量比分桶")


def plot_tail_stability(tr) -> str:
    """7. 去尾稳定性曲线（Top% → avgR 衰减）"""
    from 回测系统.monte_carlo_dist import tail_stability
    rs = tr["r_20d"].astype(float).tolist()
    tail = tail_stability(rs)
    pcts = [r["pct"] * 100 for r in tail]
    avgs = [r["avg_r"] for r in tail]
    crashed = [r["crashed"] for r in tail]
    fig, ax = _new_fig()
    ax.plot(pcts, avgs, color=BLUE, marker="o", linewidth=2)
    for p, a, c in zip(pcts, avgs, crashed):
        ax.annotate(f"{a:+.3f}{' [依赖大赢家]' if c else ''}", (p, a),
                    textcoords="offset points", xytext=(0, 8), ha="center",
                    color=RED if c else TEXT, fontsize=9)
    ax.axhline(0, color=RED, linewidth=1, linestyle="--")
    ax.set_xticks(pcts)
    ax.set_xticklabels([f"Top {p:.0f}%" for p in pcts])
    _style_ax(ax, "去尾稳定性（去掉最大收益 Top% 后 avgR）", "去掉比例", "avgR")
    return _save(fig, "去尾稳定性")


def plot_r_buckets(tr) -> str:
    """8. R 档位分布（横向柱状）"""
    from 回测系统.monte_carlo_dist import r_bucket_dist
    rs = tr["r_20d"].astype(float).tolist()
    buckets = r_bucket_dist(rs)
    labels = [b["label"] for b in buckets]
    totals = [b["total_r"] for b in buckets]
    ns = [b["n"] for b in buckets]
    fig, ax = _new_fig(w=11)
    colors = [RED if t < 0 else GREEN for t in totals]
    y = np.arange(len(labels))[::-1]
    ax.barh(y, totals, color=colors, height=0.6)
    for yi, t, n in zip(y, totals, ns):
        ax.text(t + (2 if t >= 0 else -2), yi, f"{t:+.1f}R（{n}笔）",
                va="center", ha="left" if t >= 0 else "right",
                color=TEXT, fontsize=9)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.axvline(0, color="#999", linewidth=1)
    _style_ax(ax, "R 档位分布（累计 R 贡献）", "累计 R")
    return _save(fig, "R档位分布")


# ═══ B. 蒙特卡洛补充（3 张）═══

def plot_mc_final_equities(mc, risk_amt: float, tag: str = "资金层") -> str:
    """9. 蒙卡终值分布直方图"""
    fin = mc["final_equities"] * risk_amt + CAPITAL
    fig, ax = _new_fig()
    ax.hist(fin, bins=60, color=BLUE, alpha=0.85, edgecolor=BG)
    for q, c in ((1, RED), (50, GREEN), (99, ORANGE)):
        v = float(np.percentile(fin, q))
        ax.axvline(v, color=c, linewidth=1.4, linestyle="--",
                   label=f"P{q} {v:,.0f} 元")
    _style_ax(ax, f"蒙卡终值分布（{tag} · 10000 次）", "终值（元）", "场景数")
    ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, f"蒙卡终值分布-{tag}")


def plot_mc_drawdowns(mc, risk_amt: float, tag: str = "资金层") -> str:
    """10. 蒙卡回撤分布直方图"""
    dds = mc["max_drawdowns"] * risk_amt
    fig, ax = _new_fig()
    ax.hist(dds, bins=50, color=RED, alpha=0.8, edgecolor=BG)
    p95 = float(np.percentile(dds, 95))
    ax.axvline(p95, color=ORANGE, linewidth=1.5, linestyle="--",
               label=f"P95 {p95:,.0f} 元")
    _style_ax(ax, f"蒙卡最大回撤分布（{tag}）", "最大回撤（元）", "场景数")
    ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, f"蒙卡回撤分布-{tag}")


def plot_mc_streaks(mc, tag: str = "资金层") -> str:
    """11. 蒙卡连败分布直方图"""
    streaks = mc["streaks"]
    fig, ax = _new_fig()
    ax.hist(streaks, bins=range(0, int(streaks.max()) + 2), color=PURPLE,
            alpha=0.85, edgecolor=BG)
    p99 = float(np.percentile(streaks, 99))
    ax.axvline(p99, color=ORANGE, linewidth=1.5, linestyle="--",
               label=f"P99 {p99:.0f} 连亏")
    _style_ax(ax, f"蒙卡最大连败分布（{tag}）", "最大连败（笔）", "场景数")
    ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, f"蒙卡连败分布-{tag}")


# ═══ C. 实盘图表（3 张，深色）═══

def plot_live_equity() -> str:
    """12. 实盘净值曲线（深色版）"""
    from 分析决策.跟踪.equity_records import get_records
    rows = [r for r in get_records() if r["equity"]]
    if not rows:
        return ""
    fig, ax = _new_fig()
    dates = [r["date"] for r in rows]
    vals = [float(r["equity"]) for r in rows]
    inj = [float(r.get("inject") or 0) for r in rows]
    x = np.arange(len(rows))
    ax.plot(x, vals, color=BLUE, linewidth=2.2, marker="o", markersize=4,
            label="账户净值（总资产口径）")
    inj_idx = [i for i, v in enumerate(inj) if v > 0]
    if inj_idx:
        ax.scatter(inj_idx, [vals[i] for i in inj_idx], color=RED, s=70,
                   zorder=5, label="注入日")
    ax.axhline(CAPITAL, color="#999", linewidth=1, linestyle="--",
               label="初始 5,600")
    ax.set_xticks(x)
    ax.set_xticklabels(dates, rotation=45, fontsize=8)
    _style_ax(ax, "实盘净值曲线（总资产口径）", "日期", "净值（元）")
    ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, "实盘净值曲线")


def plot_live_dual_line() -> str:
    """13. 双线对照（深色版）"""
    from 分析决策.跟踪.dual_line import _load_csv, _r_series
    live = _r_series(_load_csv("trade_journal.csv"))
    sim = _r_series(_load_csv("sim_journal.csv"), closed_only=True)
    fig, ax = _new_fig()
    if sim:
        cum = np.cumsum([r for _, r in sim])
        ax.plot(range(len(cum)), cum, color=BLUE, linewidth=2,
                label=f"虚拟盘线（{len(sim)} 笔，{cum[-1]:+.1f}R）")
    if live:
        cum = np.cumsum([r for _, r in live])
        ax.plot(range(len(cum)), cum, color=ORANGE, linewidth=2.4,
                label=f"实盘线（{len(live)} 笔，{cum[-1]:+.1f}R）")
    ax.axhline(0, color="#999", linewidth=1, linestyle="--")
    _style_ax(ax, "双线对照：实盘线 vs 虚拟盘线（累计 R）", "已平仓笔数", "累计 R")
    if live or sim:
        ax.legend(facecolor="#000000", labelcolor=TEXT, framealpha=0.6)
    return _save(fig, "双线对照")


def plot_live_r_curve() -> str:
    """14. R 值曲线（深色版）"""
    from 分析决策.跟踪.r_curve import get_records
    rows = get_records()
    if not rows:
        return ""
    dates = [r["date"] for r in rows]
    rs = [float(r["r"]) for r in rows]
    cum = np.cumsum(rs)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.patch.set_facecolor(BG)
    x = np.arange(len(rows))
    ax1.plot(x, cum, color=CYAN, linewidth=2.2, marker="o", markersize=4)
    ax1.axhline(0, color="#999", linewidth=1, linestyle="--")
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    ax2.fill_between(x, dd, 0, color=RED, alpha=0.55)
    for axx in (ax1, ax2):
        _style_ax(axx, "", "", "")
    ax1.set_title(f"R 值曲线（累计 {cum[-1]:+.1f}R）", color=TEXT, fontsize=13)
    ax2.set_title("R 回撤", color=TEXT, fontsize=12)
    ax1.tick_params(colors=TEXT, labelsize=9)
    ax2.tick_params(colors=TEXT, labelsize=9)
    ax1.set_xticks(x[:: max(1, len(x) // 10)])
    ax1.set_xticklabels([dates[i] for i in range(0, len(x), max(1, len(x) // 10))],
                        rotation=45, fontsize=8)
    return _save(fig, "R值曲线")


# ═══ 组执行 ═══

def plot_backtest_group(smoke: int | None = None) -> list[str]:
    """A+B 组：回测 8 图 + 蒙卡 3 图（3 年数据固定，生成一次）"""
    from 分析决策.跟踪.monte_carlo import simulate
    from 回测系统.monte_carlo_c23 import capital_trade_r
    out = []
    tr = _load_signal_df(smoke)
    print(f"[回测图表] 信号 {len(tr)} 笔 · 资金模拟…")
    sim, sig, klines = _capital_sim(tr)
    print(f"  成交 {len(sim['trades'])} 笔 · {sim['total_ret']:+.1f}%")
    out += [plot_equity_curve_backtest(sim), plot_drawdown_series(sim),
            plot_r_distribution(sim), plot_monthly_pnl(sim),
            plot_market_regime(tr), plot_vol_buckets(tr, klines),
            plot_tail_stability(tr), plot_r_buckets(tr)]
    print("[蒙卡图表] 10000 次 × 资金层…")
    rs = capital_trade_r(sim["trades"])
    avg_risk = float(np.mean([t["risk_actual"] for t in sim["trades"]])) \
        if sim["trades"] else 0.0
    mc = simulate([{"r_multiple": r} for r in rs], n_simulations=10000,
                  fee_per_trade_r=0.0)
    out += [plot_mc_final_equities(mc, avg_risk),
            plot_mc_drawdowns(mc, avg_risk), plot_mc_streaks(mc)]
    return [p for p in out if p]


def plot_live_group() -> list[str]:
    """C 组：实盘 3 图（轻量秒级，每日随扫描更新）"""
    out = [plot_live_equity(), plot_live_dual_line(), plot_live_r_curve()]
    return [p for p in out if p]


def render_overview(backtest_paths: list[str], live_paths: list[str]) -> str:
    """可视化图表总览报告"""
    lines = [
        "# 可视化图表总览（2026-08-07 老板指令 · 深色统一主题）",
        "",
        f"> 生成：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}"
        " ｜ 输出：`产出/输出/图表/` ｜ 主题：深色（老板版式家族，背景 #121212）",
        "",
        "## 一、回测图表（A 组 · 8 张）",
        "",
        "| 图 | 内容 | 数据源 |",
        "|---|---|---|",
        "| 回测资金曲线 | 5600×2.0%×3仓 模拟账户余额（含注入标记） | sim_capital 生产链路 |",
        "| 回撤时间序列 | 总资产口径回撤曲线（最深 31.2%） | equity 快照重算 |",
        "| 成交R分布 | 118 笔成交 R 直方图（avgR/偏度） | trades |",
        "| 月度收益 | 成交 pnl 按月柱状（盈利月绿/亏损月红） | trades |",
        "| 市场分段 | 牛/熊/震荡 avgR+胜率双轴 | signals + attach_regime |",
        "| 量比分桶 | 触发日量比区间 avgR（单调上升） | signals + vol_ratio 复算 |",
        "| 去尾稳定性 | Top 1/5/10% 去掉后 avgR 衰减（依赖大赢家标注） | tail_stability |",
        "| R档位分布 | 7 档累计 R 贡献（10R+ 撑起收益） | r_bucket_dist |",
        "",
        "## 二、蒙特卡洛图表（B 组 · 3 张 + 双件套 4 张已有）",
        "",
        "| 图 | 内容 | 数据源 |",
        "|---|---|---|",
        "| 蒙卡终值分布 | 10000 次终值直方图（P1/P50/P99 标注） | monte_carlo.simulate |",
        "| 蒙卡回撤分布 | 最大回撤直方图（P95 标注） | 同上 |",
        "| 蒙卡连败分布 | 最大连败直方图（P99 标注） | 同上 |",
        "| 净值路径堆叠 ×4 | 黑底路径堆叠（双件套标准，信号层/资金层/排序对照） | monte_carlo_chart |",
        "",
        "## 三、实盘图表（C 组 · 3 张，每日随扫描更新）",
        "",
        "| 图 | 内容 | 数据源 |",
        "|---|---|---|",
        "| 实盘净值曲线 | 账户净值 + 注入标记（修正收益率口径） | equity_records |",
        "| 双线对照 | 实盘线 vs 虚拟盘线累计 R | trade/sim_journal |",
        "| R值曲线 | 累计 R + 回撤双子图 | r_curve |",
        "",
        "## 四、图表文件清单",
        "",
    ]
    for p in backtest_paths + live_paths:
        lines.append(f"- `{Path(p).name}`")
    return "\n".join(lines)


def main() -> int:
    """全量生成：python -m 分析决策.可视化.dashboard [--smoke N]"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=None)
    ap.add_argument("--live-only", action="store_true", help="只更新实盘图表（每日）")
    ap.add_argument("--subdir", type=str, default=None,
                    help="测试归档子目录（如 20260807-全面测试）")
    args = ap.parse_args()
    if args.subdir:
        global _SUBDIR
        _SUBDIR = args.subdir
    if args.live_only:
        paths = plot_live_group()
        print(f"实盘图表 {len(paths)} 张已更新")
        return 0
    bt = plot_backtest_group(args.smoke)
    lv = plot_live_group()
    report = render_overview(bt, lv)
    out_path = _ROOT / "产出" / "输出" / "可视化图表总览-20260807.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"图表总览 → {out_path}")
    print(f"回测 {len(bt)} 张 + 实盘 {len(lv)} 张 已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
