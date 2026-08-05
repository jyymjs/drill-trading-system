"""除权完整性校验（T-017 P3 · P2 报告遗留问题 1 兜底）

背景：300093 金刚光伏 2025-11-20 漏记除权——通达信 xdxr 当日仅有 category 9/15
（转配股上市/15）记录、无 category=1（除权除息）记录，导致历史复权价差 13.5%。

本模块两条互补规则（纯函数，可单测，不依赖网络）：

  规则 A「送转类记录缺除权除息」：当日存在送转/股本类 xdxr 记录
      （category ∈ {1,2,3,5,9,15} 之外的股本类如 9/15），但当日无 category=1 → 疑似
      除权除息漏记。300093 精确命中（9+15 无 1）。假阳性极少（有送转必有除权除息）。

  规则 B「跳变超涨跌停上限无记录」：相邻交易日 close/open 跳变超过板块涨跌停上限
      （主板 >10.5%、创业板/科创板 >20.5%），且当日无任何 xdxr 记录 → 疑似漏记
      （如 10 送 10 = -50% 跳变）。P2 建议的 5% 阈值实测全库假阳性爆炸
      （涨停板 10%/20% 均命中，每只数十~数百条），故改为超限判定；300093 案的
      -8.37% 跳变低于主板上限，由规则 A 精确覆盖。

用法（在交易部门根目录执行）：
    python -m 数据基础.duckdb.xdxr_check [--db 路径] [--threshold 0.05] [--limit N] [--only 300093,...]
输出：数据基础/data/duckdb_runtime/xdxr_check_<日期>.csv（待核清单）+ 摘要日志

P3 依据：老板 2026-08-05 确认执行；P2 全量报告 4.3 / 第六节建议 3。
"""
import argparse
import csv
import json
import sys
import time

import numpy as np
import pandas as pd
from 数据基础.duckdb.config import DB_PATH, RUNTIME_DIR, XDXR_JUMP_THRESHOLD

sys.stdout.reconfigure(encoding="utf-8")

# 送转上市类 category（出现而同日无 category=1 时触发规则 A）
# 依据 P2 全量库 category 分布（第三节）+ 真实个案校准：
#   - 9 转配股上市 = 送转股上市日，必须伴随除权除息（300093 个案即 9+15 无 1）
#   - 15 类在 300093 个案中与 9 同日出现，同列为强信号
#   - 排除 category=5（股本变化）：半年报/年报定期快照，无除权，P2 库中 10.5 万条
#     （实测 600519 等正常股大量 5 类记录，若纳入规则 A 会全库假阳性）
#   - 排除 3 增发：A 股定向增发不除权，公开增发才除权（占少数的 3 类记录多属前者）
CORP_ACTION_CATEGORIES = {9, 15}


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _limit_pct(symbol: str) -> float:
    """板块涨跌停上限（含 0.5% 容差）——规则 B 的跳变判定线

    主板 ±10% ｜ ST ±5%（无代码前缀特征，容忍其少量漏报）｜
    创业板 300/301、科创板 688/689 ±20% ｜ 北交所 8/4 前缀 ±30%（防御）
    """
    if symbol.startswith(("300", "301", "688", "689")):
        return 0.205
    if symbol.startswith(("8", "4")):
        return 0.305
    return 0.105


