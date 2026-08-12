"""R-080 G1 生成器端到端口径验证（2026-08-13）

目标：证明 g1_calib_signals.py 的评级管线（duckdb qfq + 250 根截断窗口 +
prebreak_grade + 评分列提取）与 backtest_final_20260806 signals.csv 生成管线
（V4 定版信号集）逐位一致。

方法：从 final signals.csv 抽 N 个已知 S 级信号（覆盖各年份），用我的管线对
同 (code, date) 重评级 → 对比 grade/trigger/stop/risk/PT/TY/DN/DL/LK/SF。
全一致 = 生成器可用（定参段信号与验证段同口径）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
SIGNALS = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                       "数据", "backtest_final_20260806", "signals.csv")
WINDOW = 250          # 截断窗口（g1_window_equiv 已证与全量等价）
N = 12                # 抽样数
COL_MAP = {"PT平台测试": "PT", "TY统一区间": "TY", "DN动能": "DN",
           "DL独立结构": "DL", "LK轮廓质量": "LK", "SF释放级别": "SF"}


def read_window(symbol: str, end: str) -> pd.DataFrame | None:
    """读 symbol 到 end 日期的 250 根窗口（qfq 自算，与 fetcher/duckdb 分支同源）"""
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
    end_dt = pd.to_datetime(end)
    k = k[k["date"] <= end_dt]
    if len(k) < WINDOW:
        return None
    return _to_cn_kline(k.tail(WINDOW).reset_index(drop=True))


def my_rate(symbol: str, end: str) -> dict | None:
    from 分析决策.分析.indicators import all_indicators
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy
    df = read_window(symbol, end)
    if df is None:
        return None
    df.attrs["code"] = symbol
    df = all_indicators(df, needed_cols=ZuanQianStrategy.required_indicators)
    strat = ZuanQianStrategy()
    res = strat.prebreak_grade(df)
    scores = {COL_MAP[k]: v[0] for k, v in res.get("scores", {}).items() if k in COL_MAP}
    dn_g = strat._grade_dn(df)[0]
    scores.setdefault("DN", dn_g)
    return {"grade": res.get("grade"), "trigger": res.get("trigger_price"),
            "stop": res.get("stop_loss"), "risk": res.get("risk_per_share"),
            **scores}


def main() -> int:
    sig = pd.read_csv(SIGNALS, encoding="utf-8-sig", dtype={"code": str})
    # 各年份均匀抽样（2019 无——final 从 2023-07 起）
    picks = sig.groupby(sig["date"].astype(str).str[:4]).head(max(1, N // 4))
    picks = picks.sample(min(N, len(picks)), random_state=42)
    ok = diff = 0
    for _, row in picks.iterrows():
        code, d = row["code"], row["date"]
        got = my_rate(code, d)
        if got is None:
            print(f"  [SKIP] {code} {d}: 数据不足 250 根", flush=True)
            continue
        exp = {"grade": row["grade"], "trigger": row["trigger"], "stop": row["stop"],
               "risk": row["risk"],
               "PT": row["PT"], "TY": row["TY"], "DL": row["DL"],
               "LK": row["LK"], "SF": row["SF"], "DN": row["DN"]}
        bad = {k: (exp[k], got[k]) for k in exp if exp[k] != got[k]}
        if bad:
            diff += 1
            print(f"  [DIFF] {code} {d}: {bad}", flush=True)
        else:
            ok += 1
            print(f"  [OK]   {code} {d} 评级={got['grade']} trigger={got['trigger']} "
                  f"stop={got['stop']} risk={got['risk']} 全列一致", flush=True)
    print(f"\n结果: 一致 {ok} / 差异 {diff}（0 差异 = 生成器与 V4 信号集同口径）", flush=True)
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
