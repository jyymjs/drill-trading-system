"""E2 突破后二次买点（2026-08-13 · 替代进场语义）

对 T8 prebreak 触发集模拟"回踩二买"路径 vs 首进（触发成交）对照：
- 回踩定义（操作化）：触发后 20 天内 最低 ≤ 平台下沿(ty_low×1.01) → 回踩日；
  二买进场 = 回踩日最低价（回踩低点）；破位（跌破 ty_low×0.99）→ 放弃
- 二买 R：构造 Signal(date=回踩日, trigger=回踩低点, stop=原止损) → track_signal
  20d 出场模拟（与首进同规则）
- 输出：首进 vs 二买 avgR/胜率/可执行率/放弃集对照 + 触发日一字板（追不进）子集
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))       # 项目/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 交易部门根

import duckdb
import numpy as np
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
SIG = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出", "归档",
                   "旧回测-20260813", "backtest_r43_t2_T8", "signals.csv")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                   "实验", "E2-二次买点-20260813.md")

LOOKBACK = 20          # 回踩窗口（触发后 20 天内）
LOW_TOL = 1.01         # 回踩判定：最低 ≤ ty_low × 1.01
BREAK_TOL = 0.99       # 破位判定：跌破 ty_low × 0.99 → 放弃


def load_kline(con, symbol: str) -> pd.DataFrame | None:
    from 数据基础.duckdb.reader import compute_qfq, _to_cn_kline
    daily = con.execute(
        "SELECT date, open, high, low, close, vol, amount FROM daily "
        "WHERE symbol=? ORDER BY date", [symbol]).df()
    xdxr = con.execute(
        "SELECT date, fenhong, peigujia, songzhuangu, peigu FROM xdxr "
        "WHERE symbol=? AND category=1 ORDER BY date", [symbol]).df()
    if daily.empty:
        return None
    return _to_cn_kline(compute_qfq(daily, xdxr if len(xdxr) else None)
                        .reset_index(drop=True))


def main() -> int:
    from 分析决策.分析.indicators import all_indicators  # noqa: E402
    from 回测系统.tracking import Signal, track_signal  # noqa: E402
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy  # noqa: E402

    sig = pd.read_csv(SIG, encoding="utf-8-sig", dtype={"code": str})
    trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)].copy()
    print(f"T8 prebreak 触发集: {len(trig)} 笔", flush=True)

    con = duckdb.connect(DB, read_only=True)
    strat = ZuanQianStrategy()
    rows = []
    t0 = time.time()
    for i, (_, r) in enumerate(trig.iterrows(), 1):
        code = str(r["code"])
        k = load_kline(con, code)
        if k is None:
            continue
        d0 = pd.to_datetime(r["date"])
        sub = k[k["日期"] <= d0].copy()
        if len(sub) < 60:
            continue
        sub.attrs["code"] = code
        sub = all_indicators(sub, needed_cols=strat.required_indicators)
        try:
            res = strat.prebreak_grade(sub)
        except Exception:  # noqa: BLE001
            continue
        ty_low = res.get("ty_low", 0) or 0
        if ty_low <= 0:
            continue
        # 触发日（首根 high ≥ trigger）——与 tracking 同口径
        n = len(k)
        t_idx = None
        for j in range(len(sub), n):
            if k["最高"].iloc[j] >= r["trigger"]:
                t_idx = j
                break
        if t_idx is None:
            continue
        # 回踩窗口（触发日次日 ~ +20）
        win = k.iloc[t_idx + 1: t_idx + 1 + LOOKBACK]
        if len(win) == 0:
            continue
        lows = win["最低"].values
        closes = win["收盘"].values
        # 回踩 = 触及下沿容差但未破位（0.99×ty_low ≤ low ≤ 1.01×ty_low）；破位 = <0.99×ty_low
        broke = np.where(lows < ty_low * BREAK_TOL)[0]
        hit = np.where((lows >= ty_low * BREAK_TOL) & (lows <= ty_low * LOW_TOL))[0]
        reentry_r = None
        verdict = "无回踩"
        if len(broke) > 0 and (len(hit) == 0 or broke[0] < hit[0]):
            verdict = "破位放弃"
        elif len(hit) > 0:
            rb = int(hit[0])
            # 二买进场 = 回踩日收盘（确认企稳再进，非理想化低点）；止损 = 原 stop
            entry2 = float(closes[rb])
            sig2 = Signal(code=code, date=win["日期"].iloc[rb], mode="prebreak",
                          grade="S", scores={}, close=entry2, trigger=entry2,
                          stop=float(r["stop"]),
                          risk=max(entry2 - float(r["stop"]), 0.5))
            # 传入窗口须包含信号日（从回踩日及其后切片）
            tail = k.iloc[t_idx + rb:]
            oc = track_signal(sig2, tail.reset_index(drop=True), 20, enable_cost=True)
            if oc.triggered:
                reentry_r = oc.r
                verdict = f"二买@第{rb+1}日"
        rows.append({"code": code, "date": r["date"], "r_first": r["r_20d"],
                     "verdict": verdict, "r_reentry": reentry_r})
        if i % 300 == 0:
            print(f"  [{i}/{len(trig)}] {time.time()-t0:.0f}s", flush=True)
    con.close()
    df = pd.DataFrame(rows)
    df["year"] = df["date"].astype(str).str[:4]

    re = df[df["r_reentry"].notna()]
    first = df["r_first"].astype(float)
    lines = ["# E2 突破后二次买点（2026-08-13 · 替代进场语义）", "",
             f"> T8 触发集 {len(df)} 笔｜回踩窗口 {LOOKBACK} 日（触发后），下沿容差 "
             f"{LOW_TOL}，破位线 {BREAK_TOL}", "",
             "## 总览", "", "| 组 | 笔数 | avgR | 胜率 |", "|---|---|---|---|",
             f"| 首进（触发成交）| {len(df)} | {first.mean():+.3f} | {(first>0).mean():.1%} |"]
    if len(re):
        rr = re["r_reentry"].astype(float)
        exec_rate = len(re) / len(df)
        lines.append(f"| 二买（回踩进场）| {len(re)} | {rr.mean():+.3f} | {(rr>0).mean():.1%} |")
        lines += ["", f"**可执行率**: {exec_rate:.1%}（{len(re)}/{len(df)} 等来回踩）",
                  f"**效应量**: 二买 avgR - 首进 avgR = {rr.mean()-first.mean():+.3f}R",
                  f"**胜率差**: {(rr>0).mean()-(first>0).mean():+.1%}pp",
                  f"**bootstrap 提示**: 二买 n={len(re)}（回踩样本有限，区间可能重叠）", ""]
    else:
        lines += ["| 二买 | 0 | — | — |", "", "（无回踩进场样本）", ""]
    # 放弃集
    broke = df[df["verdict"] == "破位放弃"]
    if len(broke):
        br = broke["r_first"].astype(float)
        lines += ["## 放弃集（回踩破位，判据有效性）", "",
                  f"| 组 | 笔数 | avgR（首进） | 胜率 |", "|---|---|---|---|",
                  f"| 破位放弃 | {len(broke)} | {br.mean():+.3f} | {(br>0).mean():.1%} |",
                  "", "> 破位后首进 avgR 低 = 判据有效（躲过下跌）；avgR 高 = 假破位",
                  ""]
    # 7y 子窗
    sub7 = df[df["year"] >= "2020"]
    lines += ["## 7y 子窗（2020+）", "", "| 组 | 笔数 | avgR | 胜率 |", "|---|---|---|---|",
              f"| 首进 | {len(sub7)} | {sub7['r_first'].astype(float).mean():+.3f} "
              f"| {(sub7['r_first'].astype(float)>0).mean():.1%} |"]
    re7 = sub7[sub7["r_reentry"].notna()]
    if len(re7):
        rr7 = re7["r_reentry"].astype(float)
        lines.append(f"| 二买 | {len(re7)} | {rr7.mean():+.3f} | {(rr7>0).mean():.1%} |")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
