"""duckdb 存储层（T-017 P3）

- 建表结构：与 P2 全量库完全一致（PK symbol+date 去重）
- upsert：INSERT OR REPLACE（幂等，重复拉取不产生重复行）
- 校验：无重复 + 尾部缺口（未到最新交易日的股票清单）
"""
import pandas as pd

import duckdb
from 数据基础.duckdb.config import DB_PATH

DAILY_COLS = ["symbol", "date", "open", "high", "low", "close", "vol", "amount"]
XDXR_COLS = ["symbol", "date", "category", "name", "fenhong", "peigujia",
             "songzhuangu", "peigu", "suogu", "fenshu", "xingquanjia"]

_SCHEMA = {
    "daily": """CREATE TABLE IF NOT EXISTS daily (
        symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, vol DOUBLE, amount DOUBLE,
        PRIMARY KEY (symbol, date))""",
    "xdxr": """CREATE TABLE IF NOT EXISTS xdxr (
        symbol VARCHAR, date DATE, category INT, name VARCHAR,
        fenhong DOUBLE, peigujia DOUBLE, songzhuangu DOUBLE,
        peigu DOUBLE, suogu DOUBLE, fenshu DOUBLE, xingquanjia DOUBLE,
        PRIMARY KEY (symbol, date, category))""",
}


def open_db(path=None, read_only: bool = False):
    """打开 duckdb 连接；写模式时确保建表"""
    con = duckdb.connect(str(path or DB_PATH), read_only=read_only)
    if not read_only:
        ensure_schema(con)
    return con


def ensure_schema(con):
    for ddl in _SCHEMA.values():
        con.execute(ddl)


def upsert_daily(con, symbol: str, df: pd.DataFrame) -> int:
    """增量 upsert 日线。df 列：date/open/high/low/close/vol/amount。返回写入行数"""
    if df is None or df.empty:
        return 0
    d = df.copy()
    d["symbol"] = symbol
    con.register("d", d[DAILY_COLS])
    con.execute(
        "INSERT OR REPLACE INTO daily SELECT symbol,date,open,high,low,close,vol,amount FROM d")
    con.unregister("d")
    return len(d)


def upsert_xdxr(con, symbol: str, df: pd.DataFrame) -> int:
    """全量替换式 upsert 除权因子（每只全量重拉，覆盖即修复漏记/补录）。返回写入行数"""
    if df is None or df.empty:
        return 0
    x = df.copy()
    x["symbol"] = symbol
    con.register("xd", x[XDXR_COLS])
    con.execute(
        "INSERT OR REPLACE INTO xdxr SELECT symbol,date,category,name,fenhong,peigujia,"
        "songzhuangu,peigu,suogu,fenshu,xingquanjia FROM xd")
    con.unregister("xd")
    return len(x)


def known_symbols(con) -> list[str]:
    """库内已有股票的 symbol 列表（增量对象来源）"""
    return [r[0] for r in con.execute("SELECT DISTINCT symbol FROM daily ORDER BY symbol").fetchall()]


# ───────────────────────────── 校验 ─────────────────────────────

def check_no_duplicate(con) -> dict:
    """校验 daily/xdxr 均无重复行（PK 约束兜底，双保险）"""
    daily_dup = con.execute(
        "SELECT count(*) - count(DISTINCT (symbol, date)) AS dup FROM daily").fetchone()[0]
    xdxr_dup = con.execute(
        "SELECT count(*) - count(DISTINCT (symbol, date, category)) AS dup FROM xdxr").fetchone()[0]
    return {"daily_dups": int(daily_dup), "xdxr_dups": int(xdxr_dup)}


def check_tail_gaps(con, max_behind_days: int = 5) -> dict:
    """尾部缺口校验：每只股票最新日期 vs 全库最新交易日。

    停牌/退市边缘股票会自然落后，故只统计"落后超过 N 天"的股票，
    并返回落后最多的前 20 只明细（供人工判断是否异常）。
    """
    latest = con.execute("SELECT max(date) FROM daily").fetchone()[0]
    rows = con.execute("""
        SELECT symbol, max(date) AS d,
               date_diff('day', max(date), ?) AS behind
        FROM daily GROUP BY symbol
    """, [latest]).fetchall()
    behind_list = [(s, str(d), int(b)) for s, d, b in rows if d is not None]
    over = [r for r in behind_list if r[2] > max_behind_days]
    over.sort(key=lambda r: -r[2])
    return {
        "db_latest": str(latest),
        "total_symbols": len(behind_list),
        "behind_over_days": len(over),
        "top_behind": over[:20],
    }
