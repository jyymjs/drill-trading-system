#!/usr/bin/env python3
"""资金升级回测：3000 到账引入（2026-08-08 老板拍板方案）

三组对比（同链路：prebreak/S级/20d/C23/half_phase/delay2/risk_mid/2%×3仓）：
  A 基线   5600 × 2%（108 元/笔）
  B 新资金 8401.26 × 2%（168 元/笔）
  C 成长版 8401.26 × 2% + 每月 3000 注入（风险额不随注入上调）

输出（16 维度 + 图表，数据落 JSON 供报告排版）：
  总览/可买池量化/被拒全分布/成交集重叠/回撤剖面/连败金额冲击/大赢家依赖/
  市场分段/逐年分解/蒙卡双件套/最差5%情景/100笔节奏/实际vs预算风险

用法:
  python 项目/回测系统/资金升级回测_8401.py --smoke 60   # 自检
  python 项目/回测系统/资金升级回测_8401.py              # 全量
"""
import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from 分析决策.跟踪.monte_carlo import simulate
from 回测系统.capital_dd_recalc import max_drawdown
from 回测系统.confirm_replay import load_kline_cache, make_confirm_fn, rebuild_exit_for_mode
from 回测系统.delay2_dd_recalc import build_total_asset_curve
from 回测系统.monte_carlo_c23 import capital_trade_r
from 回测系统.monte_carlo_chart import plot_equity_paths
from 回测系统.monte_carlo_style import render_scenario_report
from 回测系统.regime_segment_compare import attach_regime
from 回测系统.sim_capital import simulate_capital

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"
RISK_RATIO = 0.02
MAX_POS = 3
N_SIM = 10000
INJECT = 3000.0
CAPITAL_B = 8401.26  # 5401.26 + 3000（8/10 到账）
GROUPS = [("A", 5600.0, 0.0), ("B", CAPITAL_B, 0.0), ("C", CAPITAL_B, INJECT)]
OUT_DIR = _ROOT / "产出" / "输出"
DATA_DIR = OUT_DIR / "数据" / "资金升级回测_20260808"


