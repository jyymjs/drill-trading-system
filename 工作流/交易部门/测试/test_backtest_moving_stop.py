"""C5 移动止损单元测试（2026-08-05 老板拍板 · 价格行为学04课借鉴）

验收口径：
  - 新结构低点识别：买入后新高 → 回调低点 → 次日不再创新低（收盘判定）→ 确认
  - 上移触发：新止损 = 低点×0.99，须高于当前止损 且 高于进场价（六层第3层正向硬规则）
  - 不触发场景：无新高 / 低点×0.99≤当前止损 / 候选未确认 / 新止损低于进场价
  - 默认 moving_stop=False 保持现有「止损+hold到期收盘」行为不变
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包（R-005 独立项目）

from 回测系统.tracking import Signal, track_signal


def make_df(closes: list[float], highs=None, lows=None, opens=None) -> pd.DataFrame:
    """手工构造 K 线（价格数组显式可控）

    opens 缺省 = closes（旧构造）；传 opens 可模拟跳空/低开（R-080 G10 场景）。
    """
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    opens = opens or closes
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="B"),
        "开盘": opens, "收盘": closes, "最高": highs, "最低": lows,
        "成交量": [100000] * n,
    })


def sig_normal(close: float = 10.0, stop: float = 9.0) -> Signal:
    return Signal(code="600000", date=pd.Timestamp("2024-01-08"), mode="normal",
                  grade="S", scores={}, close=close, trigger=0.0, stop=stop,
                  risk=close - stop)


def sig_prebreak(trigger: float = 10.5, stop: float = 9.5, risk: float = 1.0) -> Signal:
    return Signal(code="600000", date=pd.Timestamp("2024-01-08"), mode="prebreak",
                  grade="A", scores={}, close=10.0, trigger=trigger, stop=stop, risk=risk)


# 固定：信号日 = 2024-01-08（第 6 行，索引 5）
BASE_DATES = pd.date_range("2024-01-01", periods=20, freq="B")


# ============================================================
# 1. 新低点识别 + 上移触发
# ============================================================

def test_two_level_trail_raises_stop_then_exit_at_new_stop():
    """两级台阶上移：新高→回调→确认上移（10.2）→再新高→再回调→再上移（≈10.4）→跌破以新止损出场"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [10.0] * 20
    highs[6] = 11.2; highs[7] = 11.5; highs[8] = 11.3; highs[9] = 11.8; highs[10] = 11.6
    lows[6] = 11.0; lows[7] = 10.3; lows[8] = 10.6; lows[9] = 10.5; lows[10] = 10.8
    lows[11] = 10.2            # 跌破第二级止损 ≈10.4 → 以新止损出场
    # G10（2026-08-13）：open 全 10.5 > 止损——非跳空场景，仍按止损价成交
    df = make_df(closes, highs=highs, lows=lows, opens=[10.5] * 20)
    oc = track_signal(sig_normal(), df, hold=10, moving_stop=True)
    assert oc.triggered and oc.stopped
    # 第一级 10.3×0.99=10.197→10.2；第二级 10.5×0.99=10.395→≈10.4（浮点容差）
    assert np.isclose(oc.exit_price, 10.4, atol=0.011)
    assert oc.exit_price > oc.entry_price            # 移动止损后出场价高于进场价
    assert oc.exit_date == BASE_DATES[11]


