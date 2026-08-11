#!/usr/bin/env python3
"""R-060 无限期重放生成（2026-08-12 · R-060 修复后 B/E/F 无限期 _inf CSV 重新生成）

R-060（主动出场 active_df 全量）改变了 D/E 组的出场判定 → 无限期产物
signals_{B,E,F}_inf.csv 需重新生成（r57 主脚本只跑 20d 矩阵）。

用法:
  python 回测系统/r60_inf_replay.py        # B/E/F 三组 hold=None 重放写回
  python 回测系统/r58_inf_capital.py       # 接着跑资金层复算对账
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

from 回测系统.r57_exit_matrix import GROUPS, OUT, SIG, _RC, replay_all  # noqa: E402


def main() -> int:
    _RC.set_persist_dir(OUT / "cache")   # R-061 磁盘缓存（与 r57 共享）
    for gname in ["B", "E", "F"]:
        rep = replay_all(GROUPS[gname], None, verbose=True)
        sig = pd.read_csv(SIG, dtype={"code": str})
        rep_map = {f"{r['code']}_{r['date']}": r for r in rep}
        n_written = 0
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
            n_written += 1
        csv_path = OUT / f"signals_{gname}_inf.csv"
        sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"  {gname} 无限期重放写回 {n_written} 笔 → {csv_path.name}")
    print("完成——下一步: python 回测系统/r58_inf_capital.py（资金层复算对账）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