def safe(fn, *a, **kw):
    """数据收集兜底：单项失败不阻断整组"""
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def collect_group(sig: pd.DataFrame, klines: dict, name: str, capital: float,
                  inject: float, risk_ratio: float = RISK_RATIO,
                  max_positions: int = MAX_POS,
                  same_day_order: str = "risk_mid") -> dict:
    """单组资金层：simulate_capital → 16 维度数据收集"""
    sim = simulate_capital(sig, capital, risk_ratio, max_positions=max_positions,
                           mode="prebreak", hold="20d", grades=["S"],
                           half_phase=True, confirm_fn=make_confirm_fn("delay2"),
                           same_day_order=same_day_order, monthly_inject=inject)
    trades = sim["trades"]
    rs = capital_trade_r(trades)
    avg_risk = float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0
    n = len(rs)
    # ① 总览
    overview = {
        "capital": capital, "risk_budget": capital * risk_ratio,
        "total_ret": sim.get("total_ret"), "total_pnl": sim.get("total_pnl"),
        "n_exec": sim.get("n_exec"), "n_all": sim.get("n_all"),
        "exec_rate": sim.get("exec_rate"), "avg_r": float(np.mean(rs)) if rs else 0,
        "winrate": float(np.mean([r > 0 for r in rs])) if rs else 0,
        "avg_risk": avg_risk, "streak_max": 0,
    }
    # ② 被拒原因全分布
    reasons = sim.get("reasons", {})
    # ③ 实际 vs 预算风险
    risk_exec = sim.get("risk_exec", {})
    # ④ 回撤剖面（总资产口径；注入版曲线不含注入事件 → 由蒙卡给 R 口径）
    dd = None
    if inject == 0 and trades:
        curve = build_total_asset_curve(trades, capital, lambda c: klines.get(c))
        dd = safe(max_drawdown, curve, capital)
        if dd and not dd.get("_error") and curve is not None and not curve.empty:
            # 回撤序列（占初始 %，供回撤叠加图）
            peak = curve["total_asset"].cummax()
            dd["_curve"] = pd.DataFrame({
                "date": curve["date"].astype(str),
                "drawdown": (peak - curve["total_asset"]).astype(float),
            })
    # ⑤ 连败
    cur = 0
    for r in rs:
        cur = cur + 1 if r < 0 else 0
        overview["streak_max"] = max(overview["streak_max"], cur)
    # ⑥ 逐年分解（按退出日期归属）
    yearly = {}
    for t in trades:
        try:
            y = str(t["exit_date"])[:4]
        except (KeyError, TypeError):
            continue
        yearly.setdefault(y, {"pnl": 0.0, "n": 0})
        yearly[y]["pnl"] += float(t["pnl"])
        yearly[y]["n"] += 1
    # ⑦ 大赢家依赖（去最佳 5% 后 avgR）
    big_winner = None
    if n >= 10:
        k = max(1, int(n * 0.05))
        trim = sorted(rs)[:-k]
        big_winner = {"trim_n": k, "avg_r_trim": float(np.mean(trim)),
                      "avg_r_all": float(np.mean(rs))}
    # ⑧ 成交集（重叠分析用）
    codes = [t.get("code", t.get("symbol", "")) for t in trades]
    # ⑨ 蒙卡资金层（10000 次）
    mc = None
    mc_p5 = None
    if rs:
        mc = simulate([{"r_multiple": r} for r in rs], n_simulations=N_SIM,
                      fee_per_trade_r=0.0)
        fin = mc["final_equities"]
        mc_p5 = float(np.percentile(fin, 5))
        # 修正收益率（C 组注入版）：(终值 − Σ注入)/初始 − 1
        inj_total = 0.0
        if inject > 0:
            inj_total = sum(float(t.get("inject", 0) or 0) for t in trades)
    # ⑩ 100 笔节奏（sim 返回若有）
    pace = {"per_year": sim.get("per_year"), "months_for_100": sim.get("months_for_100")}
    return {"overview": overview, "reasons": reasons, "risk_exec": risk_exec,
            "dd": dd, "yearly": yearly, "big_winner": big_winner,
            "codes": codes, "rs": rs, "mc": mc, "mc_p5": mc_p5,
            "pace": pace, "equity": sim["equity"] if not sim["equity"].empty else None}


def buyable_pool(tr: pd.DataFrame) -> dict:
    """可买池量化：触发信号集里每股风险 ≤ 单笔风险额/100（5600×2%→1.12，8401×2%→1.68）"""
    risk = (tr["trigger"] - tr["stop"]).astype(float)
    out = {}
    for cap, label in ((5600.0, "A(5600×2%)"), (CAPITAL_B, "B/C(8401×2%)")):
        limit = cap * RISK_RATIO / 100
        out[label] = {"limit": round(limit, 2),
                      "n_signal": int((risk <= limit).sum()),
                      "pct": float((risk <= limit).mean()) * 100}
    return out


def _slim(g: dict) -> dict:
    """payload 精简：剔除 DataFrame（equity/_curve）与 mc 大对象（保留文本版式）"""
    out = {}
    for k, v in g.items():
        if k in ("equity", "mc"):
            out[k] = None
        elif isinstance(v, dict) and "_curve" in v:
            out[k] = {kk: vv for kk, vv in v.items() if kk != "_curve"}
        else:
            out[k] = v
    return out


