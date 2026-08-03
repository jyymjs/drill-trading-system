"""信号跟踪单元测试：止损触发/到期出场/多hold/prebreak触发"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.tracking import Signal, track_signal


def make_df(closes: list[float], highs=None, lows=None) -> pd.DataFrame:
    """手工构造 K 线（价格数组显式可控）"""
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="B"),
        "开盘": closes, "收盘": closes, "最高": highs, "最低": lows,
        "成交量": [100000] * n,
    })


def sig_normal(date_idx: int = 5, close: float = 10.0, stop: float = 9.0) -> Signal:
    return Signal(code="600000", date=pd.Timestamp("2024-01-08"), mode="normal",
                  grade="S", scores={}, close=close, trigger=0.0, stop=stop,
                  risk=close - stop)


def sig_prebreak(date_idx: int = 5, trigger: float = 10.5, stop: float = 9.5,
                 risk: float = 1.0) -> Signal:
    return Signal(code="600000", date=pd.Timestamp("2024-01-08"), mode="prebreak",
                  grade="A", scores={}, close=10.0, trigger=trigger, stop=stop, risk=risk)


# 固定：信号日 = 2024-01-08（第 6 行，索引 5）
BASE_DATES = pd.date_range("2024-01-01", periods=20, freq="B")


def test_stop_loss_triggered():
    """窗口内最低 ≤ 止损 → 止损价出场，stopped=True"""
    closes = [10.0] * 20
    lows = [9.8] * 20
    lows[7] = 8.9            # T+2 日最低 8.9 ≤ 止损 9.0 → 触发
    df = make_df(closes, lows=lows)
    oc = track_signal(sig_normal(), df, hold=10)
    assert oc.triggered and oc.stopped
    assert oc.exit_price == 9.0
    assert oc.exit_date == BASE_DATES[7]
    assert np.isclose(oc.r, (9.0 - 10.0) / 1.0)


def test_expiry_exit():
    """窗口内不破止损 → hold 末收盘出场，stopped=False"""
    closes = [10.0] * 20
    lows = [9.8] * 20         # 最低 9.8 > 止损 9.0
    df = make_df(closes, lows=lows)
    oc = track_signal(sig_normal(), df, hold=10)
    assert oc.triggered and not oc.stopped
    assert oc.exit_price == 10.0
    assert oc.exit_date == BASE_DATES[5 + 10]   # T+10
    assert oc.r == 0.0


def test_multi_hold_different_outcomes():
    """同信号不同 hold：5d 未到止损（到期），10d 止损触发"""
    closes = [10.0] * 20
    lows = [9.8] * 20
    lows[12] = 8.5            # T+7 日破止损
    df = make_df(closes, lows=lows)
    oc5 = track_signal(sig_normal(), df, hold=5)
    oc10 = track_signal(sig_normal(), df, hold=10)
    assert not oc5.stopped and oc5.exit_date == BASE_DATES[10]   # T+5 到期
    assert oc10.stopped and oc10.exit_date == BASE_DATES[12]     # T+7 止损


def test_signal_at_end_of_data():
    """信号日在数据末端：无跟踪空间，R=0 到期退场"""
    closes = [10.0] * 20
    df = make_df(closes)
    s = sig_normal()
    s.date = BASE_DATES[-1]
    oc = track_signal(s, df, hold=5)
    assert oc.exit_price == 10.0 and oc.r == 0.0 and not oc.stopped


def test_stop_not_hit_within_data_bounds():
    """hold 超出数据长度：用最后可用日收盘出场"""
    closes = [10.0] * 20
    lows = [9.9] * 20
    df = make_df(closes, lows=lows)
    oc = track_signal(sig_normal(), df, hold=30)   # T+30 超出 20 行数据
    assert not oc.stopped
    assert oc.exit_date == BASE_DATES[-1]
    assert oc.exit_price == 10.0


# ============================================================
# prebreak：触发/未触发
# ============================================================

def test_prebreak_trigger_and_track():
    """prebreak：最高≥trigger 触发进场；触发后不破止损 → hold 末收盘出场"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [9.6] * 20
    highs[7] = 10.6           # T+2 日触发（≥10.5）
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_prebreak(), df, hold=10)
    assert oc.triggered
    assert oc.entry_price == 10.5
    assert not oc.stopped
    assert oc.exit_date == BASE_DATES[15]
    assert np.isclose(oc.r, (10.0 - 10.5) / 1.0)  # 进场 10.5 → 出场 10.0


def test_prebreak_not_triggered():
    """prebreak 未触发：triggered=False，不参与统计"""
    closes = [10.0] * 20
    highs = [10.0] * 20       # 最高恒 10.0 < trigger 10.5
    df = make_df(closes, highs=highs)
    oc = track_signal(sig_prebreak(), df, hold=10)
    assert not oc.triggered
    assert not oc.participate()
    assert oc.entry_price == 0.0 and oc.r == 0.0


def test_prebreak_trigger_then_stop():
    """prebreak 触发后破止损 → 止损价出场"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [9.6] * 20
    highs[7] = 10.6
    lows[9] = 9.4             # 触发后（T+4）最低 9.4 ≤ 止损 9.5
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_prebreak(), df, hold=10)
    assert oc.triggered and oc.stopped
    assert oc.entry_price == 10.5
    assert oc.exit_price == 9.5
    assert oc.exit_date == BASE_DATES[9]
    assert np.isclose(oc.r, (9.5 - 10.5) / 1.0)


def test_prebreak_trigger_requires_strict_higher():
    """触发条件：最高必须 ≥ trigger（等于也触发，边界）"""
    closes = [10.0] * 20
    highs = [10.5] * 20       # 恰等于 trigger
    df = make_df(closes, highs=highs)
    oc = track_signal(sig_prebreak(), df, hold=10)
    assert oc.triggered
    assert oc.entry_price == 10.5


def test_risk_zero_guard():
    """risk=0 时 R 记 0 不除零"""
    closes = [10.0] * 20
    lows = [9.5] * 20
    df = make_df(closes, lows=lows)
    oc = track_signal(sig_normal(stop=10.0), df, hold=5)   # risk = 0
    assert oc.r == 0.0
