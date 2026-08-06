#!/usr/bin/env python3
"""delay2 真实回撤补算（2026-08-06 老板拍板：禁止现金口径回测，只允许峰值+真实回撤）

背景：确认规则对照实验（confirm_replay）的资金模拟回撤为现金口径（买入扣款市值
不计入，系统性放大：strict 86.9% / delay2 83.0%）。本脚本对 strict / delay2 两版
重算**总资产口径**（现金 + Σ持仓×当日 qfq 收盘）的真实回撤，并附占峰值回撤——
替换现金口径呈报。

口径与 capital_dd_recalc.py 完全一致（逐日估值/回撤算法/自检），差异：
- 信号源 = backtest_final_20260806/signals.csv（确认规则对照同源，514 笔触发）
- 资金模拟 = simulate_capital half_phase（0.5R 分步）+ confirm_fn 注入
  （strict / delay2 同 confirm_replay.compare_confirm_modes 链路）
- 现金流事件扩展 half 单：入场日扣半仓款+费、确认日扣补款+费（add_cost/add_fee）、
  出场日收最终股数款-费（trades 含 half/confirm_date 字段，与 simulate_capital 一致）

用法:
  python 项目/回测系统/delay2_dd_recalc.py                # strict vs delay2 全量
  python 项目/回测系统/delay2_dd_recalc.py --smoke 20     # 自检（前 20 笔触发）
"""
import argparse
import datetime as _dt
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from 回测系统.capital_dd_recalc import max_drawdown
from 回测系统.confirm_replay import load_kline_cache, make_confirm_fn, rebuild_exit_for_mode
from 回测系统.sim_capital import simulate_capital
from 分析决策.风控.capital import calc_trade_fee

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"
CAPITAL = 5600.0
RISK_RATIO = 0.02
MAX_POS = 3