def draw_equity_curve(groups_data: dict, out_png: Path) -> str:
    """资金曲线：A/B 双线 + C 注入版（注入红点）——黑底老板版式"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(11, 6), facecolor="#111")
    ax.set_facecolor("#111")
    colors = {"A": "#4a90d9", "B": "#7ed07e", "C": "#e8c26a"}
    for name, g in groups_data.items():
        eq = g.get("equity")
        if eq is None or eq.empty:
            continue
        ax.plot(eq["date"].astype(str), eq["balance"].astype(float),
                label=f"{name}（初始 {g['overview']['capital']:.0f}）",
                color=colors.get(name, "#fff"), linewidth=1.6, alpha=0.95)
        if name == "C":
            inj = eq[eq.get("inject", 0).astype(float) > 0]
            if not inj.empty:
                ax.scatter(inj["date"].astype(str), inj["balance"].astype(float),
                           color="red", s=18, zorder=5, label="注入点(红)")
    ax.set_title("资金升级回测 · 模拟账户资金曲线（5600 vs 8401 vs 8401+注入）",
                 color="#ddd", fontsize=13)
    ax.legend(facecolor="#222", labelcolor="#ddd")
    ax.grid(alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#555")
    ax.tick_params(colors="#aaa")
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="#111")
    plt.close(fig)
    return str(out_png)


def draw_drawdown_curve(groups_data: dict, out_png: Path) -> str:
    """三组回撤叠加曲线（总资产口径）"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(11, 5.5), facecolor="#111")
    ax.set_facecolor("#111")
    colors = {"A": "#4a90d9", "B": "#7ed07e", "C": "#e8c26a"}
    for name, g in groups_data.items():
        dd = g.get("dd")
        curve = dd.get("_curve") if dd else None
        if curve is None or curve.empty or dd.get("_error"):
            continue
        ax.plot(curve["date"], (curve["drawdown"] / g["overview"]["capital"] * 100),
                label=f"{name} 回撤%", color=colors.get(name, "#fff"), linewidth=1.4)
    ax.set_title("资金升级回测 · 回撤剖面对比（占初始资金 %）", color="#ddd", fontsize=13)
    ax.legend(facecolor="#222", labelcolor="#ddd")
    ax.grid(alpha=0.25)
    ax.tick_params(colors="#aaa")
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="#111")
    plt.close(fig)
    return str(out_png)


