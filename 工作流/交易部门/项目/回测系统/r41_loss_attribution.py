#!/usr/bin/env python3
"""R-041 大亏归因（2026-08-10 · 老板问"单笔最大亏损不是 1R 吗，为什么有 -3R"）

双口径：
  exec    执行口径（止损优先）：触发后 20 天内最低 ≤ 止损 → 按止损价出场（R=-1）；
          仅跳空直接穿止损价时 R<-1。此口径下大亏极少（实测 26220 笔仅 2 笔 ≤-1.5R）。
  hold20  持有 20 天口径（无止损）：(触发日后第 20 根收盘 - 进场价)/每股风险——
          旧报告"phase_in 关 -3~-1R 大亏 208 笔"即此口径（评估 phase_in 效果用，
          非执行口径）。
归因分类（逐笔查触发日后 20 根 K 线）：
  jump_gap   隔夜跳空：触发日次日开盘 < 触发日收盘 × 0.97（跳空低开≥3%）
  limit_down 跌停：触发日后存在单日收盘跌幅 ≥9.5%（主板跌停级）
  fade_high  高位回落：触发日涨幅 ≥5%（追在当天高点，次日动能不继）
  slow_draw  缓跌：其余
输出：分类统计表 + 明细 CSV + 结论行
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import pandas as pd  # noqa: E402

from 回测系统.confirm_replay import load_kline_cache  # noqa: E402

DEFAULT_A0 = os.path.join("产出", "输出", "backtest_r41_a0_1r", "signals.csv")


def classify(k: pd.DataFrame, trig_idx: int, entry: float) -> str:
    """触发日后 20 根内的亏损成因分类"""
    dates = k["日期"].astype(str).str[:10].values
    opens = k["开盘"].values
    closes = k["收盘"].values
    seg_o = opens[trig_idx + 1: trig_idx + 21]
    seg_c = closes[trig_idx + 1: trig_idx + 21]
    trig_close = closes[trig_idx]
    # 1) 隔夜跳空 ≥3%
    if len(seg_o) and seg_o[0] < trig_close * 0.97:
        return "jump_gap"
    # 2) 跌停级单日（-9.5%）
    if len(seg_c) and seg_c[0] > 0:
        drops = (seg_c[1:] - seg_c[:-1]) / seg_c[:-1] if len(seg_c) > 1 else []
        if any(d <= -0.095 for d in drops) or (len(seg_o) and len(seg_c) and seg_c[0] <= seg_o[0] * 0.95):
            return "limit_down"
    # 3) 触发日高位回落
    prev_close = closes[trig_idx - 1] if trig_idx > 0 else trig_close
    trig_pct = (trig_close - prev_close) / prev_close if prev_close > 0 else 0
    if trig_pct >= 0.05:
        return "fade_high"
    return "slow_draw"


def _attr_rows(sub: pd.DataFrame, klines: dict, mode: str) -> pd.DataFrame:
    """mode=exec（引擎 r_20d，止损优先）或 hold20（无止损持有 20 天收盘）"""
    from 回测系统.confirm_replay import _post_close_nth

    rows = []
    for _, r in sub.iterrows():
        code = str(r["code"])
        k = klines.get(code)
        if k is None:
            rows.append({"code": code, "date": str(r["date"])[:10], "grade": r["grade"],
                         "r": float(r.get("r_20d", 0) or 0), "entry": r.get("entry_20d", 0),
                         "cause": "no_data"})
            continue
        dates = k["日期"].astype(str).str[:10].values
        highs = k["最高"].values
        entry = float(r.get("entry_20d", 0) or 0)
        trig_idx = None
        for i, d in enumerate(dates):
            if d > str(r["date"])[:10] and highs[i] >= entry:
                trig_idx = i
                break
        if trig_idx is None:
            rows.append({"code": code, "date": str(r["date"])[:10], "grade": r["grade"],
                         "r": float(r.get("r_20d", 0) or 0), "entry": entry,
                         "cause": "no_trigger"})
            continue
        if mode == "hold20":
            stop_raw = r.get("stop")
            try:
                stop = float(stop_raw) if stop_raw is not None and str(stop_raw) not in ("", "nan", "None") else 0.0
            except (TypeError, ValueError):
                stop = 0.0
            risk = abs(entry - stop)
            post_c = _post_close_nth(k, str(dates[trig_idx])[:10], window=20)
            rr = (post_c - entry) / risk if risk > 0 and post_c > 0 else 0.0
        else:
            rr = float(r.get("r_20d", 0) or 0)
        rows.append({"code": code, "date": str(r["date"])[:10], "grade": r["grade"],
                     "r": round(rr, 2), "entry": entry, "cause": classify(k, trig_idx, entry)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=DEFAULT_A0)
    ap.add_argument("--threshold", type=float, default=-1.5,
                    help="亏损归因阈值（默认 -1.5R 以下）")
    ap.add_argument("--mode", default="both", choices=["exec", "hold20", "both"])
    args = ap.parse_args()

    df = pd.read_csv(args.signals, dtype={"code": str})
    sub = df[(df["mode"] == "prebreak") & (df["triggered_20d"] == 1)].copy()
    print(f"[R41-D] A0(1R直开) 触发集 {len(sub)} 笔", flush=True)

    klines = load_kline_cache(sorted(set(sub["code"].astype(str))))
    print(f"[R41-D] K 线缓存 {len(klines)} 只", flush=True)

    modes = ["exec", "hold20"] if args.mode == "both" else [args.mode]
    for m in modes:
        det = _attr_rows(sub, klines, m)
        loss = det[det["r"] <= args.threshold]
        print(f"\n════════ 口径 {m}：r≤{args.threshold} 亏损笔 {len(loss)}"
              f"（占 {round(len(loss)/max(len(det),1),1)}%）════════")
        if loss.empty:
            print("  无亏损笔——止损截断有效")
            continue
        g = loss.groupby("cause").agg(n=("r", "size"), avg_r=("r", "mean"),
                                      sum_r=("r", "sum")).reset_index()
        g["pct"] = g["n"] / len(loss)
        g = g.sort_values("n", ascending=False)
        print(f"{'分类':<12}{'笔数':>6}{'占比':>8}{'avgR':>8}{'累计R':>9}")
        print("-" * 48)
        for _, row in g.iterrows():
            print(f"{row['cause']:<12}{int(row['n']):>6}{row['pct']:>8.1%}"
                  f"{row['avg_r']:>8.2f}{row['sum_r']:>9.1f}")
        print("\n  亏损深度分布：")
        for lo, hi, lbl in [(args.threshold, -3.0, f"{args.threshold}~-3R"),
                            (-3.0, -5.0, "-3~-5R"), (-5.0, -1e9, "<-5R")]:
            seg = loss[(loss["r"] <= lo) & (loss["r"] > hi)]
            print(f"    {lbl:<12}{len(seg):>4} 笔" + (f" | avgR {seg['r'].mean():.2f}" if len(seg) else ""))
        det.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "产出", "输出", "实验", f"r41_loss_{m}.csv"),
                   index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