def build_total_asset_curve(trades: list[dict], capital: float,
                            kline_fn) -> pd.DataFrame:
    """逐日总资产曲线（现金 + 持仓市值），支持 half_phase 分步补款事件

    现金流事件与 simulate_capital 一致：
      - half 单：入场日扣 add_cost（半仓款）+ fee_in（半仓费）；确认日
        （half_ok 且已 settle）扣 add_cost + add_fee；出场日收 exit_price×shares（最终股数）- fee_out
      - 普通单：入场日扣 entry×shares + fee_in；出场日收 exit_price×shares - fee_out
    """
    if not trades:
        return pd.DataFrame(columns=["date", "cash", "market_value", "total_asset"])

    entries: list[tuple] = []      # (日, 扣款, 股数, code)
    exits: list[tuple] = []        # (日, 收款, 股数, code)
    for t in trades:
        code = str(t["code"])
        if t.get("half"):
            # 半仓起步：入场扣半仓款+费；确认日扣补款+费（add_cost 即为半仓款）
            entries.append((pd.Timestamp(str(t["date"])), float(t["add_cost"]) + float(t["fee_in"]),
                            int(t["shares"]) // 2, code))
            if t.get("half_ok") and t.get("half_settled") and t.get("confirm_date"):
                cdate = str(t["confirm_date"])
                if cdate < str(t["exit_date"]):
                    entries.append((pd.Timestamp(cdate), float(t["add_cost"]) + float(t["add_fee"]),
                                    int(t["shares"]) // 2, code))
        else:
            cost = float(t["entry"]) * int(t["shares"])
            entries.append((pd.Timestamp(str(t["date"])), cost + float(t["fee_in"]),
                            int(t["shares"]), code))
        # 出场费：sim_capital 平仓循环已回写 p["fee_out"]（E-058 修复，按最终股数），
        # 直接使用——与 balance 流一致
        proceeds = float(t["exit_price"]) * int(t["shares"])
        exits.append((pd.Timestamp(str(t["exit_date"])),
                      proceeds - float(t["fee_out"]),
                      int(t["shares"]), code))

    # 交易日历 = 涉及股票 K线日期并集 + 事件日期
    codes = sorted({str(t["code"]) for t in trades})
    kl: dict[str, pd.DataFrame | None] = {c: kline_fn(c) for c in codes}
    cal: set[pd.Timestamp] = set()
    for c in codes:
        k = kl.get(c)
        if k is not None and len(k):
            cal.update(pd.to_datetime(k["日期"]).tolist())
    for d, *_ in entries + exits:
        cal.add(d)
    min_entry = min(e[0] for e in entries)
    max_event = max(d for d, *_ in entries + exits)
    start = min_entry - pd.Timedelta(days=1)
    if kl.get(codes[0]) is not None:
        dates = pd.to_datetime(kl[codes[0]]["日期"])
        m = dates < min_entry
        if m.any():
            start = dates[m].iloc[-1]
    cal = sorted(d for d in cal if start <= d <= max_event)
    if not cal or cal[0] != start:
        cal = [start] + cal

    close_series: dict[str, pd.Series] = {}
    for c in codes:
        k = kl.get(c)
        if k is not None and len(k):
            close_series[c] = pd.Series(k["收盘"].to_numpy(dtype=float),
                                        index=pd.to_datetime(k["日期"]))

    def px_on(code: str, d: pd.Timestamp) -> float | None:
        s = close_series.get(code)
        if s is None:
            return None
        v = s.asof(d)
        return None if pd.isna(v) else float(v)

    exits_by: dict = defaultdict(list)
    entries_by: dict = defaultdict(list)
    for d, amt, sh, c in exits:
        exits_by[d].append((c, amt, sh))
    for d, amt, sh, c in entries:
        entries_by[d].append((c, amt, sh))
    entry_px: dict[str, float] = {}
    for t in trades:
        entry_px[str(t["code"])] = float(t["entry"])

    cash = capital
    holdings: dict[str, int] = {}
    rows: list[dict] = []
    for d in cal:
        for c, amt, sh in exits_by.get(d, []):
            cash += amt
            holdings[c] = holdings.get(c, 0) - sh
            if holdings.get(c, 0) <= 0:
                holdings.pop(c, None)
        for c, amt, sh in entries_by.get(d, []):
            cash -= amt
            holdings[c] = holdings.get(c, 0) + sh
        mv = 0.0
        for c, sh in holdings.items():
            px = px_on(c, d)
            if px is None:
                px = entry_px.get(c, 0.0)
            mv += sh * px
        rows.append({"date": d, "cash": round(cash, 2),
                     "market_value": round(mv, 2),
                     "total_asset": round(cash + mv, 2)})
    return pd.DataFrame(rows)


def run_mode(signals_df: pd.DataFrame, klines: dict, cm: str,
             smoke: int | None) -> dict:
    """单模式：rebuild_exit（重算出场）→ simulate_capital（half_phase + confirm_fn）
    → 总资产曲线重算 → 峰值/真实回撤"""
    print(f"[{cm}] 重算出场 + 资金模拟（half_phase，confirm_fn={cm}）…")
    sig, verify = rebuild_exit_for_mode(signals_df, klines, cm,
                                        mode="prebreak", hold="20d")
    if smoke:
        sig = sig.head(smoke)
    sim = simulate_capital(sig, CAPITAL, RISK_RATIO, max_positions=MAX_POS,
                           mode="prebreak", hold="20d", grades=["S"],
                           half_phase=True, confirm_fn=make_confirm_fn(cm))
    curve = build_total_asset_curve(sim["trades"], CAPITAL,
                                    lambda c: klines.get(c))
    dd = max_drawdown(curve, CAPITAL)
    delta = abs(dd["end_total"] - sim["end_balance"])
    print(f"  [自检] 曲线终点 {dd['end_total']:,.2f} vs 旧口径终值 {sim['end_balance']:,.2f}"
          f"（差 {delta:.4f}）")
    if delta > 0.5:
        print("  [警告] 新口径现金流与 simulate_capital 不一致，请核查！")
    return {"sim": sim, "dd": dd, "n_exec": sim["n_exec"]}


def render_report(res: dict, smoke: int | None) -> str:
    s = res["strict"]; d = res["delay2"]
    out = [
        "# delay2 真实回撤补算——总资产口径（2026-08-06 老板拍板：禁现金口径）",
        "",
        f"> 日期：{_dt.datetime.now().astimezone().date().isoformat()} · 信号源："
        "backtest_final_20260806/signals.csv"
        f"（{'自检前 ' + str(smoke) + ' 笔' if smoke else '514 笔触发'} · prebreak/20d · S 级）"
        f" · 资金模拟：{CAPITAL:,.0f} 元 × 2.0% × {MAX_POS} 仓 · half_phase 0.5R 分步。",
        f"> 口径：逐日总资产 = 现金 + Σ持仓×当日 qfq 收盘（与 capital_dd_recalc 同法）；"
        "回撤 = 历史峰值→谷底最大跌幅；主百分比 = 占初始资金；附占峰值（业界标准）。",
        "> 现金流：half 单 = 入场扣半仓款+费 → 确认日扣补款+费 → 出场收最终股数款-费"
        "（与 simulate_capital 事件逐笔一致，自检终点差 <0.5 元）。",
        "",
        "## strict vs delay2 回撤对比（总资产口径）",
        "",
        "| 指标 | 现状(strict) | 延迟二次(delay2) | 变化 |",
        "|---|---:|---:|---|",
        f"| 总收益 | {s['sim']['total_ret']:+.1f}% | {d['sim']['total_ret']:+.1f}% | |",
        f"| 成交笔数 | {s['n_exec']} | {d['n_exec']} | |",
        f"| **真实回撤（占初始资金）** | **{s['dd']['max_dd_pct']:.1f}%** | **{d['dd']['max_dd_pct']:.1f}%** | |",
        f"| **真实回撤（占峰值）** | **{s['dd']['max_dd_pct_peak']:.1f}%** | **{d['dd']['max_dd_pct_peak']:.1f}%** | |",
        f"| 回撤金额（元） | {s['dd']['max_dd']:,.2f} | {d['dd']['max_dd']:,.2f} | |",
        f"| 回撤时长（自然日） | {s['dd']['dd_days']} | {d['dd']['dd_days']} | |",
        f"| 峰值 → 谷底 | {s['dd']['peak_date']} → {s['dd']['trough_date']} | {d['dd']['peak_date']} → {d['dd']['trough_date']} | |",
        f"| 谷底总资产（元） | {s['dd']['trough_val']:,.2f} | {d['dd']['trough_val']:,.2f} | |",
        f"| 终值资金（元） | {s['dd']['end_total']:,.2f} | {d['dd']['end_total']:,.2f} | |",
        "",
        "> 结论由老板综合判断；本表为合规口径（峰值回撤 + 真实回撤），现金口径不再产出。",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    ap.add_argument("--smoke", type=int, default=None, help="自检：只处理前 N 笔触发")
    ap.add_argument("--out", default=None, help="报告输出路径")
    args = ap.parse_args()

    fin = pd.read_csv(args.signals, encoding="utf-8-sig")
    tr = fin[(fin["mode"] == "prebreak") & (fin["triggered_20d"] == 1)]
    print(f"触发样本: {len(tr)} 笔")
    if args.smoke:
        tr = tr.head(args.smoke)
    print("加载 K 线（只读 duckdb，缓存）…")
    klines = load_kline_cache([str(c) for c in tr["code"].unique()])
    print(f"K 线命中 {len(klines)} 只")

    res = {cm: run_mode(tr, klines, cm, args.smoke) for cm in ("strict", "delay2")}
    report = render_report(res, args.smoke)
    print()
    print(report)
    out_path = args.out or str(_ROOT / "产出" / "输出" / "真实回撤-strict-vs-delay2-20260806.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
