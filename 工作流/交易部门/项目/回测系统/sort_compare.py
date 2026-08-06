#!/usr/bin/env python3
"""同日多候选排序标准对照实验（2026-08-06 老板拍板 · T-032 质量优先排序）

场景：当日扫描出不止一个可买入候选（5600 元/3 仓只够一部分）——选哪一个？
对照两维：
  ① 同日候选处理顺序 same_day_order：
     time（现状先到先得）/ s_count（S 数降序）/ risk_mid（每股风险居中）/
     mom_asc（动量升序）/ vol_desc（触发日量比降序）
  ② 挂单策略 cap_per_day：
     0（全挂=现状）/ 3（只挂排序前 3）/ 5（只挂排序前 5）
     ——券商条件单按触发时间成交，质量优先只能靠挂单阶段控制挂哪些（2026-08-06 定）
口径（与生产一致）：2.0%×3仓 · half_phase 0.5R 分步 · confirm_fn=delay2 ·
信号源 backtest_final_20260806/signals.csv（514 笔触发，delay2 出场重算同
confirm_replay 链路）；回撤 = 总资产口径（峰值回撤 + 真实回撤，口径铁律 08-06）。

用法:
  python 项目/回测系统/sort_compare.py --smoke 60    # 自检（前 60 笔触发）
  python 项目/回测系统/sort_compare.py               # 全量 15 组对照
"""
import argparse
import sys
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
from 回测系统.delay2_dd_recalc import build_total_asset_curve
from 回测系统.sim_capital import simulate_capital

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"
CAPITAL = 5600.0
RISK_RATIO = 0.02
MAX_POS = 3

ORDERS = ["time", "s_count", "risk_mid", "mom_asc", "vol_desc"]
ORDER_LABEL = {"time": "时间先到先得(现状)", "s_count": "S数降序",
               "risk_mid": "每股风险居中", "mom_asc": "动量升序",
               "vol_desc": "量比降序"}
CAPS = [0, 3, 5]
CAP_LABEL = {0: "全挂(现状)", 3: "只挂前3", 5: "只挂前5"}


def enrich_sort_cols(sig: pd.DataFrame, klines: dict) -> pd.DataFrame:
    """为触发行附排序所需列：mom20（复算）+ vol_ratio（触发日量比复算）

    触发日定位与引擎同规则：信号日 T 之后首根 最高≥trigger。
    vol_ratio = 触发日成交量 ÷ 前 20 日均量（含触发日前 20 根，不含触发日）。
    """
    sig = sig.copy()
    moms, vols = [], []
    n = len(sig)
    for i, (_, r) in enumerate(sig.iterrows(), 1):
        k = klines.get(str(r["code"]))
        if k is None or k.empty:
            moms.append(None); vols.append(None); continue
        dates = k["日期"].astype(str).str[:10].values
        sig_ts = str(r["date"])[:10]
        idxs = [j for j, d in enumerate(dates) if d == sig_ts]
        if not idxs:
            moms.append(None); vols.append(None); continue
        t = idxs[0]
        high = k["最高"].to_numpy()
        vol = k["成交量"].to_numpy()
        trig = None
        for j in range(t + 1, len(k)):
            if high[j] >= float(r["trigger"]):
                trig = j
                break
        if trig is None or trig < 20:
            moms.append(None); vols.append(None); continue
        close20 = float(k["收盘"].to_numpy()[trig - 20])
        moms.append(float(r["trigger"]) / close20 - 1.0 if close20 > 0 else None)
        ref = float(vol[max(0, trig - 20):trig].mean()) if trig > 0 else 0.0
        vols.append(float(vol[trig]) / ref if ref > 0 else None)
        if i % 200 == 0 or i == n:
            print(f"  [enrich] {i}/{n}")
    sig["mom20"] = moms
    sig["vol_ratio"] = vols
    return sig


def _ordered(sig: pd.DataFrame, order: str) -> pd.DataFrame:
    """与 simulate_capital 同排序逻辑（截断机会成本用，行集一致）"""
    if order == "time":
        return sig.sort_values(["date", "code"], kind="stable")
    s = sig.copy()
    if order == "s_count":
        s["_k"] = (s[["PT", "TY", "DN", "DL", "LK", "SF"]] == "S").sum(axis=1)
        return s.sort_values(["date", "_k"], ascending=[True, False], kind="stable")
    if order == "risk_mid":
        s["_k"] = (s["risk"].astype(float) - 1.5).abs()
        return s.sort_values(["date", "_k"], ascending=[True, True], kind="stable")
    if order == "mom_asc":
        return s.sort_values(["date", "mom20"], ascending=[True, True], kind="stable")
    if order == "vol_desc":
        return s.sort_values(["date", "vol_ratio"], ascending=[True, False], kind="stable")
    raise ValueError(order)


