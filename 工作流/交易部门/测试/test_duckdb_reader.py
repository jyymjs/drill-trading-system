"""duckdb 读层 + 主链路切换单测（T-017 P5）

覆盖：
- compute_qfq：等比因子自算前复权（无除权=原始价；有除权与手工因子一致；四价同乘）
- read_kline：窗口覆盖判断（越界/缺股 → None）、中文列输出
- fetcher.get_daily_kline duckdb 优先分支（monkeypatch 读层验证优先与回退）
- CacheDataProvider：duckdb 全量优先、缺省回退 deprecated CSV 层
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

from 数据基础.duckdb import store as S
from 数据基础.duckdb.reader import compute_qfq, read_kline


@pytest.fixture()
def db(tmp_path):
    """临时库：000001 含一次除权（10派2=每股派 0.2 元），600000 无除权

    注意：写入完成后必须 close 写连接——DuckDB 同一文件同进程只允许一种
    连接配置，写连接存在时 read_only 连接打开会抛 ConnectionException。
    """
    path = tmp_path / "p5.duckdb"
    con = S.open_db(path)
    dates = pd.bdate_range("2026-01-01", periods=4)
    daily = pd.DataFrame({
        "date": [d.date() for d in dates],
        "open": [10.0, 10.2, 10.4, 10.6], "high": [10.5, 10.7, 10.9, 11.1],
        "low": [9.8, 10.0, 10.2, 10.4], "close": [10.2, 10.4, 10.6, 10.8],
        "vol": [1e5, 1.1e5, 1.2e5, 1.3e5], "amount": [1e6, 1.1e6, 1.2e6, 1.3e6],
    })
    S.upsert_daily(con, "000001", daily)
    S.upsert_daily(con, "600000", daily)
    # 000001 第 2 个交易日除权：10派2（fenhong=2.0），除权日=第 2 天
    xdxr = pd.DataFrame({
        "date": [dates[1].date()], "category": [1], "name": ["10派2"],
        "fenhong": [2.0], "peigujia": [0.0], "songzhuangu": [0.0],
        "peigu": [0.0], "suogu": [0.0], "fenshu": [0.0], "xingquanjia": [0.0],
    })
    S.upsert_xdxr(con, "000001", xdxr)
    con.close()
    yield path


# ───────────────────────── compute_qfq ─────────────────────────

def test_qfq_no_xdxr_factor_is_one():
    """无除权：factor 恒 1，四价 = 原始价"""
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
        "open": [10.0, 10.2, 10.4], "high": [10.5, 10.7, 10.9],
        "low": [9.8, 10.0, 10.2], "close": [10.2, 10.4, 10.6],
        "vol": [1e5] * 3, "amount": [1e6] * 3,
    })
    q = compute_qfq(daily, None)
    assert (q["factor"] == 1.0).all()
    for c in ("open", "high", "low", "close"):
        assert np.allclose(q[f"qfq_{c}"], daily[c])


def test_qfq_dividend_factor_hand_math():
    """10派2（每股 0.2 元）：除权日前行 factor=0.98，除权日及以后=1.0"""
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
        "open": [10.0, 10.2, 10.4], "high": [10.5, 10.7, 10.9],
        "low": [9.8, 10.0, 10.2], "close": [10.2, 10.4, 10.6],
        "vol": [1e5] * 3, "amount": [1e6] * 3,
    })
    xdxr = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05"]), "category": [1],
        "fenhong": [2.0], "peigujia": [0.0], "songzhuangu": [0.0], "peigu": [0.0],
    })
    q = compute_qfq(daily, xdxr)
    # 除权日前一日收盘 10.2 → f = (10.2 - 0.2) / 10.2 = 0.980392...
    f_expected = (10.2 - 0.2) / 10.2
    assert q.loc[0, "factor"] == pytest.approx(f_expected)
    assert q.loc[1, "factor"] == 1.0 and q.loc[2, "factor"] == 1.0
    # 四价同乘
    assert q.loc[0, "qfq_close"] == pytest.approx(10.2 * f_expected)
    assert q.loc[0, "qfq_open"] == pytest.approx(10.0 * f_expected)
    assert q.loc[1, "qfq_close"] == pytest.approx(10.4)


# ───────────────────────── read_kline ─────────────────────────

def test_read_kline_full_and_columns(db):
    """全量读取：中文列齐全，qfq 四价与手工一致"""
    k = read_kline("000001", db_path=db)
    assert k is not None and len(k) == 4
    for col in ("日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额",
                "涨跌幅", "涨跌额", "振幅", "换手率"):
        assert col in k.columns, f"缺列 {col}"
    # 与 compute_qfq 手工结果一致（第 1 行因子 = (10.2-0.2)/10.2）
    f = (10.2 - 0.2) / 10.2
    assert k.loc[0, "收盘"] == pytest.approx(10.2 * f)
    assert k.loc[3, "收盘"] == pytest.approx(10.8)


def test_read_kline_window_filter(db):
    """窗口过滤：只返回窗口内行"""
    k = read_kline("000001", start="20260105", end="20260106", db_path=db)
    assert len(k) == 2
    assert str(k.iloc[0]["日期"].date()) == "2026-01-05"


def test_read_kline_missing_symbol_none(db):
    """库内无该股 → None（回退信号）"""
    assert read_kline("999999", db_path=db) is None


def test_read_kline_window_out_of_coverage_none(db):
    """窗口早于库内起点 / 晚于库内最新 → None（回退信号）"""
    assert read_kline("000001", start="20250101", end="20260106", db_path=db) is None
    assert read_kline("000001", start="20260105", end="20270101", db_path=db) is None


def test_read_kline_raw_adjust(db):
    """adjust 非 qfq → 原始价（不复权）"""
    k = read_kline("000001", adjust="", db_path=db)
    assert k.loc[0, "收盘"] == pytest.approx(10.2)


# ──────────────────── fetcher duckdb 优先分支 ────────────────────

def test_fetcher_duckdb_priority(monkeypatch):
    """duckdb 命中 → 直接返回，不落 CSV/网络"""
    import 数据基础.duckdb.reader as reader_mod
    df4 = pd.DataFrame({
        "日期": pd.to_datetime(["2026-01-02", "2026-01-05"]),
        "开盘": [10.0, 10.2], "收盘": [10.2, 10.4], "最高": [10.5, 10.7],
        "最低": [9.8, 10.0], "成交量": [1e5] * 2, "成交额": [1e6] * 2,
    })
    monkeypatch.setattr(reader_mod, "read_kline", lambda *a, **k: df4)
    from 数据基础.数据.fetcher import get_daily_kline
    df = get_daily_kline("000001", use_cache=True)
    assert len(df) == 2 and "收盘" in df.columns


def test_fetcher_duckdb_miss_falls_back_to_network_chain(monkeypatch):
    """duckdb 未命中（None）→ 分支静默回退不抛异常，走后续链路"""
    import 数据基础.duckdb.reader as reader_mod
    monkeypatch.setattr(reader_mod, "read_kline", lambda *a, **k: None)
    from 数据基础.数据.fetcher import get_daily_kline
    df = get_daily_kline("999999", use_cache=False)   # 无缓存/无库 → 网络链路 → 大概率空
    assert isinstance(df, pd.DataFrame)               # 不抛异常即通过


# ──────────────────── CacheDataProvider ────────────────────

def test_provider_duckdb_first(db):
    """provider 直读 duckdb 全量（无窗口限制）"""
    from 回测系统.adapters.data_provider import CacheDataProvider
    p = CacheDataProvider(db_path=db)
    df = p.load("000001")
    assert len(df) == 4
    assert {"日期", "收盘", "最高", "最低"} <= set(df.columns)


def test_provider_falls_back_to_csv(db, tmp_path, monkeypatch):
    """duckdb 缺股 → 回退 deprecated CSV 层"""
    import 数据基础.数据.cache as cache_mod
    from 回测系统.adapters.data_provider import CacheDataProvider

    monkeypatch.setattr(cache_mod, "DATA_DIR", tmp_path)   # CSV 目录指向临时
    csv_df = pd.DataFrame({
        "日期": pd.to_datetime(["2026-01-02", "2026-01-05"]),
        "开盘": [9.0, 9.2], "收盘": [9.4, 9.6], "最高": [9.5, 9.7], "最低": [8.9, 9.1],
        "成交量": [1e5] * 2,
    })
    csv_df.to_csv(tmp_path / "999999.csv", index=False)

    p = CacheDataProvider(db_path=db)   # 临时库中无 999999
    df = p.load("999999")
    assert len(df) == 2
    assert df.iloc[-1]["收盘"] == pytest.approx(9.6)
