#!/usr/bin/env python3
"""R-058 V4 无限期资金层复算（2026-08-12 · 交易部审核 P0-1：定版数字必须可复算）

输入：r57/signals_{B,E,F}_inf.csv（无限期重放写回，33 列三窗格式）
输出：26/7/3 年资金层（r44.run_one 同源）+ 蒙卡 + 对账 V4 定版数字
  （E 无限期：近7年 +997.7%/-15.6%；B 无限期：26年 -57.1%/-77.2%）
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402

from 回测系统.r44_position_grid import run_one  # noqa: E402
from 回测系统.r48_grid import _build_enriched_cache  # noqa: E402

OUT = _ROOT / "产出" / "输出" / "实验" / "r57"
WINDOWS = {"26y": None, "7y": "2019-01-01", "3y": "2023-01-01"}
ANCHORS = {
    "B_26y": (-57.1, -77.2), "B_7y": (-36.2, -69.5), "B_3y": (-27.5, -51.9),
    "E_26y": (1447.2, -16.7), "E_7y": (997.7, -15.6), "E_3y": (336.5, -13.6),
    "F_26y": (1333.5, -22.6), "F_7y": (775.7, -21.0), "F_3y": (170.3, -15.3),
}


def main() -> int:
    results, fails = {}, []
    # R-061：三组 _inf CSV 三元组 (code,date,trigger) 相同 → enrich 列构建一次复制合并。
    # 注意 run_one 的 enriched_path 语义 = 数据源本身（2026-08-12 踩坑：共享 enriched
    # 路径会全部读第一组数据——E/F 复算出 B 组结果；各组 CSV 合并 enrich 列后自当数据源）。
    import hashlib as _hl
    enrich_cols = None
    ref_key = None
    for gname in ["B", "E", "F"]:
        csv_path = OUT / f"signals_{gname}_inf.csv"
        if not csv_path.exists():
            print(f"❌ 缺 {csv_path.name}——需先跑无限期重放")
            continue
        sig = pd.read_csv(csv_path, dtype={"code": str})
        _h = _hl.sha256()
        for c in ("code", "date", "trigger"):
            _h.update(sig[c].astype(str).str.cat(sep="\x1f").encode("utf-8", errors="replace"))
        _key = _h.hexdigest()[:16]
        if ref_key is None:
            ref_key = _key
            enrich_cols = pd.read_csv(_build_enriched_cache(str(csv_path)),
                                      dtype={"code": str})[["code", "date", "vol_ratio", "mom20"]].copy()
        elif _key != ref_key:
            print(f"⚠️ {gname} 组三元组与首组不一致——独立构建 enrich")
            enrich_cols = pd.read_csv(_build_enriched_cache(str(csv_path)),
                                      dtype={"code": str})[["code", "date", "vol_ratio", "mom20"]].copy()
        sig = sig.merge(enrich_cols, on=["code", "date"], how="left")
        sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
        for wtag, md in WINDOWS.items():
            m, _ = run_one(str(csv_path), 8401.0, 0.025, 999, min_date=md,
                           return_raw=True, enriched_path=str(csv_path))
            key = f"{gname}_{wtag}"
            results[key] = {"ret": m["total_ret_pct"], "dd": m["dd_peak_pct"],
                            "n": m["n_exec"], "avgR": m["avg_r"]}
            a = ANCHORS.get(key)
            if a and (abs(m["total_ret_pct"] - a[0]) > 2.0
                      or abs(m["dd_peak_pct"] - a[1]) > 2.0):
                fails.append(f"{key}: 复算 {m['total_ret_pct']:.1f}%/{m['dd_peak_pct']:.1f}%"
                             f" vs 定版 {a[0]}%/{a[1]}%")
            print(f"  {key}: {m['total_ret_pct']:+.1f}% / {m['dd_peak_pct']:.1f}% / {m['n_exec']}笔")
        # 蒙卡（成交 R 序列 ± 重采样，seed=2024）
        rng = random.Random(2024)
        sig = pd.read_csv(csv_path, dtype={"code": str})
        rs = sig[sig["triggered_20d"] == 1]["r_20d"].dropna().astype(float).tolist()
        finals = []
        for _ in range(10000):
            s = 0.0
            for r in rs:
                s += r if rng.random() < 0.5 else -r
            finals.append(s)
        finals_s = sorted(finals)
        results[f"{gname}_mc"] = {"n": len(rs),
                                  "median": finals_s[len(finals_s) // 2],
                                  "p5": finals_s[int(len(finals_s) * 0.05)],
                                  "win": sum(1 for f in finals if f > 0) / 10000}
    (OUT / "inf_capital_recalc.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
    print("\n对账:", "✅ 全部吻合" if not fails else f"❌ {fails}")
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(main())