def run_group(sig: pd.DataFrame, order: str, cap: int, klines: dict) -> dict:
    sim = simulate_capital(sig, CAPITAL, RISK_RATIO, max_positions=MAX_POS,
                           mode="prebreak", hold="20d", grades=["S"],
                           half_phase=True, confirm_fn=make_confirm_fn("delay2"),
                           same_day_order=order, cap_per_day=cap)
    curve = build_total_asset_curve(sim["trades"], CAPITAL,
                                    lambda c: klines.get(c))
    dd = max_drawdown(curve, CAPITAL)
    # 机会成本：cap 截断的候选（组内排序后第 cap+1 起同日行）信号 avgR
    opp = None
    if cap > 0:
        sub = _ordered(sig, order)
        sub["_d_rank"] = sub.groupby("date").cumcount()
        cut = sub[sub["_d_rank"] >= cap]
        if len(cut):
            opp = float(cut["r_20d"].astype(float).mean())
    return {"sim": sim, "dd": dd, "opp": opp}


def render_report(res: dict, smoke: int | None) -> str:
    out = [
        "# 同日多候选排序对照实验（T-032 · 2026-08-06 老板拍板质量优先排序）",
        "",
        f"> 场景：当日扫描出多个可买入候选，5600 元/3 仓只够一部分——选哪一个？",
        f"> 信号源：backtest_final_20260806/signals.csv（{'自检前 ' + str(smoke) if smoke else '514 笔触发'}"
        " · prebreak/20d · S 级）｜delay2 出场重算（confirm_replay 同链路）",
        f"> 口径：2.0%×{MAX_POS}仓 · half_phase · delay2 确认；回撤 = 总资产口径"
        "（真实回撤=占初始资金 / 峰值回撤=占峰值，铁律 08-06）",
        "",
        "## 对照总表（排序标准 × 挂单策略）",
        "",
        "| 排序标准 | 挂单策略 | 收益 | 真实回撤 | 峰值回撤 | 胜率 | avgR | 笔数 | 被截断信号avgR |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for order in ORDERS:
        for cap in CAPS:
            r = res[order][cap]
            sim = r["sim"]; dd = r["dd"]
            opp = f"{r['opp']:+.3f}" if r["opp"] is not None else "—"
            out.append(
                f"| {ORDER_LABEL[order]} | {CAP_LABEL[cap]} | {sim['total_ret']:+.1f}% | "
                f"{dd['max_dd_pct']:.1f}% | {dd['max_dd_pct_peak']:.1f}% | "
                f"{sim['win_rate']:.1%} | {sim['avg_r']:+.3f} | {sim['n_exec']} | {opp} |")
    out += [
        "",
        "## 结论（数据驱动 · 签字权归老板）",
        "",
        "> 由主对话根据本表综合判定排序标准与挂单策略；接入生产前需质检。",
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
    if args.smoke:
        tr = tr.head(args.smoke)
    print(f"触发样本: {len(tr)} 笔")
    print("加载 K 线（只读 duckdb，缓存）…")
    klines = load_kline_cache([str(c) for c in tr["code"].unique()])
    print(f"K 线命中 {len(klines)} 只")
    print("delay2 出场重算…")
    sig, _ = rebuild_exit_for_mode(tr, klines, "delay2", mode="prebreak", hold="20d")
    print("附排序列（mom20/vol_ratio 复算）…")
    sig = enrich_sort_cols(sig, klines)

    res: dict = {o: {} for o in ORDERS}
    for order in ORDERS:
        for cap in CAPS:
            print(f"[{order} × cap={cap}] 资金模拟 + 回撤重算…")
            res[order][cap] = run_group(sig, order, cap, klines)
            s = res[order][cap]["sim"]
            print(f"  收益 {s['total_ret']:+.1f}% | 真实回撤 "
                  f"{res[order][cap]['dd']['max_dd_pct']:.1f}% | 笔数 {s['n_exec']}")

    report = render_report(res, args.smoke)
    print()
    print(report)
    out_path = args.out or str(_ROOT / "产出" / "输出" / "排序对照实验-T032-20260806.md")
    if args.smoke:
        out_path = str(Path(out_path).with_name(Path(out_path).stem + "_smoke" + Path(out_path).suffix))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())