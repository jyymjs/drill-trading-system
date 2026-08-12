"""R-080 G12 qfq→实盘价换算层单测（2026-08-13）

验证换算层（reader.qfq_price_to_real / get_factor）：
  实盘委托价 = qfq 价 ÷ factor（factor = 该日相对最新价前复权乘数）
"""
import numpy as np
import pandas as pd
import pytest

from 数据基础.duckdb.reader import get_factor, qfq_price_to_real


# ───────────────────────── qfq_price_to_real ─────────────────────────

def test_real_price_divide_by_factor():
    """10送10（factor=0.5）：qfq 价 ÷ 0.5 = 实盘价（还原历史真实价）"""
    assert qfq_price_to_real(5.0, 0.5) == pytest.approx(10.0)
    assert qfq_price_to_real(9.8, 0.49) == pytest.approx(20.0, rel=1e-3)


def test_real_price_no_exception_factor_one():
    """factor=1（无除权）：价不变"""
    assert qfq_price_to_real(12.34, 1.0) == pytest.approx(12.34)


def test_real_price_invalid_factor_fallback():
    """factor 缺失/0/负 → 原价（防御）"""
    assert qfq_price_to_real(8.5, 0.0) == 8.5
    assert qfq_price_to_real(8.5, -1.0) == 8.5
    assert qfq_price_to_real(8.5, None) == 8.5


def test_real_price_rounding_4dp():
    """输出 4 位小数（委托价精度）"""
    assert qfq_price_to_real(7.123456, 0.8) == pytest.approx(8.9043, abs=1e-4)


# ───────────────────────── get_factor（临时库注入）─────────────────────────

def _make_db(tmp_path):
    """构造临时 duckdb：000001 3 日线 + 2026-01-05 除权（10送10）"""
    import duckdb
    db = tmp_path / "t.db"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE daily (
        symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, vol DOUBLE, amount DOUBLE)""")
    rows = [("000001", "2026-01-02", 20.0, 20.5, 19.8, 20.2, 1e5, 1e6),
            ("000001", "2026-01-05", 10.2, 10.5, 10.0, 10.4, 2e5, 2e6),
            ("000001", "2026-01-06", 10.4, 10.6, 10.1, 10.5, 1.5e5, 1.6e6)]
    con.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("""CREATE TABLE xdxr (
        symbol VARCHAR, date DATE, category INTEGER,
        fenhong DOUBLE, peigujia DOUBLE, songzhuangu DOUBLE, peigu DOUBLE)""")
    con.execute("""INSERT INTO xdxr VALUES
        ('000001', '2026-01-05', 1, 0.0, 0.0, 10.0, 0.0)""")
    con.close()
    return str(db)


def test_get_factor_before_and_after_exdividend(tmp_path):
    """除权日前 factor<1（历史 qfq 下调），除权日及以后 =1.0（最新基准）"""
    db = _make_db(tmp_path)
    f_pre = get_factor("000001", "2026-01-02", db_path=db)
    assert f_pre == pytest.approx(0.5, rel=1e-3)   # 10送10 → 0.5
    assert get_factor("000001", "2026-01-05", db_path=db) == pytest.approx(1.0)
    assert get_factor("000001", "2026-01-06", db_path=db) == pytest.approx(1.0)


def test_get_factor_roundtrip_real_price(tmp_path):
    """换算闭环：历史 qfq 收盘 ÷ factor = 实盘收盘（10.2×0.5 ÷ 0.5 = 10.2）"""
    db = _make_db(tmp_path)
    f_pre = get_factor("000001", "2026-01-02", db_path=db)
    # qfq 收盘（2026-01-02，除权前）= 实盘 10.2 × 0.5 = 5.1
    qfq_close = 10.2 * f_pre
    assert qfq_price_to_real(qfq_close, f_pre) == pytest.approx(10.2)


def test_get_factor_missing_symbol(tmp_path):
    """无该股 → 1.0（防御）"""
    db = _make_db(tmp_path)
    assert get_factor("999999", "2026-01-05", db_path=db) == 1.0
