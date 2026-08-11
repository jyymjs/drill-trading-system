#!/usr/bin/env python3
"""R-069 实验 D：组合最优（T-020 保留 × E 无限期）× 对比现行

最优组合 = 现行 C23（10%, 0.5-3.0）+ T-020 保留（信号日量比 >1.5）+ 确认时点
（C 实验结论：T0 微优）——本脚本验证 T-020 保留对 V4 主口径（E 无限期）的影响：
  D1 现行（无 T-020）：E 无限期 8401 资金层（对照 r58 定版数字）
  D2 T-020 保留：E 无限期 8401 资金层（T-020 过滤后触发集）
口径：8401 × 0.025 × 999，26y/7y/3y（与 V4 定版同口径）

用法:
  python 回测系统/r69d_combo.py
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

import pandas as pd

from 回测系统.replay_cache import KlineCache
from 回测系统.r57_exit_matrix import GROUPS, OUT, _RC, replay
from 回测系统.r48_grid import _build_enriched_cache
from 回测系统.r44_position_grid import run_one
from 回测系统.tracking import _find_signal_index

SIG = _ROOT / "产出" / "输出" / "backtest_r43_t2_T8" / "signals.csv"
WINDOWS = {"26y": None, "7y": "2019-01-01", "3y": "2023-01-01"}


def _sig_vol_ratio(code: str, sig_date: str, kc: KlineCache):
    df = kc.get(code)
    if df is None or df.empty:
        return None
    vol = df["成交量"].astype(float).values
    dates = df["日期"].astype(str).str[:10].values
    t = next((i for i, d in enumerate(dates) if d == sig_date), None)
    if t is None or t <= 0:
        return None
    ref = vol[max(0, t - 20):t]
    return float(vol[t]) / float(ref.mean()) if ref.mean() > 0 else None


def run_inf(group_key: str, switches: dict, trig_rows: pd.DataFrame,
            kc: KlineCache, label: str) -> dict:
    """E 无限期重放（过滤后触发集）→ 资金层 3 窗"""
    results = []
    for _, row in trig_rows.iterrows():
        k = kc.get(str(row["code"]))
        if k is None:
            continue
        r = replay(row, k, switches, None)
        if r.get("skip"):
            continue
        r["code"] = row["code"]
        r["date"] = row["date"]
        results.append(r)
    rep_map = {f"{r['code']}_{r['date']}": r for r in results}
    sig = pd.read_csv(SIG, dtype={"code": str})
    # 清空触发列（防旧 triggered 残留——r60 同类 bug：不清则脏行混入）
    for c in ("triggered_20d", "entry_20d", "exit_20d", "exit_date_20d",
              "r_20d", "stopped_20d"):
        if c in sig.columns:
            sig[c] = 0 if c in ("triggered_20d", "stopped_20d") else ""
    for i, row in sig.iterrows():
        rp = rep_map.get(f"{row['code']}_{row['date']}")
        if rp is None:
            continue
        sig.at[i, "triggered_20d"] = 1
        sig.at[i, "entry_20d"] = row["trigger"]
        sig.at[i, "exit_20d"] = round(float(rp["exit"]), 4)
        sig.at[i, "exit_date_20d"] = rp["exit_date"]
        sig.at[i, "r_20d"] = round(float(rp["r"]), 4)
        sig.at[i, "stopped_20d"] = int(rp["stopped"])
    csv_path = OUT / f"r69d_{label}.csv"
    # enriched 列（复用 SIG 的）
    eng = pd.read_csv(_build_enriched_cache(str(SIG)), dtype={"code": str})
    sig = sig.merge(eng[["code", "date", "vol_ratio", "mom20"]],
                    on=["code", "date"], how="left")
    sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
    out = {}
    for wtag, md in WINDOWS.items():
        m, _ = run_one(str(csv_path), 8401.0, 0.025, 999, min_date=md,
                       return_raw=True, enriched_path=str(csv_path))
        out[wtag] = {"ret": round(m["total_ret_pct"], 1), "dd": round(m["dd_peak_pct"], 1),
                     "n": m["n_exec"], "avgR": round(m["avg_r"], 3)}
    return out


def main() -> int:
    _RC.set_persist_dir(OUT / "cache")
    sig = pd.read_csv(SIG, dtype={"code": str})
    trig = sig[sig["triggered_20d"] == 1]
    kc = KlineCache()
    # D1 现行（无 T-020）：E 无限期 8401（对账 r58 定版）
    print("D1 现行（无 T-020）E 无限期……")
    d1 = run_inf("E", GROUPS["E"], trig, kc, "E_not020")
    # D2 T-020 保留（信号日量比 >1.5）+ D3 阈值 1.2（老板拍板 R-070）
    result = {"D1_现行(无T020)": d1}
    for th, label in [(1.5, "T020_1.5"), (1.2, "T020_1.2")]:
        print(f"D{label} T-020 保留（>{th}）E 无限期……")
        keep = []
        for _, row in trig.iterrows():
            sr = _sig_vol_ratio(str(row["code"]), str(row["date"])[:10], kc)
            if sr is not None and sr > th:
                keep.append(row)
        print(f"  触发集 {len(keep)} 笔（26y）")
        result[f"D{label}"] = run_inf("E", GROUPS["E"], pd.DataFrame(keep), kc, f"E_t{label}")
    (OUT / "r69d_combo_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