def check_symbol(daily: pd.DataFrame, xdxr: pd.DataFrame | None,
                 symbol: str = "", threshold: float = XDXR_JUMP_THRESHOLD) -> list[dict]:
    """单只股票除权完整性校验（纯函数）

    Args:
        daily: 列 date/open/close（升序）
        xdxr: 列 date/category（可为 None/空 = 无任何除权记录）
        symbol: 股票代码（规则 B 按板块判定涨跌停上限）
        threshold: 规则 B 最低跳变线（实际判定线 = max(threshold, 板块上限)）

    Returns:
        [{rule, date, jump_pct, open_gap_pct, close_gap_pct, xdxr_cats, note}, ...]
    """
    hits = []
    if daily is None or daily.empty:
        return hits

    d = daily.copy()
    d = d.sort_values("date").reset_index(drop=True)
    dates = d["date"].astype("datetime64[ns]").dt.normalize()
    d["_d"] = dates

    # ── xdxr 按日索引 ──
    x_by_date = {}
    cats_by_date = {}
    if xdxr is not None and not xdxr.empty:
        x = xdxr.copy()
        x["_d"] = pd.to_datetime(x["date"]).dt.normalize()
        for dd, cats in x.groupby("_d")["category"]:
            x_by_date[dd] = list(cats)
            cats_by_date[dd] = set(cats)

    # ── 规则 A：有送转/股本类记录但当日无 category=1 ──
    for dd, cats in cats_by_date.items():
        has_xdxr1 = 1 in cats
        has_corp = bool(cats & CORP_ACTION_CATEGORIES)
        if has_corp and not has_xdxr1:
            hits.append({
                "rule": "A",
                "date": dd.date().isoformat(),
                "jump_pct": None,
                "open_gap_pct": None,
                "close_gap_pct": None,
                "xdxr_cats": sorted(cats),
                "note": "有送转/股本类记录但缺 category=1(除权除息)",
            })

    # ── 规则 B：跳变超板块涨跌停上限 且当日无任何 xdxr 记录 ──
    # 语义（P2 建议 5% 启发式的降噪版）：普通涨停/跌停（主板 ±10%、创业/科创 ±20%）
    # 是正常交易行为，直接按 5% 阈值会全库百万级假阳性（全库实测每只数十~数百条）；
    # 改为"跳变超过板块涨跌停上限（含容差）"——除权比例超过涨停上限的漏记才是确定异常
    # （如 10 送 10 = -50% 跳变）。300093 案的 2025-11-21（-8.37%）低于主板上限，
    # 已由规则 A（送转记录缺除权除息）精确覆盖，规则 B 不再重复报。
    if len(d) >= 2:
        prev_close = d["close"].shift(1)
        open_gap = d["open"] / prev_close - 1.0
        close_gap = d["close"] / prev_close - 1.0
        # 判定线 = max(threshold 配置, 板块涨跌停上限)：threshold 可调严不可调松
        limit = max(_limit_pct(symbol), threshold)
        for i in range(1, len(d)):
            dd = d["_d"].iloc[i]
            if dd in cats_by_date:          # 当日有任何 xdxr 记录 → 跳过（规则 A 已管）
                continue
            og, cg = open_gap.iloc[i], close_gap.iloc[i]
            if pd.isna(og) or pd.isna(cg):
                continue
            if abs(cg) > limit or abs(og) > limit:
                hits.append({
                    "rule": "B",
                    "date": dd.date().isoformat(),
                    "jump_pct": round(float(cg) * 100, 2),
                    "open_gap_pct": round(float(og) * 100, 2),
                    "close_gap_pct": round(float(cg) * 100, 2),
                    "xdxr_cats": [],
                    "note": f"跳变{abs(cg)*100:.1f}%超板块涨跌停上限且当日无任何除权记录(疑似漏记)",
                })
    return hits


def run_check(db_path: str = str(DB_PATH), threshold: float = XDXR_JUMP_THRESHOLD,
              limit: int = 0, only: list[str] | None = None, verbose: bool = False) -> dict:
    """全库扫描除权完整性，输出待核清单 CSV + 摘要

    Returns:
        {"symbols": N, "hits_rule_a": N, "hits_rule_b": N, "csv": path, "json": path}
    """
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    if only:
        symbols = [s for s in only]
    else:
        symbols = [r[0] for r in con.execute(
            "SELECT symbol FROM daily GROUP BY symbol ORDER BY symbol").fetchall()]
    if limit > 0:
        symbols = symbols[:limit]

    all_hits = []
    for i, sym in enumerate(symbols, 1):
        daily = con.execute(
            "SELECT date, open, close FROM daily WHERE symbol=? ORDER BY date", [sym]).df()
        xdxr = con.execute(
            "SELECT date, category FROM xdxr WHERE symbol=? ORDER BY date", [sym]).df()
        hits = check_symbol(daily, xdxr, symbol=sym, threshold=threshold)
        for h in hits:
            all_hits.append({"symbol": sym, **h})
        if verbose and (i % 1000 == 0 or hits):
            print(f"[{i}/{len(symbols)}] {sym}: {len(hits)} 条待核", flush=True)
    con.close()

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    csv_path = RUNTIME_DIR / f"xdxr_check_{stamp}.csv"
    json_path = RUNTIME_DIR / f"xdxr_check_{stamp}.json"
    if all_hits:
        # QUOTE_NONNUMERIC：symbol/date 等文本列强制加引号，防止 000001 类
        # 代码在 Excel/pandas 读回时被推断为数字丢前导零
        pd.DataFrame(all_hits).to_csv(csv_path, index=False, encoding="utf-8-sig",
                                      quoting=csv.QUOTE_NONNUMERIC)
    else:
        csv_path.write_text("(无待核项)\n", encoding="utf-8")

    result = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": db_path, "threshold": threshold,
        "symbols_checked": len(symbols),
        "hits_rule_a": sum(1 for h in all_hits if h["rule"] == "A"),
        "hits_rule_b": sum(1 for h in all_hits if h["rule"] == "B"),
        "total_hits": len(all_hits),
        "csv": str(csv_path), "json": str(json_path),
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="除权完整性校验（价格跳变 + 送转缺除权除息启发式）")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--threshold", type=float, default=XDXR_JUMP_THRESHOLD, help="规则B跳变阈值(默认0.05)")
    ap.add_argument("--limit", type=int, default=0, help="冒烟：只查前 N 只")
    ap.add_argument("--only", default="", help="只查指定代码，逗号分隔，如 300093,000651")
    args = ap.parse_args(argv)

    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    r = run_check(args.db, args.threshold, args.limit, only, verbose=True)
    print(f"\n完成: 检查 {r['symbols_checked']} 只 | 规则A(送转缺除权除息) {r['hits_rule_a']} 条 | "
          f"规则B(价格跳变) {r['hits_rule_b']} 条 | 输出 {r['csv']}")


if __name__ == "__main__":
    main()
