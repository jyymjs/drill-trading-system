#!/usr/bin/env python3
"""R-069 实验 C：0.5R 补仓确认时点变体（R-069 方案 v3，交易部终审通过）

变体（20d 口径，与门禁 1 对账一致）：
  V0'：A/B + delay2（T+1 收盘三条件确认 + T+2 顺延）＝现行语义（A/B 注入）
  T0 ：A/B + {A,B,C3@T0}（T0 收盘确认 → T+1 开盘补仓；失败 → T0 收盘平仓）
  V4 ：A/B + 严格首根（T+1 无 delay2）＝历史对照
A/B（R-053，恒绑定触发日）：A=触发日收盘≥触发价；B=触发日量比>1.5（前20日均量分母）
C3@T0（indicators 口径）：T0 收阴且量比>1.5（MA5 分母）→ 拒绝
出场统一（比较变量只有确认时点）：确认后逐日低点≤止损 → 止损出场；否则 20d 末收盘
补仓价：V0/V4 = 确认日收盘；T0 = T+1 开盘
门禁 1：V0'（无 A/B 版）vs T8 r_20d ±0.005；门禁 3：T0 人工样本单元测试

用法:
  python 回测系统/r69c_confirm.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np
import pandas as pd

from 回测系统.replay_cache import KlineCache
from 回测系统.tracking import _find_signal_index, _trade_cost
from 分析决策.分析.indicators import half_position_confirm_delay2

SIG = _ROOT / "产出" / "输出" / "backtest_r43_t2_T8" / "signals.csv"
OUT = _ROOT / "产出" / "输出" / "实验" / "r57"
HOLD = 20
VOL_CONFIRM = 1.5


def _r(entry: float, exit_price: float, risk: float) -> float:
    cost = _trade_cost(entry, exit_price, True, 1.0)
    return (exit_price - entry - cost) / risk if risk > 0 else 0.0


def _ab_check(k: pd.DataFrame, trig_idx: int, entry: float) -> tuple[bool, bool]:
    """R-053 A/B（触发日口径）：A 收盘站稳；B 量比>1.5（前 20 日均量分母）"""
    closes = k["收盘"].astype(float).values
    vols = k["成交量"].astype(float).values
    a_ok = closes[trig_idx] >= entry
    ref = vols[max(0, trig_idx - 20):trig_idx]
    b_ok = (vols[trig_idx] / ref.mean()) > VOL_CONFIRM if ref.mean() > 0 else False
    return a_ok, b_ok


def _c3_t0(k: pd.DataFrame, trig_idx: int) -> bool:
    """C3@T0：非放量阴线（MA5 口径，indicators.py:1132-1141）"""
    o = float(k["开盘"].astype(float).values[trig_idx])
    c = float(k["收盘"].astype(float).values[trig_idx])
    vols = k["成交量"].astype(float).values
    if len(vols) < 6:
        return True
    ma5 = float(vols[max(0, trig_idx - 5):trig_idx].mean())
    reject = ma5 > 0 and vols[trig_idx] > ma5 * 1.5 and c < o
    return not reject


def replay_confirm(code: str, row: pd.Series, k: pd.DataFrame, variant: str) -> dict:
    """单笔确认时点变体回放（20d）"""
    entry = float(row["trigger"])
    stop = float(row["stop"])
    risk = float(row["risk"])
    if risk <= 0:
        return {"skip": "risk<=0"}
    dates = k["日期"].astype(str).str[:10].values
    highs = k["最高"].astype(float).values
    lows = k["最低"].astype(float).values
    closes = k["收盘"].astype(float).values
    opens = k["开盘"].astype(float).values
    sig_idx = _find_signal_index(k, pd.Timestamp(str(row["date"])[:10]))
    if sig_idx is None:
        return {"skip": "no_sig_idx"}
    end = min(sig_idx + HOLD, len(k) - 1)
    trig_idx = next((j for j in range(sig_idx + 1, end + 1)
                     if highs[j] >= entry), None)
    if trig_idx is None:
        return {"skip": "no_trigger"}
    if trig_idx + 1 > end:
        return {"skip": "hold_end_nospace"}
    a_ok, b_ok = _ab_check(k, trig_idx, entry)
    ab_ok = a_ok and b_ok
    # ── V0'：A/B + delay2 ──
    if variant == "V0":
        if not ab_ok:
            # A/B 失败 → reject（确认判定日收盘平仓——与 sim_trading R-053 同构）
            return {"exit": float(closes[trig_idx]), "exit_date": str(dates[trig_idx])[:10],
                    "r": _r(entry, float(closes[trig_idx]), risk), "confirm": "reject_ab"}
        v = half_position_confirm_delay2(k, entry, stop, trig_idx + 1, max_idx=end)
        used = v["conf_idx_used"]
        if v["stopped"]:
            return {"exit": stop, "exit_date": str(dates[used])[:10],
                    "r": _r(entry, stop, risk), "confirm": "stopped"}
        if v["reject"]:
            return {"exit": float(v["close"]), "exit_date": str(dates[used])[:10],
                    "r": _r(entry, float(v["close"]), risk), "confirm": "reject"}
        used_safe = min(used, len(dates) - 1)
        return _hold_out(k, entry, stop, risk, used_safe, end, dates,
                         f"confirmed@{used}")
    # ── T0：A/B + {A,B,C3@T0}（T0 收盘确认 → T+1 开盘补仓；失败 → T0 收盘平）──
    if variant == "T0":
        c3 = _c3_t0(k, trig_idx)
        if not (ab_ok and c3):
            return {"exit": float(closes[trig_idx]), "exit_date": str(dates[trig_idx])[:10],
                    "r": _r(entry, float(closes[trig_idx]), risk),
                    "confirm": "reject_t0" if not ab_ok else "reject_t0_c3"}
        # 确认 → 补仓（T+1 开盘价）；持有到出场（低点≤止损 → 止损，否则 20d 末）
        return _hold_out(k, entry, stop, risk, trig_idx, end, dates, "confirmed_t0")
    # ── V4：A/B + 严格首根（T+1 无 delay2）──
    if variant == "V4":
        if not ab_ok:
            return {"exit": float(closes[trig_idx]), "exit_date": str(dates[trig_idx])[:10],
                    "r": _r(entry, float(closes[trig_idx]), risk), "confirm": "reject_ab"}
        v = half_position_confirm_delay2(k, entry, stop, trig_idx + 1, max_idx=trig_idx + 1)
        used = v["conf_idx_used"]
        if v["stopped"]:
            return {"exit": stop, "exit_date": str(dates[used])[:10],
                    "r": _r(entry, stop, risk), "confirm": "stopped"}
        if v["reject"]:
            return {"exit": float(v["close"]), "exit_date": str(dates[used])[:10],
                    "r": _r(entry, float(v["close"]), risk), "confirm": "reject"}
        return _hold_out(k, entry, stop, risk, used, end, dates, "confirmed_strict")
    raise ValueError(f"未知变体 {variant}")


def _hold_out(k, entry, stop, risk, start, end, dates, confirm_tag) -> dict:
    """确认后持有：逐日低点≤止损 → 止损出场；否则 20d 末收盘出场"""
    lows = k["最低"].astype(float).values
    closes = k["收盘"].astype(float).values
    for j in range(start + 1, end + 1):
        if lows[j] <= stop:
            return {"exit": stop, "exit_date": str(dates[j])[:10],
                    "r": _r(entry, stop, risk), "confirm": confirm_tag}
    return {"exit": float(closes[end]), "exit_date": str(dates[end])[:10],
            "r": _r(entry, float(closes[end]), risk), "confirm": confirm_tag}


def main() -> int:
    sig = pd.read_csv(SIG, dtype={"code": str})
    trig = sig[sig["triggered_20d"] == 1]
    kc = KlineCache()
    # ── 门禁 1：V0（无 A/B）vs T8 r_20d（±0.005）──
    fails = checked = 0
    for _, row in trig.iterrows():
        k = kc.get(str(row["code"]))
        if k is None:
            continue
        r = replay_confirm(str(row["code"]), row, k, "V0")
        if r.get("skip"):
            continue
        if r.get("confirm", "").startswith("reject"):
            continue   # 引擎 T8 无 A/B——A/B reject 行不对账
        if abs(r["r"] - float(row["r_20d"])) > 0.005:
            fails += 1
            if fails <= 5:
                print(f"  门禁差: {row['code']} {row['date']} replay {r['r']:.4f} vs T8 {row['r_20d']:.4f}")
        checked += 1
    print(f"[门禁 1] V0(无A/B) 对账 {checked} 笔 | 不一致 {fails}")

    # ── 变体矩阵 ──
    results = {v: [] for v in ["V0", "T0", "V4"]}
    for _, row in trig.iterrows():
        k = kc.get(str(row["code"]))
        if k is None:
            continue
        for v in results:
            r = replay_confirm(str(row["code"]), row, k, v)
            if r.get("skip"):
                continue
            r["code"] = row["code"]
            r["date"] = row["date"]
            results[v].append(r)
    out = {}
    for v, rs in results.items():
        n = len(rs)
        rs_r = [r["r"] for r in rs]
        confirms = [r["confirm"] for r in rs]
        out[v] = {
            "n": n,
            "confirm_rate": round(sum(1 for c in confirms if c.startswith("confirmed")) / n, 3),
            "reject_rate": round(sum(1 for c in confirms if c.startswith("reject")) / n, 3),
            "avgR": round(float(np.mean(rs_r)), 3),
            "sumR": round(float(np.sum(rs_r)), 1),
            "win": round(float(np.mean([r > 0 for r in rs_r])), 3),
            "confirm_dist": {c: confirms.count(c) for c in set(confirms)}}
    # 增量集归因：T0 确认 ∩ V0 拒绝
    v0_map = {f"{r['code']}_{r['date']}": r for r in results["V0"]}
    t0_map = {f"{r['code']}_{r['date']}": r for r in results["T0"]}
    inc = []
    for key, r in t0_map.items():
        v0 = v0_map.get(key)
        if v0 and v0["confirm"].startswith("reject") and r["confirm"].startswith("confirmed"):
            inc.append(r)
    if inc:
        out["T0增补集"] = {"n": len(inc),
                           "avgR": round(float(np.mean([r["r"] for r in inc])), 3),
                           "sumR": round(float(np.sum([r["r"] for r in inc])), 1)}
    # 假确认诊断（T0 确认后 T+1 收盘 < 触发价）
    fake = []
    for key, r in t0_map.items():
        if not r["confirm"].startswith("confirmed"):
            continue
        k = kc.get(r["code"])
        if k is None:
            continue
        dates = k["日期"].astype(str).str[:10].values
        try:
            ei = next(i for i, d in enumerate(dates) if d == r["exit_date"])
        except StopIteration:
            continue
        trig_row = trig[(trig["code"] == r["code"]) & (trig["date"] == r["date"])]
        if trig_row.empty:
            continue
        entry = float(trig_row.iloc[0]["trigger"])
        if ei + 1 < len(dates) and float(k["收盘"].values[ei + 1]) < entry:
            fake.append(r)
    out["假确认(T0确认后T+1收盘<触发价)"] = {
        "n": len(fake),
        "avgR": round(float(np.mean([r["r"] for r in fake])), 3) if fake else None}
    (OUT / "r69c_confirm_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