def test_trail_raised_but_never_hit_exits_at_hold_end():
    """止损上移后价格守住 → hold 末收盘出场（不误伤）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [10.5] * 20
    highs[6] = 11.2; highs[7] = 11.5
    lows[7] = 10.3; lows[8] = 10.6          # 确认回调低点 10.3 → 止损上移 10.2
    lows[9:] = [10.25] * 11                 # 之后守住 10.2
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_normal(), df, hold=10, moving_stop=True)
    assert not oc.stopped
    assert oc.exit_price == 10.0
    assert oc.exit_date == BASE_DATES[15]


# ============================================================
# 2. 不触发场景
# ============================================================

def test_no_new_high_trail_not_active():
    """无买入后新高（持续阴跌）→ 移动止损不生效 → 原止损出场"""
    closes = [10.0] * 20
    highs = [9.8] * 20
    lows = [9.7] * 20
    lows[14] = 8.9                 # 跌破原止损 9.0
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_normal(), df, hold=10, moving_stop=True)
    assert oc.stopped
    assert oc.exit_price == 9.0               # 原止损价（未被上移）
    assert oc.exit_date == BASE_DATES[14]


def test_trail_low_times_099_below_current_stop_no_raise():
    """回调低点×0.99 ≤ 当前止损 → 不上移（低点 9.55 → 9.45 < 止损 9.5）"""
    closes = [10.0] * 20
    highs = [10.5] * 20
    lows = [10.0] * 20
    highs[6] = 11.0; highs[7] = 11.2
    lows[6] = 10.5; lows[7] = 9.55; lows[8] = 9.7; lows[9] = 9.4
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_normal(stop=9.5), df, hold=10, moving_stop=True)
    assert oc.stopped
    assert oc.exit_price == 9.5               # 原止损价（9.55×0.99=9.45 < 9.5，不上移）
    assert oc.exit_date == BASE_DATES[9]


def test_candidate_not_confirmed_within_window():
    """候选低点持续下移、窗口内未确认 → 止损从未上移 → hold 末收盘出场"""
    closes = [10.0] * 20
    highs = [10.8] * 20
    lows = [10.0] * 20
    highs[6] = 11.2; highs[7] = 11.4
    lows[6] = 10.5; lows[7] = 10.4; lows[8] = 10.3; lows[9] = 10.2
    lows[10] = 10.1; lows[11] = 10.05; lows[12:] = [10.05] * 8   # 之后不高于候选 → 不确认
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_normal(), df, hold=10, moving_stop=True)
    assert not oc.stopped
    assert oc.exit_price == 10.0
    assert oc.exit_date == BASE_DATES[15]


def test_new_stop_below_entry_rejected():
    """正向硬规则（六层第3层）：新止损 > 当前止损 但 < 进场价 → 不上移"""
    closes = [10.0] * 20
    highs = [10.5] * 20
    lows = [10.0] * 20
    highs[6] = 11.0; highs[7] = 11.5
    lows[6] = 9.8; lows[7] = 9.9; lows[8] = 7.9        # 低点 9.8 → 9.8×0.99=9.7 > 8.0 但 < 10.0
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_normal(stop=8.0), df, hold=10, moving_stop=True)
    assert oc.stopped
    assert oc.exit_price == 8.0                        # 原止损价（正向硬规则拒绝上移）
    assert oc.exit_date == BASE_DATES[8]


# ============================================================
# 3. prebreak 模式 + 开关对照
# ============================================================

def test_prebreak_with_moving_stop():
    """prebreak：触发后新高→回调确认→止损上移 10.7×0.99≈10.59 → 跌破以新止损出场"""
    closes = [10.0] * 20
    highs = [11.0] * 20
    lows = [10.0] * 20
    highs[7] = 10.6                                     # 触发日（trigger=10.5）
    highs[8] = 11.8; highs[9] = 11.9
    lows[7] = 10.4; lows[8] = 10.9; lows[9] = 10.7      # 回调低点 10.7
    lows[10] = 10.8                                     # 确认 → 10.7×0.99≈10.59
    lows[11] = 10.5                                     # 跌破 10.59
    df = make_df(closes, highs=highs, lows=lows)
    oc = track_signal(sig_prebreak(), df, hold=12, moving_stop=True)
    assert oc.triggered and oc.stopped
    assert oc.entry_price == 10.5
    assert np.isclose(oc.exit_price, 10.7 * 0.99, atol=0.01)
    assert oc.exit_date == BASE_DATES[11]


def test_switch_off_preserves_legacy_behavior():
    """开关对照：同数据下 moving_stop=False 走原止损 9.0；True 止损上移 10.2 后触发"""
    closes = [10.0] * 20
    highs = [10.5] * 20
    lows = [10.5] * 20
    highs[6] = 11.2; highs[7] = 11.5
    lows[7] = 10.3; lows[8] = 10.6; lows[14] = 8.9      # 跌破原止损 9.0
    # G10：open 全 10.5 > 止损——非跳空，按止损价成交（保持原行为断言）
    df = make_df(closes, highs=highs, lows=lows, opens=[10.5] * 20)
    oc_off = track_signal(sig_normal(), df, hold=10)                 # 默认关
    oc_on = track_signal(sig_normal(), df, hold=10, moving_stop=True)
    assert oc_off.stopped and oc_off.exit_price == 9.0               # 原行为：原止损价出场
    assert oc_on.stopped and np.isclose(oc_on.exit_price, 10.3 * 0.99, atol=0.01)  # 移动止损出场
    assert oc_on.exit_date == oc_off.exit_date == BASE_DATES[14]


# ============================================================
# R-080 G10：跳空/一字跌停成交假设（2026-08-13）
# ============================================================

def test_g10_gap_open_below_stop_exits_at_open():
    """G10：跌破止损日开盘 < 止损（低开穿越，非一字跌停）→ 按当日开盘成交（实盘口径）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [10.0] * 20
    lows[14] = 8.9                    # 跌破止损 9.0
    opens = [10.0] * 20
    opens[14] = 8.7                   # 当日低开 8.7 < 止损 9.0 → 按 8.7 成交
    df = make_df(closes, highs=highs, lows=lows, opens=opens)
    oc = track_signal(sig_normal(), df, hold=10)
    assert oc.stopped
    assert np.isclose(oc.exit_price, 8.7, atol=0.001)
    assert oc.exit_date == BASE_DATES[14]


def test_g10_one_line_limit_down_defers_to_next_day():
    """G10：止损日一字跌停（开=高=低=收=跌停价）→ 卖不出，顺延下一交易日"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [10.0] * 20
    limit = round(10.0 * 0.905, 2)    # 主板跌停价 9.05
    closes[14] = limit; highs[14] = limit; lows[14] = limit
    closes[15] = 9.2; highs[15] = 9.3; lows[15] = 9.1    # 次日开板回升
    opens = [10.0] * 20
    opens[14] = limit; opens[15] = 9.2
    df = make_df(closes, highs=highs, lows=lows, opens=opens)
    oc = track_signal(sig_normal(stop=9.2), df, hold=15)   # 止损 9.2：一字日 9.05 触发但被堵
    assert oc.stopped
    assert np.isclose(oc.exit_price, 9.2, atol=0.001)      # 顺延次日开盘 9.2 不低开 → 按止损价
    assert oc.exit_date == BASE_DATES[15]
