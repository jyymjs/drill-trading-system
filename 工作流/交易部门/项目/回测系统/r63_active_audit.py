#!/usr/bin/env python3
"""R-063 主动出场割肉/逃顶审计（2026-08-12 · R-062 两口径分离的数据背书）

老板要求：回测当前策略，查看主动出场的"割肉"和"逃顶"数据——用数据回答
"严格执行系统（只长持仓 ≥21 根自动触发主动出场）"是否合理。

两组口径对比：
  A 组（R-062 现行）：E 无限期，主动出场 = 切片 + 持仓 ≥21 根才触发（r57.replay）
  B 组（R-060 对照）：E 无限期，主动出场 = 全量 df（短持仓也触发——被 R-062 排除）

对每笔主动出场单：
  - 持仓天数（触发日 → 出场日，交易日）
  - 后续 20 交易日最高价 vs 出场价 → 砍肉（卖早）/ 逃顶（卖对）
  - 砍肉平均"少赚"（后续高点 − 出场价 → R）；逃顶平均"躲过"（出场价 − 后续高点）

用法:
  python 回测系统/r63_active_audit.py
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

from 回测系统.r57_exit_matrix import GROUPS, _KC, load_trig, replay
from 回测系统.replay_cache import KlineCache
from 回测系统.tracking import _find_signal_index

LOOKBACK_WINDOW = 20        # 后续验证窗口（交易日）
PRICE_TOL = 0.01            # 价差容差（元）


def audit(rows: list[dict], label: str) -> dict:
    """对主动出场单做割肉/逃顶审计（触发日从 K 线反推）"""
    kc = KlineCache()
    out = []
    for r in rows:
        df = kc.get(r["code"])
        if df is None:
            continue
        dates = df["日期"].astype(str).str[:10].values
        highs = df["最高"].astype(float).values
        closes = df["收盘"].astype(float).values
        try:
            exit_idx = next(i for i, d in enumerate(dates) if d == r["exit_date"])
            sig_idx = _find_signal_index(df, pd.Timestamp(r["date"]))
        except (StopIteration, TypeError):
            continue
        if sig_idx is None or exit_idx <= sig_idx:
            continue
        # 触发日 = 信号日后第一个 high >= trigger（与 replay 同逻辑）
        trig_idx = next((j for j in range(sig_idx + 1, exit_idx + 1)
                         if highs[j] >= r["trigger"]), None)
        if trig_idx is None:
            continue
        hold_days = exit_idx - trig_idx
        # 后续窗口：20 交易日（不足 5 根不判）
        fut_end = min(exit_idx + 1 + LOOKBACK_WINDOW, len(dates))
        if fut_end - exit_idx - 1 < 5:
            continue
        fut_high = float(np.max(highs[exit_idx + 1:fut_end]))
        fut_close = float(closes[fut_end - 1])     # 窗口终点收盘（真卖早/卖对判定）
        exit_px = float(r["exit"])
        risk = float(r["risk"])
        if fut_close > exit_px + PRICE_TOL:
            kind = "砍肉"     # 真卖早：20 天后收盘仍更高
        else:
            kind = "逃顶"     # 卖对：20 天后收盘 ≤ 出场价
        gap_r = (fut_high - exit_px) / risk if risk > 0 else 0.0   # 高点口径（信息参考）
        out.append({"code": r["code"], "date": r["date"], "kind": kind,
                    "hold_days": hold_days, "r": r["r"], "exit": exit_px,
                    "fut_high": fut_high, "fut_close": fut_close, "gap_r": gap_r})
    n = len(out)
    if n == 0:
        return {"label": label, "n": 0}
    hurts = [o for o in out if o["kind"] == "砍肉"]
    escapes = [o for o in out if o["kind"] == "逃顶"]
    return {"label": label, "n": n,
            "砍肉": len(hurts), "逃顶": len(escapes),
            "砍肉率": round(len(hurts) / n, 3),
            "avg_hold_days": round(float(np.mean([o["hold_days"] for o in out])), 1),
            "short_hold(<21天)": len([o for o in out if o["hold_days"] < 21]),
            "long_hold(>=21天)": len([o for o in out if o["hold_days"] >= 21]),
            "砍肉组": {"avg_r": round(float(np.mean([o["r"] for o in hurts])), 2),
                       "avg_高点少赚R": round(float(np.mean([o["gap_r"] for o in hurts])), 2),
                       "avg_hold": round(float(np.mean([o["hold_days"] for o in hurts])), 1)},
            "逃顶组": {"avg_r": round(float(np.mean([o["r"] for o in escapes])), 2),
                       "avg_高点少赚R": round(float(np.mean([o["gap_r"] for o in escapes])), 2),
                       "avg_hold": round(float(np.mean([o["hold_days"] for o in escapes])), 1)}}


def _collect(use_060: bool) -> list[dict]:
    """收集 E 无限期主动出场单（use_060=True → R-060 全量口径）"""
    trig = load_trig()
    rows = []
    for _, row in trig.iterrows():
        df = _KC.get(str(row["code"]))
        if df is None or df.empty:
            continue
        if use_060:
            # R-060 对照口径：复制 replay 但主动出场用全量 df
            from 分析决策.分析.indicators import half_position_confirm_delay2
            from 分析决策.风控.exit_manager import Position, evaluate_exit
            from 回测系统.tracking import _trade_cost
            dates = df["日期"].astype(str).str[:10].values
            highs = df["最高"].astype(float).values
            lows = df["最低"].astype(float).values
            closes = df["收盘"].astype(float).values
            sig_idx = _find_signal_index(df, pd.Timestamp(row["date"]))
            if sig_idx is None:
                continue
            trig_idx = next((j for j in range(sig_idx + 1, len(df))
                             if highs[j] >= row["trigger"]), None)
            if trig_idx is None:
                continue
            entry, stop, risk = float(row["trigger"]), float(row["stop"]), float(row["risk"])
            v = half_position_confirm_delay2(df, entry, stop, trig_idx + 1, max_idx=len(df) - 1)
            used = v["conf_idx_used"]
            if v["stopped"] or v["reject"]:
                continue
            pos = Position(symbol=row["code"], direction="long", market="stock",
                           entry_price=entry, initial_stop=stop, current_stop=stop,
                           volume=100, grade_at_entry=str(row["grade"]))
            pos.highest_price = entry
            pos.lowest_price = entry
            for j in range(used + 1, len(df)):
                day_df = df.iloc[trig_idx:j + 1]
                pos.highest_price = max(pos.highest_price, float(highs[j]))
                pos.lowest_price = min(pos.lowest_price, float(lows[j]))
                ev = evaluate_exit(pos, day_df, active_df=df, **GROUPS["E"])
                if ev["stop_update"] and ev["stop_update"] > pos.current_stop:
                    pos.current_stop = ev["stop_update"]
                if ev["should_exit"]:
                    cost = _trade_cost(entry, ev["exit_price"] or float(closes[j]), True, 1.0)
                    rr = ((ev["exit_price"] or float(closes[j])) - entry - cost) / risk if risk > 0 else 0.0
                    rows.append({"code": row["code"], "date": row["date"],
                                 "trigger": float(row["trigger"]), "risk": float(row["risk"]),
                                 "exit": ev["exit_price"] or float(closes[j]),
                                 "exit_date": str(dates[j])[:10], "r": rr,
                                 "reason": ev["reason"]})
                    break
            continue
        r = replay(row, df, GROUPS["E"], None)
        if r.get("skip"):
            continue
        if "主动出场" not in r.get("reason", ""):
            continue
        rows.append({"code": row["code"], "date": row["date"],
                     "trigger": float(row["trigger"]), "risk": float(row["risk"]),
                     "exit": float(r["exit"]), "exit_date": r["exit_date"],
                     "r": r["r"], "reason": r["reason"]})
    return rows


def main() -> int:
    print("正在收集 A 组（R-062 现行口径：≥21 根才触发）……")
    rep_a = _collect(use_060=False)
    a = audit(rep_a, "A 组（R-062 现行：长持仓才触发）")
    print("正在收集 B 组（R-060 对照口径：全量触发）……")
    rep_b = _collect(use_060=True)
    b = audit(rep_b, "B 组（R-060 对照：短持仓也触发）")
    out = {"A_062": a, "B_060": b}
    (Path(_ROOT) / "产出" / "输出" / "实验" / "r57" / "active_audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
