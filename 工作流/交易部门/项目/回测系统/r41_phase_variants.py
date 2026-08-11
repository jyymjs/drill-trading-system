#!/usr/bin/env python3
"""R-041 建仓方式实验·变体回放（2026-08-10 · 老板问"1R vs 0.5R 哪个好"重新回测）

口径（与回测引擎 tracking.py 完全对齐，可复算）：
- 信号集 = A1 全量重跑产物（backtest_r41_a1_phase/signals.csv，prebreak/20d 触发）
- 触发定位 = 信号日 t 之后、t+hold 内首根 最高≥trigger（引擎同规则）
- 确认判定 = indicators.half_position_confirm_delay2（引擎同函数，max_idx=end 同参）
- 出场 = 引擎 _track_window（逐日最低≤止损→止损价；否则 hold 末收盘）——直接复用
- 输出双口径：
    r_full  全仓 R（与引擎 E-046 同：reject/stop 不乘 0.5，可对账引擎 r_20d）
    r_money 金额加权 R（0.5R 权重 0.5 / 补仓后 1.0 / 直开 1.0）——"哪个赚得多"用这个
  （变体间比较一律用 r_money；B0 对账引擎用 r_full）
- 成本：不计入（变体间相对比较；与引擎绝对数差异 ≈0.01R 注明）

变体：
  B0  delay2 现行（基线）
  B1  涨停免确认补仓：开仓日涨幅 ≥7% → 跳过确认判定直接补 0.5R（按确认日收盘价追补）
  B2  涨停不追高：开仓日涨幅 ≥7% → 不补仓，0.5R 持有到出场
  B3  补仓限价：确认日收盘 > 开仓日收盘 × 1.05 → 放弃补仓（0.5R 持有）
  B4  放宽动能：C2 门槛 ≥ 开仓日收盘 × 0.98（首根判定，无 delay2）
  C1  个股环境：scale 由个股 60 日 environment_quality（替代大盘统一）
  C2  个股强提档：大盘弱但（评级 S/A 且开仓日涨幅 ≥5%）→ 按 1R 直开
诊断：D 涨停票专项（开仓日涨幅 ≥7%：确认率/reject 误杀/confirm 表现）
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
from 回测系统.tracking import _track_window  # noqa: E402（引擎同源出场，不复制）

DEFAULT_SIGNALS = os.path.join("产出", "输出", "backtest_r41_a1_phase", "signals.csv")
HOLD = 20


def _load_signals(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    return df[(df["mode"] == "prebreak") & (df[f"triggered_{HOLD}d"] == 1)].copy()


def _signal_idx(k: pd.DataFrame, signal_date: str) -> int:
    dates = k["日期"].astype(str).str[:10].values
    for i, d in enumerate(dates):
        if d == signal_date:
            return i
    return -1


def _trig_idx(k: pd.DataFrame, t: int, end: int, entry: float) -> int | None:
    highs = k["最高"].values
    for j in range(t + 1, end + 1):
        if highs[j] >= entry:
            return j
    return None


def _trig_pct(k: pd.DataFrame, trig_idx: int) -> float:
    closes = k["收盘"].values
    prev = closes[trig_idx - 1] if trig_idx > 0 else closes[trig_idx]
    return (closes[trig_idx] - prev) / prev if prev > 0 else 0.0


def _r(exit_price: float, entry: float, risk: float) -> float:
    return (exit_price - entry) / risk if risk > 0 else 0.0


def replay_variant(signals: pd.DataFrame, klines: dict, variant: str) -> pd.DataFrame:
    from 分析决策.分析.indicators import half_position_confirm_delay2

    rows = []
    for _, r in signals.iterrows():
        code = str(r["code"]).zfill(6)
        k = klines.get(code)
        if k is None:
            continue
        entry = float(r[f"entry_{HOLD}d"])
        risk = float(r["risk"]) if pd.notna(r.get("risk")) else 0.0
        stop = float(r["stop"]) if pd.notna(r.get("stop")) else 0.0
        if risk <= 0:
            continue
        sig_date = str(r["date"])[:10]
        t = _signal_idx(k, sig_date)
        if t < 0:
            continue
        end = min(t + HOLD, len(k) - 1)
        if t + 1 > end:
            continue
        trig = _trig_idx(k, t, end, entry)
        if trig is None:
            continue
        tpct = _trig_pct(k, trig)
        grade = str(r.get("grade", "?"))

        # ── scale 判定（C 组）──
        scale = 0.5
        if variant == "C1":
            from 分析决策.分析.indicators import environment_quality
            env = environment_quality(k.iloc[: trig + 1])
            scale = 1.0 if env["quality"] == "good" else 0.5
        elif variant == "C2":
            if grade in ("S", "A") and tpct >= 0.05:
                scale = 1.0

        if scale == 1.0:
            ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                     k["日期"].values, trig + 1, end, entry, stop, False, 1.0, False)
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                         "r_full": round(_r(ex, entry, risk), 3),
                         "r_money": round(_r(ex, entry, risk), 3),
                         "weight": 1.0, "kind": "direct_1r"})
            continue

        # ── 0.5R 流程（引擎同源判定）──
        conf_idx = trig + 1
        v = half_position_confirm_delay2(k, entry, stop, conf_idx, max_idx=end)
        used = v["conf_idx_used"]

        # 无确认空间（触发在窗口末）→ 0.5R 持有到期（引擎保守近似同式）
        if conf_idx > end or v.get("wait"):
            ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                     k["日期"].values, trig + 1, end, entry, stop, False, 1.0, False)
            r_full = _r(ex, entry, risk)
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                         "r_full": round(r_full, 3), "r_money": round(0.5 * r_full, 3),
                         "weight": 0.5, "kind": "no_confirm_space"})
            continue
        trig_close = float(k["收盘"].values[trig])

        # B1/B2 涨停特例（先于确认判定）
        if tpct >= 0.07 and variant in ("B1", "B2"):
            if variant == "B1":
                # 免确认直接补仓（按确认日收盘价），1R 持有
                add_price = float(k["收盘"].values[used]) if used < len(k) else trig_close
                ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                         k["日期"].values, used + 1, end, entry, stop, False, 1.0, False)
                rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                             "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                             "r_full": round(_r(ex, entry, risk), 3),
                             "r_money": round(_r(ex, entry, risk), 3),
                             "weight": 1.0, "chase_pct": round((add_price - trig_close) / trig_close, 4),
                             "kind": "limit_up_direct_add"})
            else:
                # 不追高不平仓：0.5R 持有到出场
                ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                         k["日期"].values, used + 1, end, entry, stop, False, 1.0, False)
                r_full = _r(ex, entry, risk)
                rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                             "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                             "r_full": round(r_full, 3), "r_money": round(0.5 * r_full, 3),
                             "weight": 0.5, "kind": "limit_up_hold_half"})
            continue

        # B4 放宽 C2（首根判定，无 delay2）
        if variant == "B4":
            from 分析决策.分析.indicators import confirm_conditions
            first = trig + 1  # 首根确认日（B4 不用 delay2 的 T+2）
            if first > end:
                continue  # 无确认空间已由上方兜底处理
            cond = confirm_conditions(k.iloc[: first + 1], entry, stop)
            if cond["wait"]:
                continue
            if cond["stopped"]:
                rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                             "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                             "r_full": -1.0, "r_money": -0.5, "weight": 0.5, "kind": "stop_half"})
                continue
            closes = k["收盘"].values
            c2_relax = closes[first] >= trig_close * 0.98
            confirmed = bool(cond["c1"] and c2_relax and cond["c3"])
            conf_close = float(cond["close"]) if cond["close"] else 0.0
            stopped = False
            used = first
        else:
            confirmed = bool(v["confirmed"])
            stopped = bool(v["stopped"])
            conf_close = float(v["close"]) if v["close"] else 0.0

        if stopped:
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                         "r_full": -1.0, "r_money": -0.5, "weight": 0.5, "kind": "stop_half"})
            continue

        if not confirmed:
            r_full = _r(conf_close, entry, risk)
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                         "r_full": round(r_full, 3), "r_money": round(0.5 * r_full, 3),
                         "weight": 0.5, "kind": "reject_half"})
            continue

        # 确认 → 补 0.5R（B3 追高 >5% 放弃补仓）
        chase = (conf_close - trig_close) / trig_close if trig_close > 0 else 0.0
        if variant == "B3" and chase > 0.05:
            ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                     k["日期"].values, used + 1, end, entry, stop, False, 1.0, False)
            r_full = _r(ex, entry, risk)
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                         "r_full": round(r_full, 3), "r_money": round(0.5 * r_full, 3),
                         "weight": 0.5, "chase_pct": round(chase, 4), "kind": "skip_add"})
        else:
            ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                     k["日期"].values, used + 1, end, entry, stop, False, 1.0, False)
            r_full = _r(ex, entry, risk)
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "trig_pct": round(tpct, 4), "variant": variant,
                         "r_full": round(r_full, 3), "r_money": round(r_full, 3),
                         "weight": 1.0, "chase_pct": round(chase, 4), "kind": "confirm_add"})

    return pd.DataFrame(rows)


def summarize(det: pd.DataFrame) -> dict:
    if det is None or det.empty:
        return {"n": 0}
    n = len(det)
    rm = det["r_money"].sum()
    rf = det["r_full"].sum()
    wins = int((det["r_money"] > 0).sum())
    big = int((det["r_full"] <= -1.0).sum())
    return {"n": n, "cum_money": round(rm, 1), "cum_full": round(rf, 1),
            "avg_money": round(float(det["r_money"].mean()), 3),
            "win_rate_money": round(wins / n, 3), "big_loss_n": big,
            "big_loss_pct": round(big / n, 3),
            "chase_avg": round(float(det.get("chase_pct", pd.Series([0.0])).fillna(0).mean()), 4),
            "kind_dist": det["kind"].value_counts().to_dict()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=DEFAULT_SIGNALS)
    ap.add_argument("--variants", default="B0,B1,B2,B3,B4,C1,C2")
    args = ap.parse_args()

    signals = _load_signals(args.signals)
    codes = sorted(set(signals["code"].astype(str)))
    print(f"[R41] 信号集 {len(signals)} 笔（prebreak/{HOLD}d 触发）/ {len(codes)} 只", flush=True)
    klines = load_kline_cache(codes)
    print(f"[R41] K 线缓存 {len(klines)} 只", flush=True)

    # ── 诊断 D：涨停票专项 ──
    print("\n════════ 诊断 D：开仓日涨幅 ≥7%（涨停级）票专项 ════════")
    from 分析决策.分析.indicators import half_position_confirm_delay2
    from 回测系统.confirm_replay import _post_exit_high
    lu = []
    for _, r in signals.iterrows():
        k = klines.get(str(r["code"]).zfill(6))
        if k is None:
            continue
        entry = float(r[f"entry_{HOLD}d"])
        stop = float(r["stop"]) if pd.notna(r.get("stop")) else 0.0
        risk = float(r["risk"]) if pd.notna(r.get("risk")) else 0.0
        sig_date = str(r["date"])[:10]
        t = _signal_idx(k, sig_date)
        if t < 0:
            continue
        end = min(t + HOLD, len(k) - 1)
        trig = _trig_idx(k, t, end, entry)
        if trig is None or _trig_pct(k, trig) < 0.07:
            continue
        v = half_position_confirm_delay2(k, entry, stop, trig + 1, max_idx=end)
        verdict = "stop" if v["stopped"] else "confirm" if v["confirmed"] else "reject" if not v["wait"] else "wait"
        post_high = _post_exit_high(k, str(k["日期"].astype(str).str[:10].values[trig])[:10], window=20)
        lu.append({"code": str(r["code"]), "date": sig_date, "grade": str(r.get("grade", "?")),
                   "trig_pct": round(_trig_pct(k, trig), 3), "verdict": verdict,
                   "post_high_20d": round(post_high, 2), "entry": entry,
                   "risk": round(risk, 2)})
    lud = pd.DataFrame(lu)
    if len(lud):
        print(f"  涨停级 {len(lud)} 笔 | 确认率 {round((lud['verdict']=='confirm').mean(),3)} | "
              f"reject {(lud['verdict']=='reject').sum()} | stop {(lud['verdict']=='stop').sum()} | wait {len(lud)-lud['verdict'].isin(['confirm','reject','stop']).sum()}")
        for vv, lbl in [("reject", "reject 组（不确认平仓）"), ("confirm", "confirm 组（补仓）")]:
            g = lud[lud["verdict"] == vv]
            if len(g):
                g = g.copy()
                g["miss"] = (g["post_high_20d"] - g["entry"]) >= g["risk"]
                print(f"  {lbl} {len(g)} 笔中 {int(g['miss'].sum())} 笔后续 20d 最高涨幅 ≥1R")
    else:
        print("  无")

    # ── 变体回放 ──
    print("\n════════ 变体对照（金额加权累计 R = r_money 口径）════════")
    header = f"{'变体':<8}{'笔数':>6}{'累计R(money)':>13}{'累计R(full)':>12}{'avgR':>8}{'胜率':>8}{'大亏笔':>7}{'追高均':>8}"
    print(header)
    print("-" * len(header))
    results = {}
    for v in [x.strip() for x in args.variants.split(",") if x.strip()]:
        det = replay_variant(signals, klines, v)
        s = summarize(det)
        results[v] = (s, det)
        print(f"{v:<8}{s['n']:>6}{s['cum_money']:>13.1f}{s['cum_full']:>12.1f}"
              f"{s['avg_money']:>8.3f}{s['win_rate_money']:>8.1%}{s['big_loss_n']:>7}"
              f"{s['chase_avg']:>8.1%}")

    print("\n  vs B0（现行）金额口径差值：")
    for v in results:
        if v == "B0":
            continue
        s, _ = results[v]
        b0, _ = results["B0"]
        print(f"  {v}: 累计R(money) {s['cum_money'] - b0['cum_money']:+.1f} "
              f"| avgR {s['avg_money'] - b0['avg_money']:+.3f} "
              f"| 大亏 {(s['big_loss_n'] - b0['big_loss_n']):+d} 笔")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "产出", "输出", "实验")
    os.makedirs(out_dir, exist_ok=True)
    for v, (s, det) in results.items():
        det.to_csv(os.path.join(out_dir, f"r41_variant_{v}.csv"), index=False, encoding="utf-8-sig")
    print(f"\n明细 → {out_dir}/r41_variant_*.csv")


if __name__ == "__main__":
    main()
