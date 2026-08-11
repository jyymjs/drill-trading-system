#!/usr/bin/env python3
"""R-053 触发笔开仓日量比分布验证 v2（2026-08-11）

v1 教训：fetcher 缓存 = 前复权，signals.csv trigger = 引擎口径（duckdb 不复权）——
跨口径比较触发日必错（600058：2014 信号 × 2026 缓存价格）。
v2 教训：引擎 provider.load → read_kline（**qfq 自算**）——引擎 trigger/触发日全为前复权口径；
直接读 duckdb 原始价同样错。v3 用 read_kline（qfq，引擎同源）。

口径（对齐引擎 tracking.py:305-317）：
- 触发日 = 信号日后第一根 最高 ≥ 触发价（盘中穿越，qfq 口径）
- 量比 = 触发日量 / 触发日前 20 日均量
- dn_confirm=1.5 过滤后，triggered_20d==1 的笔应 100% 量比 >1.5
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

from 数据基础.duckdb.reader import read_kline  # noqa: E402

SIG = _ROOT / "产出" / "输出" / "backtest_r43_t2" / "signals.csv"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["filtered", "all"], default="filtered",
                    help="filtered=dn_confirm 过滤后触发笔（默认）；all=全量信号（未过滤，A-3 证据）")
    args = ap.parse_args()

    sig = pd.read_csv(SIG, dtype={"code": str})
    if args.mode == "filtered":
        trig = sig[sig["triggered_20d"] == 1]
        label = "20d 触发笔（dn_confirm=1.5 过滤后）"
    else:
        trig = sig
        label = f"全量信号（{len(sig)} 笔，未过滤）"
    print(f"{label}: {len(trig)}")

    ratios, skipped, no_break = [], 0, 0
    bad = []
    cache: dict[str, pd.DataFrame] = {}
    for _, row in trig.iterrows():
        code, sig_date = row["code"], str(row["date"])[:10]
        trigger = float(row["trigger"])
        df = cache.get(code)
        if df is None:
            df = read_kline(code, shared=True)  # qfq，引擎同源
            cache[code] = df
        if df is None or df.empty:
            skipped += 1
            continue
        dates = df["日期"].astype(str).str[:10].values
        highs = df["最高"].astype(float).values
        vols = df["成交量"].astype(float).values
        idxs = [i for i, d in enumerate(dates) if d > sig_date]
        if not idxs:
            skipped += 1
            continue
        brk = next((i for i in idxs if highs[i] >= trigger), None)
        if brk is None:
            no_break += 1
            continue
        if brk == 0:
            skipped += 1
            continue
        ref = vols[max(0, brk - 20):brk]
        if len(ref) == 0:
            skipped += 1
            continue
        ref_mean = ref.mean()
        if ref_mean <= 0:
            skipped += 1
            continue
        ratio = vols[brk] / ref_mean
        ratios.append(ratio)
        if ratio <= 1.5:
            bad.append((code, dates[brk], round(ratio, 2), round(trigger, 2), sig_date))

    import statistics as st

    ratios_s = sorted(ratios)
    n = len(ratios_s)
    print(f"可算量比: {n} | 跳过(数据不足): {skipped} | 无突破(信号后无高≥触发价): {no_break}")
    if n:
        print(f"量比分布: min {ratios_s[0]:.2f} | p25 {ratios_s[n//4]:.2f} | "
              f"中位 {st.median(ratios_s):.2f} | p75 {ratios_s[3*n//4]:.2f} | max {ratios_s[-1]:.2f}")
        low = [r for r in ratios_s if r <= 1.5]
        print(f"量比≤1.5: {len(low)} 笔 ({len(low)/n:.1%})"
              f"{' ❌ 回测 dn_confirm 过滤有漏' if low else ' ✅ 100% 达标（dn_confirm=1.5 过滤生效，与实盘拟加确认不冲突）'}")
        if bad:
            print("不达标明细（code/触发日/量比/触发价/信号日）:")
            for b in bad[:20]:
                print(f"  {b}")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
