#!/usr/bin/env python3
"""R-052 0.5R 分步 vs 1R 直开建仓方式对比（V3 现行口径，2026-08-11）

信号层回放（复用 r41_phase_variants 骨架 + 引擎同源函数，不复制规则）：
  V0 现行 0.5R 分步（delay2 确认）｜1R 一次直开｜B1 触发日涨幅≥7%免确认直接补
  （决策=触发日收盘后，无前视）｜H1 信号日涨幅≥7%→1R｜H2 信号日涨幅≥5%→1R｜
  V2 涨幅≥7%→0.5R 不补持有到底｜V3 确认日收盘>开仓日×1.05→放弃补仓｜V4 严格首根判定

双口径：r_full（全仓 R，E-046，对账引擎 r_20d）／r_money（金额加权：0.5R 权重 0.5、
补仓后 1.0、直开 1.0）。

无前视：sig_day_pct（信号日收盘涨幅）在信号日收盘后挂单时已知 → 决策变量；
trig_pct（触发日涨幅）仅诊断（挂单时不可知）；conf_ratio（确认日收盘/进场价）仅描述性
归因（补仓决策在确认日收盘后做出——V3 放弃补仓无前视）。

对账门禁：V0 replay r_full == backtest_r43_t2 r_20d（±0.005，逐笔）；1R replay ==
backtest_r52_1r r_20d——不过即停（禁止带病分析）。

用法:
  python 项目/回测系统/r52_phase_compare.py [--signals-05 产出/输出/backtest_r43_t2/signals.csv]
      [--signals-1r 产出/输出/backtest_r52_1r/signals.csv] [--out 产出/输出/实验/r52]
      [--verify-1r]（1R 对账需引擎产物就绪；0.5R 组/变体/场景可先行）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402

from 回测系统.confirm_replay import load_kline_cache  # noqa: E402
from 回测系统.tracking import _trade_cost, _track_window  # noqa: E402（引擎同源出场/成本，不复制）

HOLD = 20
WINDOWS = [("26年", None), ("近7年", "2019-01-01"), ("近3年", "2023-01-01")]


def _signal_idx(k: pd.DataFrame, signal_date: str) -> int:
    dates = [str(d)[:10] for d in k["日期"]]
    try:
        return dates.index(signal_date)
    except ValueError:
        return -1


def _trig_idx(k: pd.DataFrame, t: int, end: int, entry: float) -> int | None:
    high = k["最高"].values
    for j in range(t + 1, end + 1):
        if high[j] >= entry:
            return j
    return None


def _trig_pct(k: pd.DataFrame, trig_idx: int) -> float:
    closes = k["收盘"].values
    if trig_idx < 1 or closes[trig_idx - 1] <= 0:
        return 0.0
    return closes[trig_idx] / closes[trig_idx - 1] - 1.0


def _r(exit_price: float, entry: float, risk: float, enable_cost: bool = True) -> float:
    """引擎同口径 R = (exit - entry - cost) / risk（成本元/股，_trade_cost 同源）

    r43_t2 引擎 enable_cost=true——回放必须扣成本才能逐笔对账（r41 时代 cost=False
    只差 ±0.01R 且不做逐笔对账；R-052 对账容差 ±0.005 必须同口径）。
    """
    if risk <= 0:
        return 0.0
    cost = _trade_cost(entry, exit_price, enable_cost, 1.0) if enable_cost else 0.0
    return (exit_price - entry - cost) / risk


def _sig_day_pct(k: pd.DataFrame, t: int) -> float:
    closes = k["收盘"].values
    if t < 1 or closes[t - 1] <= 0:
        return 0.0
    return closes[t] / closes[t - 1] - 1.0


def replay_variant(signals: pd.DataFrame, klines: dict, variant: str) -> pd.DataFrame:
    """单变体回放（V0/1R/H1/H2/V2/V3/V4；r41 骨架 + 场景变量）"""
    from 分析决策.分析.indicators import half_position_confirm_delay2, confirm_conditions

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
        spct = _sig_day_pct(k, t)   # 信号日收盘涨幅（挂单时已知，决策变量）
        grade = str(r.get("grade", "?"))
        trig_close = float(k["收盘"].values[trig])

        def _track(start: int) -> float:
            ex, _, _ = _track_window(k["最高"].values, k["最低"].values, k["收盘"].values,
                                     k["日期"].values, start, end, entry, stop,
                                     True, 1.0, False)  # enable_cost=True（与引擎 r43_t2 同口径）
            return ex

        def _emit(rf: float, w: float, kind: str, conf_ratio: float = 0.0) -> None:
            rows.append({"code": code, "date": sig_date, "grade": grade, "entry": entry,
                         "risk": risk, "sig_day_pct": round(spct, 4), "trig_pct": round(tpct, 4),
                         "conf_ratio": round(conf_ratio, 4) if conf_ratio else 0.0,
                         "variant": variant, "r_full": round(rf, 3), "r_money": round(w * rf, 3),
                         "weight": w, "kind": kind})

        # ── 混合/特例决策 ──
        if variant == "1R" or (variant in ("H1", "H2") and spct >= (0.07 if variant == "H1" else 0.05)):
            _emit(_r(_track(trig + 1), entry, risk), 1.0, "direct_1r")
            continue
        # B1 涨停免确认（审核意见 1 恢复：R-041 无前视被否结论，决策时点=触发日
        # 收盘后涨幅已知，执行=确认日收盘后追补，信息时序合规；与 H1 同构对照）
        if variant == "B1" and tpct >= 0.07:
            add_price = float(k["收盘"].values[used]) if used < len(k) else trig_close
            _emit(_r(_track(used + 1), entry, risk), 1.0, "limit_up_direct_add",
                   conf_ratio=(add_price / entry if entry > 0 else 0.0))
            continue

        conf_idx = trig + 1
        v = half_position_confirm_delay2(k, entry, stop, conf_idx, max_idx=end)
        used = v["conf_idx_used"]

        # 无确认空间 → 0.5R 持有到期（引擎保守近似同式）
        if conf_idx > end:
            _emit(_r(_track(trig + 1), entry, risk), 0.5, "no_confirm_space")
            continue
        # wait（收线未出现）→ 引擎同款：按确认补仓处理（_phase_in_track 对
        # wait 走 _track_window(used+1) 确认路径，非 0.5R 持有——审核意见 2）
        if v.get("wait"):
            _emit(_r(_track(used + 1), entry, risk), 1.0, "confirmed_wait")
            continue

        # V2：信号日涨幅 ≥7% → 0.5R 不补持有到底（不追高，r41 B2 无前视版）
        if variant == "V2" and spct >= 0.07:
            _emit(_r(_track(used + 1), entry, risk), 0.5, "limit_up_hold_half")
            continue

        # V4：严格首根判定（无 delay2）
        if variant == "V4":
            first = trig + 1
            cond = confirm_conditions(k.iloc[: first + 1], entry, stop)
            if cond["wait"]:
                continue
            if cond["stopped"]:
                _emit(_r(stop, entry, risk), 0.5, "stop_half")
                continue
            closes = k["收盘"].values
            confirmed = bool(cond["c1"] and closes[first] >= trig_close and cond["c3"])
            conf_close = float(cond["close"]) if cond["close"] else 0.0
            stopped, used = False, first
        else:
            confirmed, stopped = bool(v["confirmed"]), bool(v["stopped"])
            conf_close = float(v["close"]) if v["close"] else 0.0

        if stopped:
            _emit(_r(stop, entry, risk), 0.5, "stop_half")
            continue

        conf_ratio = conf_close / entry if entry > 0 else 0.0

        if not confirmed:
            _emit(_r(conf_close, entry, risk), 0.5, "reject_half", conf_ratio)
            continue

        # V3：确认日收盘 > 开仓日收盘×1.05 → 放弃补仓（0.5R 持有到底，无前视：
        # 补仓决策在确认日收盘后做出，价格已知）
        if variant == "V3" and conf_close > trig_close * 1.05:
            _emit(_r(_track(used + 1), entry, risk), 0.5, "skip_add", conf_ratio)
            continue

        # V0 确认补仓：1R 持有到出场
        _emit(_r(_track(used + 1), entry, risk), 1.0, "confirmed", conf_ratio)
    return pd.DataFrame(rows)


def _agg(df: pd.DataFrame) -> dict:
    if not len(df):
        return {"n": 0}
    r_full = df["r_full"].values
    r_money = df["r_money"].values
    return {"n": len(df), "avgR_full": round(float(r_full.mean()), 4),
            "avgR_money": round(float(r_money.mean()), 4),
            "win_rate": round(float((r_full > 0).mean()), 4),
            "sumR_full": round(float(r_full.sum()), 2),
            "sumR_money": round(float(r_money.sum()), 2)}


def _curve_dd(rs: pd.Series) -> float:
    """按信号日排序累计 R 曲线最大回撤（峰值→谷底，R 单位）"""
    if not len(rs):
        return 0.0
    peak = -1e18
    dd = 0.0
    for v in rs.values:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals-05", default="产出/输出/backtest_r43_t2/signals.csv")
    ap.add_argument("--signals-1r", default="产出/输出/backtest_r52_1r/signals.csv")
    ap.add_argument("--out", default="产出/输出/实验/r52")
    ap.add_argument("--verify-1r", action="store_true", help="1R 对账（需引擎产物就绪）")
    ap.add_argument("--variant", default="V0", help="回放变体（V0/1R/H1/H2/V2/V3/V4）")
    ap.add_argument("--window", default=None, help="时间窗（26年/近7年/近3年，默认全量）")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 信号载入（20d 触发集）──
    df = pd.read_csv(args.signals_05, encoding="utf-8-sig", dtype={"code": str})
    trig = df[(df["mode"] == "prebreak") & (df["triggered_20d"] == 1)].copy()
    if args.window:
        wmin = {"近7年": "2019-01-01", "近3年": "2023-01-01"}.get(args.window)
        if wmin:
            trig = trig[trig["date"].astype(str) >= wmin]
    print(f"[r52] 20d 触发集 {len(trig)} 笔（{args.window or '26年'}）| 加载 K 线 ...")
    codes = sorted(set(str(c).zfill(6) for c in trig["code"]))
    klines = load_kline_cache(codes)
    print(f"[r52] K 线 {len(klines)}/{len(codes)} 只")

    # ── 回放 ──
    replay = replay_variant(trig, klines, args.variant)
    print(f"[r52] {args.variant} 回放 {len(replay)} 笔（信号 {len(trig)} 笔）")

    # ── 对账（V0 vs r43_t2 / 1R vs r52_1r，逐笔 ±0.005）──
    if args.variant == "V0":
        src = pd.read_csv(args.signals_05, encoding="utf-8-sig", dtype={"code": str})
        src = src[(src["mode"] == "prebreak") & (src["triggered_20d"] == 1)]
        src["key"] = src["code"].astype(str) + "_" + src["date"].astype(str).str[:10]
        rp = replay.copy()
        rp["key"] = rp["code"] + "_" + rp["date"]
        m = rp.merge(src[["key", "r_20d"]], on="key", how="inner")
        if len(m):
            diffs = (m["r_full"] - pd.to_numeric(m["r_20d"], errors="coerce")).abs()
            bad = int((diffs > 0.005).sum())
            print(f"[r52] V0 对账：{len(m)} 笔 | 超差 {bad} 笔 | max 差 {diffs.max():.4f}R")
            if bad:
                print("  ❌ 对账失败——停跑（禁止带病分析）")
                return 2
            print("  ✅ V0 对账通过（与 backtest_r43_t2 r_20d 逐笔一致）")
    if args.verify_1r and args.variant == "1R":
        p1 = Path(args.signals_1r)
        if not p1.exists():
            print(f"[r52] 1R 信号集不存在（{p1}）——引擎重跑未完成，跳过 1R 对账")
        else:
            src = pd.read_csv(p1, encoding="utf-8-sig", dtype={"code": str})
            src = src[(src["mode"] == "prebreak") & (src["triggered_20d"] == 1)]
            src["key"] = src["code"].astype(str) + "_" + src["date"].astype(str).str[:10]
            rp = replay.copy()
            rp["key"] = rp["code"] + "_" + rp["date"]
            m = rp.merge(src[["key", "r_20d"]], on="key", how="inner")
            if len(m):
                diffs = (m["r_full"] - pd.to_numeric(m["r_20d"], errors="coerce")).abs()
                bad = int((diffs > 0.005).sum())
                print(f"[r52] 1R 对账：{len(m)} 笔 | 超差 {bad} 笔 | max 差 {diffs.max():.4f}R")
                if bad:
                    print("  ❌ 1R 对账失败——停跑")
                    return 2
                print("  ✅ 1R 对账通过（与 backtest_r52_1r r_20d 逐笔一致）")

    # ── 输出 ──
    replay.to_csv(out_dir / f"replay_{args.variant}_{args.window or 'full'}.csv",
                  index=False, encoding="utf-8-sig")
    if len(replay):
        replay_sorted = replay.sort_values("date")
        dd = _curve_dd(replay_sorted["r_full"].cumsum())
        a = _agg(replay)
        print(f"  n {a['n']} | avgR_full {a['avgR_full']} | avgR_money {a['avgR_money']} | "
              f"胜率 {a['win_rate']:.1%} | 累计R_full {a['sumR_full']} | 累计R_money {a['sumR_money']} | "
              f"回撤(累计R曲线) {dd:.1f}R")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
