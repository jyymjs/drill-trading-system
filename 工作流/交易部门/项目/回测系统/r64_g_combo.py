#!/usr/bin/env python3
"""R-064 G 组对照：平保+移动获利+TTP（无主动出场）——老板问"放弃主动出场只靠其他止盈"

对比矩阵（无限期，8401×0.025×无限制）：
  B 纯平保 | C 平保+移动 | F 平保+TTP | G 平保+移动+TTP（无主动）| E 全开（V4 现行）

用法:
  python 回测系统/r64_g_combo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402

from 回测系统.r57_exit_matrix import OUT, SIG, _RC, replay_all  # noqa: E402
from 回测系统.r48_grid import _build_enriched_cache  # noqa: E402
from 回测系统.r44_position_grid import run_one  # noqa: E402

# G 组 = 平保 + 移动获利 + TTP（无主动出场）
# H 组 = 平保 + 主动 + TTP（无移动获利）
COMBO = {
    "G": dict(enable_breakeven=True, enable_trailing=True,
              enable_active=False, enable_ttp=True),
    "H": dict(enable_breakeven=True, enable_trailing=False,
              enable_active=True, enable_ttp=True),
}
WINDOWS = {"26y": None, "7y": "2019-01-01", "3y": "2023-01-01"}


def main() -> int:
    _RC.set_persist_dir(OUT / "cache")
    enriched_a = pd.read_csv(_build_enriched_cache(str(OUT / "signals_A.csv")),
                             dtype={"code": str})[["code", "date", "vol_ratio", "mom20"]]
    for gname, sw in COMBO.items():
        print(f"{gname} 组无限期重放（并行）……")
        rep = replay_all(sw, None, verbose=False)
        sig = pd.read_csv(SIG, dtype={"code": str})
        rep_map = {f"{r['code']}_{r['date']}": r for r in rep}
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
        csv_path = OUT / f"signals_{gname}_inf.csv"
        sig = sig.merge(enriched_a, on=["code", "date"], how="left")
        sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"{gname} 组无限期资金层：")
        for wtag, md in WINDOWS.items():
            m, _ = run_one(str(csv_path), 8401.0, 0.025, 999, min_date=md,
                           return_raw=True, enriched_path=str(csv_path))
            print(f"  {gname}_{wtag}: +{m['total_ret_pct']:.1f}% / -{m['dd_peak_pct']:.1f}% / "
                  f"n={m['n_exec']} / avgR={m['avg_r']:.2f}")
    print("\n对比（现有组，r58 复算）:")
    print("  B 纯平保:   26y -57.1%/-77.2% | 7y -36.2%/-69.5% | 3y -27.5%/-51.9%")
    print("  F 平保+TTP: 26y +1333.5%/-22.6% | 7y +775.7%/-21.0% | 3y +170.3%/-15.3%")
    print("  E 全开:     26y +1447.2%/-16.7% | 7y +997.7%/-15.6% | 3y +336.5%/-13.6%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
