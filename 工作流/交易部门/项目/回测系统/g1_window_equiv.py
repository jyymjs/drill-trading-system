"""R-080 G1 窗口等价性验证（2026-08-13）

问题：定参段（2020-2022）评级需历史窗口。评级函数内部搜索型函数
（_detect_consolidation_phase_v2 回溯 20 start×120 根 ≈ n-140、_grade_dl n-90、
_find_last_ty_index n-30、SF dl_start-60 ≈ n-200）——理论 ≥250 根窗口与全量历史
评级逐位一致。本脚本实证：采样股票 × 多日期，全量评级 vs 250 根截断评级对比
（grade/trigger/stop/DN 列全一致 → 截断窗口可用）。

通过后 g1_calib_signals.py 用 250 根窗口生成定参段信号（内存友好）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
SAMPLES = 25          # 采样股票数
DATES = ["20201231", "20220630", "20220930"]   # 定参段多日期抽查


def read_full(symbol: str) -> pd.DataFrame | None:
    con = duckdb.connect(DB, read_only=True)
    try:
        daily = con.execute(
            "SELECT date, open, high, low, close, vol, amount FROM daily "
            "WHERE symbol=? ORDER BY date", [symbol]).df()
        xdxr = con.execute(
            "SELECT date, fenhong, peigujia, songzhuangu, peigu FROM xdxr "
            "WHERE symbol=? AND category=1 ORDER BY date", [symbol]).df()
    finally:
        con.close()
    if daily is None or daily.empty:
        return None
    from 数据基础.duckdb.reader import compute_qfq, _to_cn_kline
    k = compute_qfq(daily, xdxr if len(xdxr) else None)
    return _to_cn_kline(k.reset_index(drop=True))


def rate(strategy, df: pd.DataFrame, code: str) -> dict:
    from 分析决策.分析.indicators import all_indicators
    df = df.copy()
    df.attrs["code"] = code
    df = all_indicators(df, needed_cols=strategy.required_indicators)
    res = strategy.prebreak_grade(df)
    dn = strategy._grade_dn(df)[0] if hasattr(strategy, "_grade_dn") else "?"
    return {"grade": res.get("grade"), "trigger": res.get("trigger_price"),
            "stop": res.get("stop_loss"), "dn": dn}


def main() -> int:
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy
    strat = ZuanQianStrategy()

    con = duckdb.connect(DB, read_only=True)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM daily WHERE date >= '2019-06-01' "
        "ORDER BY symbol LIMIT ?", [SAMPLES]).fetchall()]
    con.close()
    print(f"采样 {len(codes)} 只: {codes}", flush=True)

    ndiff = 0
    for code in codes:
        full = read_full(code)
        if full is None or len(full) < 300:
            continue
        for d in DATES:
            dt = pd.to_datetime(d)
            if full["日期"].max() < dt:
                continue
            end_i = int(full[full["日期"] <= dt].index.max()) + 1
            base = full.iloc[:end_i]
            if len(base) < 250:
                continue
            r_full = rate(strat, base, code)                 # 全量历史
            r250 = rate(strat, base.iloc[-250:], code)       # 250 根截断
            r400 = rate(strat, base.iloc[-400:], code)       # 400 根截断
            keys = ("grade", "trigger", "stop", "dn")
            for tag, r in (("250", r250), ("400", r400)):
                same = all(r_full[k] == r[k] for k in keys)
                if not same:
                    ndiff += 1
                    print(f"  [DIFF] {code} {d} {tag}根: 全量={r_full} vs {tag}={r}", flush=True)
                else:
                    print(f"  [OK]   {code} {d} {tag}根 一致 "
                          f"({r_full['grade']}/trg={r_full['trigger']})", flush=True)
    print(f"\n结论: {ndiff} 处不一致（0 = 截断窗口与全量历史评级逐位一致）", flush=True)
    return 0 if ndiff == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
