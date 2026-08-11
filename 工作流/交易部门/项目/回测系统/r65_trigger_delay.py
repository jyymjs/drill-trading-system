#!/usr/bin/env python3
"""R-065 触发延迟分档质量表（2026-08-12 · "3 天有效期"背书复算 + 持续埋伏依据）

背景：3 天有效期声称"69% 3 日内触发、3~5 天档质量最差"——原始分桶未入库、
今日复算对不上（覆盖率 61.3%）。本脚本正式落盘 V4 信号集（T8）的延迟分档表，
回答"3 天 vs 持续埋伏"的量化差距。

口径：信号日 → 首个 high ≥ trigger 的交易日数（0 = 当天突破）；分档
0 / 1~2 / 3~5 / >5 日；每档 avgR/胜率/占比 + ≤N 日累计覆盖率。

用法:
  python 回测系统/r65_trigger_delay.py
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
from 回测系统.tracking import _find_signal_index

SIG = _ROOT / "产出" / "输出" / "backtest_r43_t2_T8" / "signals.csv"
OUT = _ROOT / "产出" / "输出" / "实验" / "r57"
BANDS = [("0 日", 0, 0), ("1~2 日", 1, 2), ("3~5 日", 3, 5), (">5 日", 6, None)]


def main() -> int:
    sig = pd.read_csv(SIG, dtype={"code": str})
    trig = sig[sig["triggered_20d"] == 1]
    kc = KlineCache()
    delays: list[int] = []
    bands: dict[str, list[float]] = {b[0]: [] for b in BANDS}
    for _, row in trig.iterrows():
        df = kc.get(str(row["code"]))
        if df is None or df.empty:
            continue
        highs = df["最高"].astype(float).values
        sig_idx = _find_signal_index(df, pd.Timestamp(row["date"]))
        if sig_idx is None:
            continue
        trig_idx = next((j for j in range(sig_idx + 1, len(df))
                         if highs[j] >= row["trigger"]), None)
        if trig_idx is None:
            continue
        d = trig_idx - sig_idx - 1      # 延迟交易日（0 = 当天突破）
        delays.append(d)
        for label, lo, hi in BANDS:
            if (hi is None and d >= lo) or (lo <= d <= (hi if hi is not None else 0)):
                bands[label].append(float(row["r_20d"]))
                break
    n = len(delays)
    if n == 0:
        print("无有效触发样本")
        return 1
    d_arr = np.asarray(delays)
    out = {"n": n, "median_delay": int(np.median(d_arr)),
           "coverage": {f"<={k}日": round(float((d_arr <= k).mean()), 4)
                        for k in (1, 2, 3, 5)},
           "bands": {}}
    print(f"触发样本 {n} 笔 | 中位延迟 {np.median(d_arr):.0f} 日")
    print(f"覆盖率: ≤1日 {(d_arr<=1).mean()*100:.1f}% | ≤2日 {(d_arr<=2).mean()*100:.1f}% | "
          f"≤3日 {(d_arr<=3).mean()*100:.1f}% | ≤5日 {(d_arr<=5).mean()*100:.1f}%")
    print(f"\n| 延迟档 | 笔数 | 占比 | avgR | 胜率 |")
    print("|---|---|---|---|---|")
    for label, _, _ in BANDS:
        rs = bands[label]
        if not rs:
            continue
        r_arr = np.asarray(rs)
        out["bands"][label] = {"n": len(rs), "pct": round(len(rs) / n, 4),
                               "avgR": round(float(r_arr.mean()), 3),
                               "win": round(float((r_arr > 0).mean()), 4)}
        print(f"| {label} | {len(rs)} | {len(rs)/n*100:.1f}% | {r_arr.mean():+.2f}R | "
              f"{(r_arr>0).mean()*100:.0f}% |")
    (OUT / "trigger_delay_bands.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已落盘 → r57/trigger_delay_bands.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