def draw_r_hist(groups_data: dict, out_png: Path) -> str:
    """三组成交 R 分布直方图叠放"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor="#111")
    ax.set_facecolor("#111")
    colors = {"A": "#4a90d9", "B": "#7ed07e", "C": "#e8c26a"}
    for name, g in groups_data.items():
        rs = g.get("rs") or []
        if rs:
            ax.hist(rs, bins=40, alpha=0.45, label=f"{name}（{len(rs)}笔）",
                    color=colors.get(name), edgecolor="none")
    ax.axvline(0, color="#ff6b6b", linewidth=1.2, linestyle="--")
    ax.set_title("资金升级回测 · 成交 R 分布对比", color="#ddd", fontsize=13)
    ax.legend(facecolor="#222", labelcolor="#ddd")
    ax.tick_params(colors="#aaa")
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="#111")
    plt.close(fig)
    return str(out_png)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    ap.add_argument("--smoke", type=int, default=None, help="自检：只处理前 N 笔触发")
    ap.add_argument("--extra-groups", default="",
                    help="追加组：name,capital,inject[,risk_ratio[,max_positions]] 分号分隔"
                         "（如 B2,8401.26,0,0.012855 / G4,8401.26,0,0.02,4）")
    ap.add_argument("--tag", default="20260808",
                    help="输出目录后缀（完整周期用 2019fullcycle，避免覆盖 3 年数据）")
    ap.add_argument("--order", default="risk_mid",
                    help="同日候选排序（risk_mid/dist_asc，P2-5 对比用）")
    args = ap.parse_args()

    global DATA_DIR
    DATA_DIR = OUT_DIR / "数据" / f"资金升级回测_{args.tag}"

    global GROUPS
    if args.extra_groups:
        for spec in args.extra_groups.split(";"):
            parts = [p.strip() for p in spec.split(",")]
            name, capital, inject = parts[0], float(parts[1]), float(parts[2])
            rr = float(parts[3]) if len(parts) > 3 else RISK_RATIO
            mp = int(parts[4]) if len(parts) > 4 else MAX_POS
            GROUPS = GROUPS + [(name, capital, inject, rr, mp)]
    else:
        GROUPS = [(n, c, i, RISK_RATIO, MAX_POS) for n, c, i in GROUPS]

    fin = pd.read_csv(args.signals, encoding="utf-8-sig")
    tr = fin[(fin["mode"] == "prebreak") & (fin["triggered_20d"] == 1)]
    if args.smoke:
        tr = tr.head(args.smoke)
    print(f"触发样本: {len(tr)} 笔")
    print("加载 K 线（只读 duckdb，缓存）…")
    klines = load_kline_cache([str(c) for c in tr["code"].unique()])
    print(f"K 线命中 {len(klines)} 只")

    print("[rebuild] delay2 出场重算…")
    sig, _ = rebuild_exit_for_mode(tr, klines, "delay2", mode="prebreak", hold="20d")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    groups_data = {}
    for spec in GROUPS:
        name, capital, inject = spec[0], spec[1], spec[2]
        rr = spec[3] if len(spec) > 3 else RISK_RATIO
        mp = spec[4] if len(spec) > 4 else MAX_POS
        print(f"[{name}] 资金层模拟（{capital:.0f} × {rr:.2%} × {mp}仓"
              f"{' + 月注入3000' if inject else ''}）…")
        groups_data[name] = collect_group(sig, klines, name, capital, inject, rr, mp,
                                          same_day_order=args.order)
        o = groups_data[name]["overview"]
        print(f"  → {o['n_exec']} 笔 | {o['total_ret']:+.1f}% | avgR {o['avg_r']:+.3f}"
              f" | 回撤 {groups_data[name]['dd']['max_dd_pct']:.1f}%"
              if groups_data[name]["dd"] else f"  → {o['n_exec']} 笔 | {o['total_ret']:+.1f}%")

    print("[蒙卡] 信号层 + 资金层…")
    # 信号层蒙卡（三组共享，一次）
    sig_rs = tr["r_20d"].astype(float).tolist()
    mc_sig = simulate([{"r_multiple": r} for r in sig_rs], n_simulations=N_SIM,
                      fee_per_trade_r=0.0)
    # 各组资金层蒙卡文本（供报告引用）
    mc_txts = {}
    for name, g in groups_data.items():
        if g["rs"]:
            mc_txts[name] = render_scenario_report(g["mc"], g["overview"]["capital"],
                                                   g["overview"]["avg_risk"], rs=g["rs"])
    # B 组蒙卡净值曲线图（老板版式）
    png_mc = plot_equity_paths(groups_data["B"]["mc"], CAPITAL_B,
                               groups_data["B"]["overview"]["avg_risk"],
                               out_path=str(OUT_DIR / "实验" /
                                            "蒙特卡洛-净值曲线-资金8401-资金层.png"))
    print(f"  图: {png_mc}")

    print("[图表] 资金曲线/回撤叠加/R 直方图…")
    png_eq = draw_equity_curve(groups_data, OUT_DIR / "图表" / "图表-资金升级-资金曲线.png")
    png_dd = draw_drawdown_curve(groups_data, OUT_DIR / "图表" / "图表-资金升级-回撤叠加.png")
    png_r = draw_r_hist(groups_data, OUT_DIR / "图表" / "图表-资金升级-R分布.png")

    # 可买池量化
    pool = buyable_pool(tr)
    # 成交集重叠（A vs B 成交 code）
    codes_a = set(groups_data["A"]["codes"])
    codes_b = set(groups_data["B"]["codes"])
    added = sorted(codes_b - codes_a)
    overlap = sorted(codes_a & codes_b)
    print(f"成交集：A={len(codes_a)}只 B={len(codes_b)}只 新增{len(added)}只 重叠{len(overlap)}只")

    payload = {
        "meta": {"signals": args.signals, "smoke": args.smoke,
                 "n_trigger": len(tr), "n_signal_mc": N_SIM},
        "groups": {name: _slim(g) for name, g in groups_data.items()},
        "pool": pool, "overlap": {"added": added, "n_added": len(added),
                                  "n_overlap": len(overlap)},
        "mc_sig_txt": render_scenario_report(mc_sig, 5600.0,
                                             groups_data["A"]["overview"]["avg_risk"],
                                             rs=sig_rs),
        "mc_txts": mc_txts,
        "charts": {"equity": png_eq, "drawdown": png_dd, "r_hist": png_r,
                   "mc_net": png_mc},
    }
    json_path = DATA_DIR / "data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
    print(f"数据 → {json_path}")
    print("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
