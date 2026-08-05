"""日终对账（T-017 P3 · P2 报告第六节建议 4，质量闭环门）

流程：随机抽样 50 只（固定种子 + 强制疑难案例 000651/600519）→ 库内自算前复权
（等比因子法，P2/P1 同款算法）vs 新浪 qfq → 晚期（≥2006）中位误差 > 0.5% 报警。

口径注意：新浪接口当日数据滞后 1 天（P2 4.5 确认），只对共同日期比对。

用法（在交易部门根目录执行）：
    python -m 数据基础.duckdb.recon [--db 路径] [--sample 50] [--alert-pct 0.5]
输出：数据基础/data/duckdb_runtime/recon_<日期>.json（含明细 + 报警清单）

P3 依据：老板 2026-08-05 确认执行；P2 全量报告第六节建议 4。
"""
import argparse
import json
import random
import sys
import time

import numpy as np
import pandas as pd
from 数据基础.duckdb.config import (
    DB_PATH,
    RECON_ALERT_PCT,
    RECON_ERA_CUT,
    RECON_MUST,
    RECON_SAMPLE,
    RECON_SEED,
    RUNTIME_DIR,
)
from 数据基础.duckdb.reader import compute_qfq, read_daily_raw, read_xdxr

sys.stdout.reconfigure(encoding="utf-8")

CUT = pd.Timestamp(RECON_ERA_CUT)


def to_sina_symbol(symbol: str) -> str:
    return f"sh{symbol}" if symbol.startswith(("6", "9")) else f"sz{symbol}"


def fetch_sina_qfq(sym, cache_dir):
    """新浪 qfq 日线（带缓存，3 次重试）"""
    import akshare as ak
    fp = cache_dir / f"{sym}.csv"
    if fp.exists():
        # 质检 B2 修复：date 列须按日期读回（与首次拉取 df["date"]=to_datetime 同型），
        # 否则缓存路径与库内路径 merge 时 date 类型不一致报 ValueError
        return pd.read_csv(fp, parse_dates=["date"])
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=to_sina_symbol(sym), adjust="qfq")
            if df is None or len(df) == 0:
                raise RuntimeError("空表")
            df = df.reset_index()
            df["date"] = pd.to_datetime(df["date"])
            df.to_csv(fp, index=False, encoding="utf-8-sig")
            return df
        except Exception as e:  # noqa: BLE001 - akshare 网络异常兜底重试
            print(f"  [{sym}] 新浪尝试{attempt + 1}/3 失败: {type(e).__name__}: {e}")
            time.sleep(3)
    return None


def recon(db_path: str = str(DB_PATH), sample_n: int = RECON_SAMPLE,
          alert_pct: float = RECON_ALERT_PCT) -> dict:
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    cache_dir = RUNTIME_DIR / "sina_qfq_recon"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ORDER BY symbol：固定种子下抽样顺序可复现（T-017 P5 收尾项 6）
    all_symbols = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM daily ORDER BY symbol").fetchall()]
    rng = random.Random(RECON_SEED)
    rest = [s for s in all_symbols if s not in RECON_MUST]
    rng.shuffle(rest)
    sample = RECON_MUST + rest[:sample_n - len(RECON_MUST)]

    per_symbol, alerts, sina_fail = [], [], []
    all_late = []
    for sym in sample:
        sina = fetch_sina_qfq(sym, cache_dir)
        if sina is None:
            sina_fail.append(sym)
            continue
        daily = read_daily_raw(con, sym)
        xdxr = read_xdxr(con, sym)
        self_df = compute_qfq(daily, xdxr)
        m = self_df[["date", "close", "qfq_close"]].merge(
            sina[["date", "close"]].rename(columns={"close": "sina_close"}),
            on="date", how="inner")
        if m.empty:
            continue
        m["pct"] = (m["qfq_close"] - m["sina_close"]).abs() / m["sina_close"] * 100
        late = m[m["date"] >= CUT]
        rec = {
            "symbol": sym,
            "common_dates": len(m),
            "late_median_pct": round(float(late["pct"].median()), 4) if len(late) else None,
            "late_max_pct": round(float(late["pct"].max()), 4) if len(late) else None,
        }
        per_symbol.append(rec)
        if len(late):
            all_late.extend(late["pct"].tolist())
            if late["pct"].median() > alert_pct:
                alerts.append({**rec, "note": f"晚期中位误差 {rec['late_median_pct']}% > {alert_pct}% 报警"})

    result = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": db_path,
        "sample": sample_n,
        "sina_fail": sina_fail,
        "alerts": alerts,
        "summary": {
            "n": len(all_late),
            "median_pct": round(float(np.median(all_late)), 4) if all_late else None,
            "mean_pct": round(float(np.mean(all_late)), 4) if all_late else None,
            "n_alert": len(alerts),
        },
        "per_symbol": per_symbol,
    }
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNTIME_DIR / f"recon_{time.strftime('%Y-%m-%d')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    con.close()
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="日终对账：库内自算前复权 vs 新浪 qfq 抽样对照")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--sample", type=int, default=RECON_SAMPLE)
    ap.add_argument("--alert-pct", type=float, default=RECON_ALERT_PCT)
    args = ap.parse_args(argv)

    r = recon(args.db, args.sample, args.alert_pct)
    s = r["summary"]
    print(f"\n对账完成: 抽样{r['sample']}只 新浪失败{len(r['sina_fail'])} 样本点{s['n']} "
          f"中位误差{s['median_pct']}% 报警{len(r['alerts'])}只")
    for a in r["alerts"]:
        print(f"  [报警] {a['symbol']}: 晚期中位 {a['late_median_pct']}%")
    if not r["alerts"]:
        print("  无报警（偏差在阈值内）")


if __name__ == "__main__":
    main()
