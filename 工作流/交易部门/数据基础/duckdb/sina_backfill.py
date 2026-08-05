"""次新股新浪补齐（T-017 P3 · P2 遗留问题 2）

背景：7 只次新股（301655/301707/301717/603468/688826/688828/688836）通达信行情
服务器未收录（P2 全量失败），用新浪源补齐上市以来全量日线。

口径处理：
  - daily 存原始价（不复权，与 mootdx/通达信同口径）
  - xdxr 因子：新浪仅提供复权价、无因子明细表。方案：
      1) 对比新浪 qfq 与 raw 价格：全区间一致 → 上市以来无除权 → xdxr 留空（标记
         confirmed_no_xdxr，合理：次新股 2025 年后上市、大概率无除权）
      2) 存在差异 → 差异起始日 = 除权日，按复权比反推因子并**标记"待新浪源验证"**
         （不自动写入 xdxr 因子表，P5 接入复权计算前需人工核验）
  - 补齐后股票进入 daily 表，增量脚本（update_daily.py）自动将其纳入每日增量循环

用法（在交易部门根目录执行）：
    python -m 数据基础.duckdb.sina_backfill [--db 路径] [--codes 301655,...] [--reload]

P3 依据：老板 2026-08-05 确认执行；P2 全量报告遗留问题 2。
"""
import argparse
import json
import sys
import time

import pandas as pd
from 数据基础.duckdb import store as S
from 数据基础.duckdb.config import DB_PATH, RUNTIME_DIR, SINA_FALLBACK_CODES

sys.stdout.reconfigure(encoding="utf-8")


def to_sina_symbol(symbol: str) -> str:
    """A股代码 → 新浪代码（sh/sz 前缀）"""
    return f"sh{symbol}" if symbol.startswith(("6", "9")) else f"sz{symbol}"


def fetch_sina(symbol: str, adjust: str = "", retry: int = 3) -> pd.DataFrame | None:
    """拉新浪日线（akshare 接口，3 次重试）

    Returns:
        DataFrame(date/open/high/low/close/volume/amount) 或 None
    """
    try:
        import akshare as ak
    except ImportError:
        return None
    last_err = None
    for attempt in range(retry):
        try:
            df = ak.stock_zh_a_daily(symbol=to_sina_symbol(symbol), adjust=adjust)
            if df is None or len(df) == 0:
                raise RuntimeError("空表")
            df = df.reset_index()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        except Exception as e:  # noqa: BLE001 - akshare 网络异常兜底重试
            last_err = e
            time.sleep(3)
    print(f"  [{symbol}] 新浪拉取失败: {type(last_err).__name__}: {last_err}")
    return None


def check_no_xdxr(raw: pd.DataFrame, qfq: pd.DataFrame) -> tuple[bool, list[dict]]:
    """对比 raw 与 qfq 价格，判断是否存在除权事件

    Returns:
        (无除权?, 疑似除权日清单[{date, factor, raw_close, qfq_close}])
    """
    if raw is None or qfq is None or raw.empty or qfq.empty:
        return False, []
    m = raw[["date", "close"]].merge(
        qfq[["date", "close"]].rename(columns={"close": "qfq"}), on="date", how="inner")
    if m.empty:
        return False, []
    m["factor"] = m["qfq"] / m["close"]
    # 容差 1e-4：除权前各日 factor 理论相等但存在浮点舍入噪声（1e-6 级），
    # 真实除权因子差异为 1e-2 级（如 300093 的 0.8806），二者量级分离清晰
    diff = m[(m["factor"] - m["factor"].iloc[0]).abs() > 1e-4]
    if diff.empty:
        return True, []
    # 因子变化的起始日 ≈ 除权日
    out, prev_f = [], None
    for _, r in diff.iterrows():
        d = pd.Timestamp(r["date"])
        if prev_f is None or abs(r["factor"] - prev_f) > 1e-4:
            out.append({"date": d.date().isoformat(), "factor": round(float(r["factor"]), 6),
                        "raw_close": float(r["close"]), "qfq_close": float(r["qfq"])})
            prev_f = r["factor"]
    return False, out


def backfill_one(con, symbol: str, reload: bool = False) -> dict:
    """补齐单只：新浪 raw 全量 upsert + 除权状态判定"""
    if not reload:
        n = con.execute("SELECT count(*) FROM daily WHERE symbol=?", [symbol]).fetchone()[0]
        if n > 0:
            return {"symbol": symbol, "status": "skipped", "rows": n, "note": "已入库，跳过"}

    raw = fetch_sina(symbol, adjust="")
    if raw is None:
        return {"symbol": symbol, "status": "failed", "note": "新浪无数据"}
    qfq = fetch_sina(symbol, adjust="qfq")

    no_xdxr, events = check_no_xdxr(raw, qfq)

    # 落库（新浪 volume 列 → vol；amount 若缺失填 0）
    d = raw.rename(columns={"volume": "vol"})
    if "amount" not in d.columns:
        d["amount"] = 0.0
    d = d[["date", "open", "high", "low", "close", "vol", "amount"]]
    n = S.upsert_daily(con, symbol, d)

    note = "confirmed_no_xdxr" if no_xdxr else "suspected_xdxr"
    if events:
        note += f": {len(events)} 个除权日待核验 " + ", ".join(e["date"] for e in events)
    print(f"  [{symbol}] 入库 {n} 条 raw, qfq一致={no_xdxr}, 除权事件{len(events)}")
    return {"symbol": symbol, "status": "done", "rows": n,
            "no_xdxr": no_xdxr, "xdxr_events": events, "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description="次新股新浪补齐（P2 失败 7 只）")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--codes", default="", help="指定代码（逗号分隔），默认 P2 失败 7 只")
    ap.add_argument("--reload", action="store_true", help="强制重拉覆盖")
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or SINA_FALLBACK_CODES
    con = S.open_db(args.db)

    result = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "db": args.db, "results": []}
    for sym in codes:
        print(f"处理 {sym} ...")
        result["results"].append(backfill_one(con, sym, args.reload))

    # 新入库的股票 xdxr 为空 → 除权校验规则 B 会对其价格跳变告警，属正常（新股上市初期波动）
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNTIME_DIR / f"sina_backfill_{time.strftime('%Y-%m-%d')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    con.close()
    print(f"完成 -> {out}")


if __name__ == "__main__":
    main()
