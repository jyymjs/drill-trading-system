"""duckdb 存储层单测（T-017 P3）

覆盖：建表结构、upsert PK 去重幂等、无重复校验、尾部缺口校验。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 数据基础.duckdb import store as S


@pytest.fixture()
def con(tmp_path):
    """临时库连接（每测独立）"""
    c = S.open_db(tmp_path / "test.duckdb")
    yield c
    c.close()


def test_schema_created(con):
    """建表：daily PK (symbol,date)，xdxr PK (symbol,date,category)"""
    d = con.execute("SELECT count(*) FROM duckdb_constraints() c "
                    "JOIN duckdb_tables() t ON t.table_name = c.table_name "
                    "WHERE c.constraint_type = 'PRIMARY KEY' AND c.table_name IN ('daily','xdxr')").fetchone()
    assert d[0] == 2


def test_upsert_idempotent_no_duplicate(con):
    """同 (symbol,date) 重复 upsert → 不产生重复行（INSERT OR REPLACE 幂等）"""
    df1 = pd.DataFrame({
        "date": [pd.Timestamp("2026-08-03").date(), pd.Timestamp("2026-08-04").date()],
        "open": [10.0, 10.5], "high": [10.8, 11.0], "low": [9.9, 10.2],
        "close": [10.5, 10.9], "vol": [1000.0, 1200.0], "amount": [1e6, 1.3e6],
    })
    S.upsert_daily(con, "000001", df1)

    # 再次插入，含同日不同价格（应被替换而非新增）与一行新日期
    df2 = pd.DataFrame({
        "date": [pd.Timestamp("2026-08-04").date(), pd.Timestamp("2026-08-05").date()],
        "open": [10.6, 11.0], "high": [11.1, 11.2], "low": [10.3, 10.8],
        "close": [11.0, 11.1], "vol": [1300.0, 900.0], "amount": [1.4e6, 1.0e6],
    })
    S.upsert_daily(con, "000001", df2)

    n = con.execute("SELECT count(*) FROM daily WHERE symbol='000001'").fetchone()[0]
    assert n == 3, f"应有 3 行（去重后），实际 {n}"
    dup = S.check_no_duplicate(con)
    assert dup["daily_dups"] == 0 and dup["xdxr_dups"] == 0
    # 08-04 应为第二次插入的值（replace 生效）
    v = con.execute("SELECT close FROM daily WHERE symbol='000001' AND date='2026-08-04'").fetchone()
    assert v[0] == pytest.approx(11.0)


def test_upsert_xdxr_full_replace(con):
    """xdxr 全量重插：同 (symbol,date,category) 幂等"""
    x1 = pd.DataFrame({
        "date": [pd.Timestamp("2025-11-20").date()] * 2,
        "category": [9, 15],
        "name": ["转配股上市", "15"], "fenhong": [0.0, 0.0], "peigujia": [0.0, 0.0],
        "songzhuangu": [0.0, 0.0], "peigu": [0.0, 0.0], "suogu": [0.0, 0.0],
        "fenshu": [0.0, 0.0], "xingquanjia": [0.0, 0.0],
    })
    S.upsert_xdxr(con, "300093", x1)
    S.upsert_xdxr(con, "300093", x1)
    n = con.execute("SELECT count(*) FROM xdxr WHERE symbol='300093'").fetchone()[0]
    assert n == 2
    assert S.check_no_duplicate(con) == {"daily_dups": 0, "xdxr_dups": 0}


def test_tail_gap_check(con):
    """尾部缺口：落后超过阈值的天数被统计、未落后不报"""
    def daily(sym, dates):
        return pd.DataFrame({
            "date": dates, "open": [10.0] * len(dates), "high": [10.5] * len(dates),
            "low": [9.5] * len(dates), "close": [10.2] * len(dates),
            "vol": [1000.0] * len(dates), "amount": [1e6] * len(dates),
        })
    S.upsert_daily(con, "000001", daily("000001",
        [pd.Timestamp("2026-08-01").date(), pd.Timestamp("2026-08-05").date()]))
    S.upsert_daily(con, "000002", daily("000002",
        [pd.Timestamp("2026-06-01").date()]))  # 落后 60+ 天（停牌模拟）

    gaps = S.check_tail_gaps(con, max_behind_days=5)
    assert gaps["db_latest"] == "2026-08-05"
    assert gaps["behind_over_days"] == 1
    assert gaps["top_behind"][0][0] == "000002"


def test_known_symbols(con):
    """known_symbols 返回库内全部 symbol"""
    S.upsert_daily(con, "000001", pd.DataFrame({
        "date": [pd.Timestamp("2026-08-05").date()], "open": [10.0], "high": [10.5],
        "low": [9.5], "close": [10.2], "vol": [1000.0], "amount": [1e6],
    }))
    assert S.known_symbols(con) == ["000001"]
