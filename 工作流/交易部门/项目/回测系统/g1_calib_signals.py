"""R-080 G1 定参段信号生成（2026-08-13 · 网格对齐验证版）

口径（与 backtest_final_20260806/signals.csv 同管线，g1_validate_pipeline 已证）：
- 数据：t017_p2.duckdb 原始价 + qfq 因子自算（fetcher/duckdb 主链路同源），全量历史
- 网格：range(249, n, 5)（engine.GRID_ANCHOR=249 + interval=5，每股全量第 249 根起
  每 5 交易日采样——final 信号 602 笔抽样 100% 落在该网格，0 偏离）
- 窗口：250 根截断（g1_window_equiv 已证与全量历史评级逐位一致）
- 评级：prebreak_grade（5 条件）+ px 预筛 + quick_prefilter（scan_single_stock 同序）
- 信号：grade==S（与 final 信号集一致；无 R-035 DN 剔除——08-06 生成时无此规则）
- 无前视：窗口一律 iloc[:i+1]（只用到 i 日及以前数据）
- 预筛：close ≥ 0.70×high60（final 602 笔 min=0.715 → 0.70 理论 0 漏，纯砍算力）

输出：产出/输出/数据/backtest_calib_2020-2022/signals_calib.csv
（列序同 final：mode,code,date,grade,PT,TY,DN,DL,LK,SF,close,trigger,stop,risk,
prbook_warn；triggered_* 触发列留空——由回测引擎现场模拟）
"""
import sys, os, time
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出", "数据",
                   "backtest_calib_2020-2022", "signals_calib.csv")
WINDOW = 250          # 截断窗口（等价性已证）
CALIB_START = "2020-01-01"
CALIB_END = "2022-12-31"
PX_MIN = 0.35         # G8 像素感池级预筛阈值（scanner.G8_PX_THRESHOLD 同值）
BATCH = 80            # 每进程批股票数
COL_MAP = {"PT平台测试": "PT", "TY统一区间": "TY", "DN动能": "DN",
           "DL独立结构": "DL", "LK轮廓质量": "LK", "SF释放级别": "SF"}
COL_ORDER = ["mode", "code", "date", "grade", "PT", "TY", "DN", "DL", "LK", "SF",
             "close", "trigger", "stop", "risk", "prbook_warn"]


def _load_pool() -> list[str]:
    con = duckdb.connect(DB, read_only=True)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM daily WHERE date <= ?", [CALIB_START]).fetchall()]
    con.close()
    return codes


def _read_stock(symbol: str) -> pd.DataFrame | None:
    """读全量历史（网格从第 249 根起算，与引擎同构——起点截断会移动网格）"""
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


def _scan_one(symbol: str) -> list[dict]:
    from 分析决策.分析.indicators import all_indicators, pixelation_score
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy

    df = _read_stock(symbol)
    if df is None or len(df) < WINDOW:
        return []
    df = df[df["日期"].astype(str) <= CALIB_END].reset_index(drop=True)
    if len(df) < WINDOW:
        return []
    df.attrs["code"] = symbol
    df = all_indicators(df, needed_cols=ZuanQianStrategy.required_indicators)

    strat = ZuanQianStrategy()
    # ── 向量化预筛（一次性）：60 根滚动高点——S 级信号日 close≥0.7×high60
    # （final 信号 602 笔统计 min=0.715/p5=0.806 → 0.70 理论 0 漏，纯砍算力）
    hi = df["最高"].values
    cl = df["收盘"].values
    r60 = pd.Series(hi).rolling(60).max().values
    cand_hi = cl >= 0.70 * r60

    rows = []
    n = len(df)
    # 网格对齐引擎：range(GRID_ANCHOR=249, n, interval=5)（final 信号 100% 落网格）
    for i in range(249, n, 5):
        if not cand_hi[i]:
            continue
        d = str(df["日期"].iloc[i])[:10]
        if d < CALIB_START:
            continue
        if d > CALIB_END:
            break
        win = df.iloc[i - WINDOW + 1: i + 1]
        # scan_single_stock 同序预筛：quick_prefilter → px
        if not strat.quick_prefilter(win):
            continue
        if pixelation_score(win) < PX_MIN:
            continue
        try:
            res = strat.prebreak_grade(win)
        except Exception:  # noqa: BLE001 - 单点失败跳过（g2 同款）
            continue
        if res.get("grade") != "S":
            continue
        dn_g = strat._grade_dn(win)[0] if hasattr(strat, "_grade_dn") else "C"
        scores = {COL_MAP[k]: v[0] for k, v in res.get("scores", {}).items()
                  if k in COL_MAP}
        rows.append({
            "mode": "prebreak", "code": symbol, "date": d, "grade": "S",
            "PT": scores.get("PT", "C"), "TY": scores.get("TY", "C"),
            "DN": dn_g, "DL": scores.get("DL", "C"), "LK": scores.get("LK", "C"),
            "SF": scores.get("SF", "C"),
            "close": round(float(win["收盘"].iloc[-1]), 4),
            "trigger": res.get("trigger_price", 0),
            "stop": res.get("stop_loss", 0),
            "risk": res.get("risk_per_share", 0),
            "prbook_warn": "",
        })
    return rows


def _work(batch: list[str]) -> list[dict]:
    out = []
    for s in batch:
        try:
            out.extend(_scan_one(s))
        except Exception:  # noqa: BLE001 - 单股失败不影响批次
            continue
    return out


def main() -> int:
    codes = _load_pool()
    print(f"股票池: {len(codes)} 只（{CALIB_START} 前有数据）", flush=True)
    batches = [codes[i:i + BATCH] for i in range(0, len(codes), BATCH)]
    print(f"批次: {len(batches)}（每批 {BATCH} 只）", flush=True)

    t0 = time.time()
    all_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(_work, b): j for j, b in enumerate(batches)}
        for j, f in enumerate(as_completed(futs)):
            try:
                all_rows.extend(f.result())
            except Exception as e:  # noqa: BLE001
                print(f"  [批失败] {e}", flush=True)
            if (j + 1) % 40 == 0 or j + 1 == len(batches):
                el = time.time() - t0
                print(f"  进度 {j+1}/{len(batches)} 批 · 已收集 {len(all_rows)} 信号 "
                      f"· 耗时 {el:.0f}s", flush=True)

    sig = pd.DataFrame(all_rows, columns=COL_ORDER).sort_values(["date", "code"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sig.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n完成: {len(sig)} 笔定参段 S 级信号 → {OUT} "
          f"(耗时 {time.time()-t0:.0f}s)", flush=True)
    if len(sig):
        print("年份分布:", sig["date"].str[:4].value_counts().sort_index().to_dict(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
