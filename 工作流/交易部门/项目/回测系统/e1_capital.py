"""E1 资金层复验（2026-08-13）：全集 vs 窄平台（宽度≤5%）过滤

10 万 × 0.025 × 999，7y（2019+）主口径；宽度复用 e1_width 同款计算。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))       # 项目/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 交易部门根

import duckdb
import numpy as np
import pandas as pd

from 回测系统.r44_position_grid import run_one  # noqa: E402

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
SIG = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出", "归档",
                   "旧回测-20260813", "backtest_r43_t2_T8", "signals.csv")


def main() -> int:
    from 分析决策.分析.indicators import all_indicators  # noqa: E402
    from 数据基础.duckdb.reader import compute_qfq, _to_cn_kline  # noqa: E402
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy  # noqa: E402

    sig = pd.read_csv(SIG, encoding="utf-8-sig", dtype={"code": str})
    trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)].copy()

    con = duckdb.connect(DB, read_only=True)
    strat = ZuanQianStrategy()
    widths = {}
    for i, (_, r) in enumerate(trig.iterrows(), 1):
        daily = con.execute(
            "SELECT date, open, high, low, close, vol, amount FROM daily "
            "WHERE symbol=? ORDER BY date", [str(r["code"])]).df()
        xdxr = con.execute(
            "SELECT date, fenhong, peigujia, songzhuangu, peigu FROM xdxr "
            "WHERE symbol=? AND category=1 ORDER BY date", [str(r["code"])]).df()
        if daily.empty:
            continue
        k = _to_cn_kline(compute_qfq(daily, xdxr if len(xdxr) else None)
                         .reset_index(drop=True))
        sub = k[k["日期"] <= pd.to_datetime(r["date"])].copy()
        if len(sub) < 60:
            continue
        sub.attrs["code"] = str(r["code"])
        sub = all_indicators(sub, needed_cols=strat.required_indicators)
        try:
            res = strat.prebreak_grade(sub)
        except Exception:  # noqa: BLE001
            continue
        th, tl = res.get("ty_high", 0) or 0, res.get("ty_low", 0) or 0
        widths[(str(r["code"]), r["date"])] = (th - tl) / tl if tl > 0 else np.nan
    con.close()
    print(f"宽度已算: {len(widths)} 笔", flush=True)

    narrow = trig[[(str(r["code"]), r["date"]) in widths and widths[(str(r["code"]), r["date"])] <= 0.05
                  for _, r in trig.iterrows()]]
    narrow_path = os.path.join(os.path.dirname(__file__), "..", "..", "产出",
                               "输出", "数据", "E1_窄平台_signals.csv")
    narrow.to_csv(narrow_path, index=False, encoding="utf-8-sig")
    print(f"窄平台集: {len(narrow)} 笔 → {narrow_path}", flush=True)

    for name, p in [("全集(T8)", SIG), ("窄平台≤5%", narrow_path)]:
        m, _ = run_one(p, 100000.0, 0.025, 999, min_date="2019-01-01", return_raw=True)
        print(f"[{name}] 成交 {m['n_exec']} | 收益 {m['total_ret_pct']:+.1f}% "
              f"| 回撤 {m['dd_peak_pct']:.1f}% | avgR {m['avg_r']:+.3f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
