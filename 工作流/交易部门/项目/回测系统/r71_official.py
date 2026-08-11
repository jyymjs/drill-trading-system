#!/usr/bin/env python3
"""R-071 V4 官方数字验证（T-020 阈值 1.2 纳入回测 · R-070 落地验证）

1. 官方数字：1.2 口径 E 无限期 8401 三窗（r69d D3 复核——门禁锚点 D1=定版）
2. P2-1：10 万口径 1.2 vs 1.5 交叉（防资金档依赖）
3. P2-2：7y 新增 66 笔（1.2 vs 1.5）贡献分布（E 组合右偏稳健性）
4. P2-3：1.2-1.5 区间触发日放量率（dn_confirm 执行差异——用 r69ab B2 交叉数据）
5. P2-4：R-069 D 表补录 1.2 档（改报告）

用法:
  python 回测系统/r71_official.py
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

from 回测系统.r44_position_grid import run_one

OUT = _ROOT / "产出" / "输出" / "实验" / "r57"


def main() -> int:
    result = {}
    # ── 官方数字（D3 1.2 档复核 + D1 门禁锚点）──
    d = json.load(open(OUT / "r69d_combo_result.json", encoding="utf-8"))
    result["official_1.2"] = d["DT020_1.2"]
    result["anchor_no020"] = d["D1_现行(无T020)"]
    # ── P2-1：10 万口径 1.2 vs 1.5 交叉 ──
    cap10 = {}
    for label, f in [("1.5", "r69d_E_tT020_1.5.csv"), ("1.2", "r69d_E_tT020_1.2.csv")]:
        p = OUT / f
        m, _ = run_one(str(p), 100_000.0, 0.025, 999, min_date="2019-01-01",
                       return_raw=True, enriched_path=str(p))
        cap10[label] = {"ret": round(m["total_ret_pct"], 1), "dd": round(m["dd_peak_pct"], 1),
                        "n": m["n_exec"]}
    result["P2-1_10万口径_7y"] = cap10
    # ── P2-2：7y 新增笔（1.2 vs 1.5）贡献分布 ──
    e15 = pd.read_csv(OUT / "r69d_E_tT020_1.5.csv", dtype={"code": str})
    e12 = pd.read_csv(OUT / "r69d_E_tT020_1.2.csv", dtype={"code": str})
    t15 = set(zip(e15[e15["triggered_20d"] == 1]["code"],
                  e15[e15["triggered_20d"] == 1]["date"]))
    t12 = set(zip(e12[e12["triggered_20d"] == 1]["code"],
                  e12[e12["triggered_20d"] == 1]["date"]))
    new_rows = e12[(e12["triggered_20d"] == 1) &
                   e12.apply(lambda r: (r["code"], r["date"]) in t12 - t15, axis=1)]
    new7 = new_rows[new_rows["date"].astype(str) >= "2019-01-01"]
    rs = new7["r_20d"].dropna().astype(float)
    result["P2-2_新增笔"] = {
        "n_26y": int(len(new_rows)), "n_7y": int(len(rs)),
        "sumR_7y": round(float(rs.sum()), 1),
        "avgR_7y": round(float(rs.mean()), 3) if len(rs) else None,
        "win_7y": round(float((rs > 0).mean()), 3) if len(rs) else None,
        "top3_贡献占比": round(float(rs.nlargest(3).sum() / rs.sum()), 3) if len(rs) and rs.sum() != 0 else None,
        "中位R": round(float(rs.median()), 3) if len(rs) else None}
    # ── P2-3：1.2-1.5 区间触发日放量率（r69ab B2 交叉）──
    ab = json.load(open(OUT / "r69_ab_result.json", encoding="utf-8"))
    cross = ab["B"]["cross"].get("1.2-1.5", {})
    result["P2-3_1.2-1.5区间"] = {
        "触发日放量率": cross.get("trig_vol_ok"),
        "结论": ("dn_confirm 执行差异可忽略（触发日放量率 ≥99%）" if
                (cross.get("trig_vol_ok") or 0) >= 0.99 else "需报告标注执行差异")}
    (OUT / "r71_official_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
