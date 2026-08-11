#!/usr/bin/env python3
"""R-052 资金层对比：0.5R 分步 vs 1R 直开（总资产口径回撤，R-041 缺口）

矩阵（12 格）：
  8401×0.025×999：V0(0.5R, r43_t2 signals) vs 1R(r52_1r signals) × {26年/近7年/近3年}
  30k×0.025×999：V0 vs 1R × 26 年（资金充足对照）
  8401×0.025×5：V0 vs 1R × 26 年（实盘手动 5 仓档）
  8401+月注入3000×999：V0 vs 1R × 26 年（注入路径）

锚点门禁：V0 8401 26 年 = +1534.0%/-13.8%（R-050/051 已核实）等——零差才可信。
回撤一律总资产口径（r44.run_one 内置 build_total_asset_curve）。
"""
from __future__ import annotations

import argparse
import json
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

SIG_05 = "产出/输出/backtest_r43_t2/signals.csv"   # phase_in（0.5R）
SIG_1R = "产出/输出/backtest_r52_1r/signals.csv"   # 直开（1R）


def run_cell(signals: str, capital: float, max_pos: int, min_date: str,
             half: bool, monthly: float = 0.0) -> dict:
    m, res = run_one(signals, capital, 0.025, max_pos,
                     monthly_inject=monthly,
                     min_date=min_date or None,
                     risk_growth=bool(monthly),
                     return_raw=True,
                     enriched_path=str(_build_enriched_cache(signals)),
                     half_phase=half)
    m["signals"] = "r43_t2" if "r43_t2" in signals else "r52_1r"
    m["half_phase"] = half
    m["n_confirm_shortfall"] = int(res.get("n_confirm_shortfall", 0) or 0)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="产出/输出/实验/r52")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cells = [
        # (id, signals, capital, max_pos, min_date, half, monthly)
        ("V0_26y", SIG_05, 8401.0, 999, "", True, 0.0),
        ("R1_26y", SIG_1R, 8401.0, 999, "", False, 0.0),
        ("V0_7y", SIG_05, 8401.0, 999, "2019-01-01", True, 0.0),
        ("R1_7y", SIG_1R, 8401.0, 999, "2019-01-01", False, 0.0),
        ("V0_3y", SIG_05, 8401.0, 999, "2023-01-01", True, 0.0),
        ("R1_3y", SIG_1R, 8401.0, 999, "2023-01-01", False, 0.0),
        ("V0_30k", SIG_05, 30000.0, 999, "", True, 0.0),
        ("R1_30k", SIG_1R, 30000.0, 999, "", False, 0.0),
        ("V0_5pos", SIG_05, 8401.0, 5, "", True, 0.0),
        ("R1_5pos", SIG_1R, 8401.0, 5, "", False, 0.0),
        ("V0_inj", SIG_05, 8401.0, 999, "", True, 3000.0),
        ("R1_inj", SIG_1R, 8401.0, 999, "", False, 3000.0),
    ]
    results = []
    gate = []
    for cid, sig, cap, mp, md, half, mth in cells:
        r = run_cell(sig, cap, mp, md, half, mth)
        r["id"] = cid
        results.append(r)
        print(f"{cid}: 收益 {r['total_ret_pct']:>9.1f}% | 回撤 {r['dd_peak_pct']:>6.1f}% | "
              f"成交 {r['n_exec']:>4} | avgR {r['avg_r']:.3f} | selfcheck {r.get('selfcheck_pass')}")
        (out_dir / f"{cid}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                             encoding="utf-8")

    # 锚点门禁（V0 现行行为与 R-050/051 已知数字零差）
    anchors = {"V0_26y": (1534.0, -13.8, 946), "V0_7y": (801.1, -16.3, 570),
               "V0_3y": (302.2, -12.0, 262)}
    for cid, (ret, dd, ne) in anchors.items():
        r = next(x for x in results if x["id"] == cid)
        if abs(r["total_ret_pct"] - ret) > 0.5 or abs(r["dd_peak_pct"] - dd) > 0.5 \
                or r["n_exec"] != ne:
            gate.append(f"{cid}: {r['total_ret_pct']}/{r['dd_peak_pct']}/{r['n_exec']} "
                        f"vs 期望 {ret}/{dd}/{ne}")
    print("[r52-capital] 锚点门禁:", "✅ 零差" if not gate else f"❌ {gate}")
    return 0 if not gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
