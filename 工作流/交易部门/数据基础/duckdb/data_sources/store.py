"""data_sources 存储层（T-017 P4）：公告 / 预约披露 / 快讯 三表

- announcements 公告表：巨潮公告（PK symbol+date+title，重复拉取幂等）
- prbook 预约披露表：定期报告预约披露日（PK symbol+report_period）
  —— 财报日避让的核心数据：first_appoint 为首次预约披露日
- news_flash 快讯表：财联社/东财实时快讯（PK ts+title+source，幂等）

与 P3 store.py 风格一致：INSERT OR REPLACE 幂等 upsert。
"""
import pandas as pd

import duckdb
from 数据基础.duckdb.config import DB_PATH

ANN_COLS = ["symbol", "date", "title", "ann_type", "url", "adjunct_url",
            "adj_size", "org_id"]
PRBOOK_COLS = ["symbol", "secname", "report_period", "first_appoint",
               "change1", "change2", "change3", "actual_date"]
NEWS_COLS = ["ts", "title", "content", "level", "source"]

_SCHEMA = {
    "announcements": """CREATE TABLE IF NOT EXISTS announcements (
        symbol VARCHAR, date DATE, title VARCHAR, ann_type VARCHAR,
        url VARCHAR, adjunct_url VARCHAR, adj_size DOUBLE, org_id VARCHAR,
        fetched_at TIMESTAMP DEFAULT current_timestamp,
        PRIMARY KEY (symbol, date, title))""",
    "prbook": """CREATE TABLE IF NOT EXISTS prbook (
        symbol VARCHAR, secname VARCHAR, report_period DATE,
        first_appoint DATE, change1 DATE, change2 DATE, change3 DATE,
        actual_date DATE, fetched_at TIMESTAMP DEFAULT current_timestamp,
        PRIMARY KEY (symbol, report_period))""",
    "news_flash": """CREATE TABLE IF NOT EXISTS news_flash (
        ts TIMESTAMP, title VARCHAR, content VARCHAR,
        level VARCHAR DEFAULT 'normal', source VARCHAR,
        fetched_at TIMESTAMP DEFAULT current_timestamp,
        PRIMARY KEY (ts, title, source))""",
}


def open_db(path=None, read_only: bool = False):
    """打开 duckdb 连接（写模式时确保三表已建）"""
    con = duckdb.connect(str(path or DB_PATH), read_only=read_only)
    if not read_only:
        ensure_schema(con)
    return con


def ensure_schema(con):
    """建表（幂等，含 P3 的 daily/xdxr——若主库尚无则由 P3 store 兜底）"""
    for ddl in _SCHEMA.values():
        con.execute(ddl)


def _insert_or_replace(con, table: str, cols: list[str], df: pd.DataFrame) -> int:
    """INSERT OR REPLACE 公共实现：显式列名 + reindex 补缺列。

    表含 fetched_at DEFAULT 列，全列 SELECT * 会导致列数不匹配（duckdb 的
    INSERT OR REPLACE 要求列数与表结构一致）；显式列名 + 缺列走 DEFAULT 兜底。
    """
    con.register("t", df)
    con.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) SELECT * FROM t")
    con.unregister("t")
    return len(df)


def upsert_announcements(con, rows: list[dict]) -> int:
    """公告 upsert（PK symbol+date+title，重复拉取不产生重复行）。返回写入行数"""
    if not rows:
        return 0
    df = pd.DataFrame(rows).reindex(columns=ANN_COLS).replace("", None)
    return _insert_or_replace(con, "announcements", ANN_COLS, df)


def upsert_prbook(con, rows: list[dict]) -> int:
    """预约披露 upsert（PK symbol+report_period，同一报告期重复拉取覆盖更新）。
    返回写入行数"""
    if not rows:
        return 0
    # 接口对"无变更/未披露"返回空字符串，转 NULL 才能落 DATE 列
    df = pd.DataFrame(rows).reindex(columns=PRBOOK_COLS).replace("", None)
    return _insert_or_replace(con, "prbook", PRBOOK_COLS, df)


def upsert_news(con, rows: list[dict]) -> int:
    """快讯 upsert（PK ts+title+source）。财联社无级别字段 → level 默认 'normal'。
    返回写入行数"""
    if not rows:
        return 0
    df = pd.DataFrame(rows).reindex(columns=NEWS_COLS).replace("", None)
    df["level"] = df["level"].fillna("normal")  # 仅 level 补缺，其他列如实保留
    return _insert_or_replace(con, "news_flash", NEWS_COLS, df)


# ───────────────────────── 查询辅助 ─────────────────────────

def next_prbook_dates(con, symbols: list[str]) -> list[dict]:
    """财报日避让查询（实时视图）：给定股票 → 当前尚未披露的报告期预约披露日

    返回 [{symbol, secname, report_period, first_appoint, actual_date}]，
    按 first_appoint 升序。actual_date 为空 = 尚未披露（避让对象）。
    （C1 财报日避让·2026-08-05 老板拍板；回测历史视图用 prbook_rows。）
    """
    if not symbols:
        return []
    marks = ",".join(f"'{s}'" for s in symbols)
    return con.execute(
        "SELECT symbol, secname, report_period, first_appoint, actual_date "
        f"FROM prbook WHERE symbol IN ({marks}) AND actual_date IS NULL "
        "ORDER BY first_appoint").fetch_df().to_dict("records")


def prbook_rows(con, symbols: list[str]) -> list[dict]:
    """财报日避让查询（回测历史视图）：给定股票 → 全部报告期预约披露行（含已披露）

    返回 [{symbol, secname, report_period, first_appoint, actual_date}]，
    按 first_appoint 升序。actual_date 供"信号日 T 时点是否已披露"判断
    （回测需要历史报告期的披露日参与避让；实时避让用 next_prbook_dates）。
    """
    if not symbols:
        return []
    marks = ",".join(f"'{s}'" for s in symbols)
    return con.execute(
        "SELECT symbol, secname, report_period, first_appoint, actual_date "
        f"FROM prbook WHERE symbol IN ({marks}) "
        "ORDER BY first_appoint").fetch_df().to_dict("records")


def latest_fetched(con, table: str) -> str | None:
    """三表最近一次落库时间（fetched_at 最大值），报告增量新鲜度"""
    return con.execute(
        f"SELECT max(fetched_at) FROM {table}").fetchone()[0]
