"""次新股新浪补齐单测（T-017 P3 · P2 遗留问题 2）

覆盖：新浪代码映射、raw/qfq 一致性判定（无除权 vs 疑似除权）。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 数据基础.duckdb.sina_backfill import check_no_xdxr, to_sina_symbol


def test_to_sina_symbol():
    """代码 → 新浪 sh/sz 前缀"""
    assert to_sina_symbol("603468") == "sh603468"
    assert to_sina_symbol("688836") == "sh688836"
    assert to_sina_symbol("301655") == "sz301655"
    assert to_sina_symbol("000001") == "sz000001"


def make_raw(dates, closes):
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})


class TestCheckNoXdxr:
    def test_no_xdxr_consistent_prices(self):
        """raw 与 qfq 全区间一致 → 判定无除权（次新股上市以来无除权）"""
        raw = make_raw(["2026-01-05", "2026-01-06", "2026-01-07"], [20.0, 20.5, 20.3])
        qfq = make_raw(["2026-01-05", "2026-01-06", "2026-01-07"], [20.0, 20.5, 20.3])
        no_xdxr, events = check_no_xdxr(raw, qfq)
        assert no_xdxr is True
        assert events == []

    def test_xdxr_event_detected(self):
        """存在除权：除权日后 qfq != raw → 判定有除权并列出除权日与复权比"""
        raw = make_raw(["2025-11-14", "2025-11-18", "2025-11-20", "2025-11-21"],
                       [14.16, 14.83, 15.42, 14.13])
        qfq = make_raw(["2025-11-14", "2025-11-18", "2025-11-20", "2025-11-21"],
                       [12.47, 13.06, 15.42, 14.13])
        no_xdxr, events = check_no_xdxr(raw, qfq)
        assert no_xdxr is False
        assert len(events) == 1
        assert events[0]["date"] == "2025-11-20"   # 因子变化起始日 = 除权日
        assert events[0]["factor"] == pytest.approx(1.0, abs=1e-3)

    def test_empty_input_safe(self):
        """空输入不报错、判定无除权"""
        assert check_no_xdxr(pd.DataFrame(), None) == (False, [])
        assert check_no_xdxr(None, None) == (False, [])

    def test_float_rounding_tolerance(self):
        """微小数值误差（浮点）不误判为除权"""
        raw = make_raw(["2026-01-05", "2026-01-06"], [10.0, 10.5])
        qfq = make_raw(["2026-01-05", "2026-01-06"], [10.0000001, 10.5000001])
        no_xdxr, _ = check_no_xdxr(raw, qfq)
        assert no_xdxr is True
