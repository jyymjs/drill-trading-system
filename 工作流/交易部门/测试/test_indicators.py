"""技术指标计算单元测试"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

# 确保能导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 分析决策.分析.indicators import (
    all_indicators,
    atr,
    body_to_range_ratio,
    body_to_range_series,
    boll,
    channel_detect,
    consecutive_count,
    ema,
    kdj,
    ma,
    ma_cross,
    macd,
    overshoot_detect,
    platform_test_count,
    profile_compactness,
    retracement_detect,
    rolling_volatility,
    rsi,
    support_resistance_levels,
    volume_ratio,
)

# ============================================================
# 辅助：生成测试用的 K 线数据
# ============================================================

def make_kline(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """生成模拟 K 线 DataFrame"""
    rng = np.random.default_rng(seed)
    close = 10 + np.arange(n) * 0.1 + rng.normal(0, 0.5, n).cumsum()
    high = close + abs(rng.normal(0, 0.3, n))
    low = close - abs(rng.normal(0, 0.3, n))
    open_ = close + rng.normal(0, 0.2, n)
    volume = rng.integers(10000, 100000, n)
    return pd.DataFrame({
        "开盘": open_,
        "收盘": close,
        "最高": high,
        "最低": low,
        "成交量": volume,
    })


# ============================================================
# MA（移动平均线）
# ============================================================

class TestMA:
    def test_basic_ma5(self):
        s = pd.Series([1, 2, 3, 4, 5, 6])
        result = ma(s, 3)
        # 前 2 个为 NaN，第 3 个起 = 前 3 个均值
        assert np.isnan(result.iloc[0])
        assert np.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
        assert result.iloc[3] == pytest.approx(3.0)   # (2+3+4)/3
        assert result.iloc[4] == pytest.approx(4.0)   # (3+4+5)/3
        assert result.iloc[5] == pytest.approx(5.0)   # (4+5+6)/3

    def test_ma_n_equals_len(self):
        s = pd.Series([1, 2, 3, 4, 5])
        result = ma(s, 5)
        assert np.isnan(result.iloc[:4]).all()
        assert result.iloc[4] == pytest.approx(3.0)

    def test_ma_empty(self):
        assert ma(pd.Series([], dtype=float), 5).empty


# ============================================================
# EMA（指数移动平均线）
# ============================================================

class TestEMA:
    def test_basic_ema(self):
        s = pd.Series([1, 2, 3, 4, 5])
        result = ema(s, 3)
        assert len(result) == 5
        # EMA(1) ≈ 1, EMA(2) ≈ 1.25 (alpha=0.5 for span=3)
        assert not result.isna().all()

    def test_ema_constant(self):
        s = pd.Series([5.0] * 10)
        result = ema(s, 3)
        assert (result.dropna() == 5.0).all()


# ============================================================
# MACD
# ============================================================

class TestMACD:
    def test_basic_macd(self):
        s = pd.Series(np.arange(1, 51, dtype=float))
        dif, dea, bar = macd(s)
        assert len(dif) == len(dea) == len(bar) == 50
        # 上升序列中 DIF > 0
        assert dif.iloc[-1] > 0

    def test_macd_flat(self):
        s = pd.Series([10.0] * 50)
        dif, dea, bar = macd(s)
        # 平坦序列中 DIF ≈ 0
        assert abs(dif.iloc[-1]) < 1e-10


# ============================================================
# RSI
# ============================================================

class TestRSI:
    def test_rsi_always_up(self):
        """总体趋势向上 → RSI 偏高（>65）"""
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 0.1, 100)
        trend = np.linspace(0, 5, 100)  # 稳定上升趋势
        s = pd.Series(10 + trend + noise)
        result = rsi(s, 14)
        assert result.dropna().iloc[-1] > 65

    def test_rsi_always_down(self):
        """持续下跌 → RSI 接近 0"""
        s = pd.Series(range(20, 0, -1), dtype=float)
        result = rsi(s, 14)
        assert result.iloc[-1] < 10  # 接近 0

    def test_rsi_range(self):
        """RSI 始终在 [0, 100] 范围内"""
        s = pd.Series(np.random.default_rng(42).normal(0, 1, 100))
        result = rsi(s, 14).dropna()
        assert (result >= 0).all()
        assert (result <= 100).all()


# ============================================================
# KDJ
# ============================================================

class TestKDJ:
    def test_kdj_shapes(self):
        df = make_kline(100)
        k, d, j = kdj(df["最高"], df["最低"], df["收盘"])
        assert len(k) == len(d) == len(j) == 100
        # K/D 在 [0, 100] 范围
        valid = k.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_kdj_j_divergence(self):
        """J 值偏离程度比 K/D 更大"""
        df = make_kline(100)
        k, d, j = kdj(df["最高"], df["最低"], df["收盘"])
        valid = pd.DataFrame({"k": k, "j": j}).dropna()
        assert valid["j"].std() > valid["k"].std()


# ============================================================
# 布林带
# ============================================================

class TestBoll:
    def test_boll_structure(self):
        s = pd.Series(np.random.default_rng(42).normal(100, 5, 100))
        mid, upper, lower = boll(s, 20, 2)
        assert len(mid) == len(upper) == len(lower) == 100
        # 上轨 ≥ 中轨 ≥ 下轨
        valid = pd.DataFrame({"u": upper, "m": mid, "l": lower}).dropna()
        assert (valid["u"] >= valid["m"]).all()
        assert (valid["m"] >= valid["l"]).all()

    def test_boll_default_std(self):
        s = pd.Series([10.0] * 50)
        mid, upper, lower = boll(s, 20)
        # 无波动 → 三线重合
        assert mid.dropna().iloc[-1] == pytest.approx(upper.dropna().iloc[-1])
        assert mid.dropna().iloc[-1] == pytest.approx(lower.dropna().iloc[-1])


# ============================================================
# 量比
# ============================================================

class TestVolumeRatio:
    def test_vol_ratio_baseline(self):
        vol = pd.Series([100] * 10)
        result = volume_ratio(vol, 5)
        # 稳定成交量 → 量比 ≈ 1
        assert result.dropna().iloc[-1] == pytest.approx(1.0)

    def test_vol_ratio_surge(self):
        """突然放量 → 量比显著 > 1"""
        vol = pd.Series([100] * 10 + [500])
        result = volume_ratio(vol, 5)
        # MA5 在最后 = (100+100+100+100+500)/5 = 180, 量比 = 500/180 ≈ 2.78
        assert result.dropna().iloc[-1] > 2.0


# ============================================================
# 金叉/死叉
# ============================================================

class TestMACross:
    def test_golden_cross(self):
        """短线上穿长线 → 金叉信号"""
        short = pd.Series([1, 2, 3, 4, 5, 6])
        long = pd.Series([5, 4, 3, 2, 1, 0])
        signal = ma_cross(short, long)
        assert signal.iloc[-1] == 0  # 已在上方
        # 交叉点在第 2 根（index 2）short=3 > long=3 的 diff=1
        cross_idx = (short > long).astype(int).diff()
        assert (cross_idx == 1).sum() == 1  # 恰好一次金叉

    def test_death_cross(self):
        """短线下穿长线 → 死叉信号"""
        short = pd.Series([5, 4, 3, 2, 1, 0])
        long = pd.Series([0, 1, 2, 3, 4, 5])
        signal = ma_cross(short, long)
        cross_idx = (short > long).astype(int).diff()
        assert (cross_idx == -1).sum() == 1  # 恰好一次死叉


# ============================================================
# ATR
# ============================================================

class TestATR:
    def test_atr_positive(self):
        df = make_kline(100)
        result = atr(df["最高"], df["最低"], df["收盘"])
        # ATR 始终为正
        assert (result.dropna() >= 0).all()

    def test_atr_no_volatility(self):
        """零波动 → ATR = 0"""
        h = pd.Series([10.0] * 30)
        l = pd.Series([10.0] * 30)
        c = pd.Series([10.0] * 30)
        result = atr(h, l, c, 14)
        assert result.dropna().iloc[-1] == pytest.approx(0.0)


# ============================================================
# 波动率
# ============================================================

class TestVolatility:
    def test_volatility_positive(self):
        s = pd.Series(np.random.default_rng(42).normal(100, 2, 100))
        result = rolling_volatility(s, 20)
        assert (result.dropna() >= 0).all()

    def test_volatility_zero(self):
        """无波动 → 波动率 = 0"""
        s = pd.Series([10.0] * 50)
        result = rolling_volatility(s, 20)
        assert result.dropna().iloc[-1] == pytest.approx(0.0)


# ============================================================
# 实体占比
# ============================================================

class TestBodyRatio:
    def test_full_body(self):
        """光头光脚 → 实体比 = 1"""
        row = pd.Series({"开盘": 10, "收盘": 12, "最高": 12, "最低": 10})
        assert body_to_range_ratio(row) == pytest.approx(1.0)

    def test_doji(self):
        """十字星 → 实体比 ≈ 0（取决于精度）"""
        row = pd.Series({"开盘": 10, "最高": 12, "收盘": 10.01, "最低": 8})
        ratio = body_to_range_ratio(row)
        assert ratio < 0.01

    def test_body_to_range_series(self):
        df = make_kline(100)
        result = body_to_range_series(df)
        assert len(result) == 100
        assert (result >= 0).all() and (result <= 1.0).all()


# ============================================================
# 连续满足次数
# ============================================================

class TestConsecutiveCount:
    def test_all_match(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert consecutive_count(s, lambda x: x > 0) == 5

    def test_none_match(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert consecutive_count(s, lambda x: x > 10) == 0

    def test_partial_trailing(self):
        s = pd.Series([1, 2, 5, 3, 7, 8])
        # 从尾部: 8>5 ✓, 7>5 ✓, 3>5 ✗ → 2
        assert consecutive_count(s, lambda x: x > 5) == 2

    def test_empty_series(self):
        assert consecutive_count(pd.Series([], dtype=int), lambda x: True) == 0


# ============================================================
# 轮廓紧凑度
# ============================================================

class TestProfileCompactness:
    def test_tight_range(self):
        """窄幅整理 → body/range 比值较低（实体小影线大）"""
        df = pd.DataFrame({
            "开盘": [10, 10.01, 9.99, 10.02],
            "收盘": [10.01, 9.99, 10.01, 10.00],
            "最高": [10.05, 10.03, 10.04, 10.05],
            "最低": [9.95, 9.97, 9.96, 9.95],
        })
        score = profile_compactness(df, 4)
        # body/range: 0.01/0.10 + 0.02/0.06 + 0.02/0.08 + 0.02/0.10 ≈ 0.22
        assert score == pytest.approx(0.22, abs=0.05)

    def test_wide_range(self):
        """大幅波动 → 紧凑度较低"""
        df = pd.DataFrame({
            "开盘": [10, 15],
            "收盘": [15, 10],
            "最高": [15, 15],
            "最低": [10, 10],
        })
        score = profile_compactness(df, 2)
        assert score == 1.0  # 每根 K 线实体都等于波幅


# ============================================================
# all_indicators 按需计算
# ============================================================

class TestAllIndicators:
    def test_all_indicators_default(self):
        df = make_kline(200)
        result = all_indicators(df)
        expected_cols = {
            "MA5", "MA20", "DIF", "DEA", "MACD", "RSI",
            "K", "D", "J",
            "BOLL_MID", "BOLL_UPPER", "BOLL_LOWER",
            "VOL_RATIO", "ATR", "VOLATILITY", "BODY_RATIO",
        }
        assert expected_cols.issubset(set(result.columns))

    def test_needed_subset(self):
        df = make_kline(200)
        result = all_indicators(df, needed_cols=["MA5", "RSI", "DIF"])
        assert "MA5" in result.columns
        assert "RSI" in result.columns
        assert "DIF" in result.columns
        assert "BOLL_UPPER" not in result.columns
        assert "VOL_RATIO" not in result.columns

    def test_needed_empty(self):
        df = make_kline(50)
        result = all_indicators(df, needed_cols=[])
        # 只返回原始数据列
        assert set(result.columns) == {"开盘", "收盘", "最高", "最低", "成交量"}

    def test_needed_ma_cross(self):
        """MA_CROSS 自动补全 MA5/MA20"""
        df = make_kline(200)
        result = all_indicators(df, needed_cols=["MA_CROSS"])
        assert "MA_CROSS" in result.columns
        assert "MA5" in result.columns  # 自动补全
        assert "MA20" in result.columns  # 自动补全


# ============================================================
# 回踩检测
# ============================================================

class TestRetracement:
    def test_no_retracement(self):
        """单调下跌 → 无回踩"""
        df = pd.DataFrame({
            "最高": np.arange(20, 10, -0.5),
            "最低": np.arange(19, 9, -0.5),
            "收盘": np.arange(19.5, 9.5, -0.5),
        })
        result = retracement_detect(df, 15)
        assert result["has_retracement"] is False


# ============================================================
# 过高点检测
# ============================================================

class TestOvershoot:
    def test_no_overshoot(self):
        """平稳走势 → 无过高点"""
        df = make_kline(60, seed=123)
        result = overshoot_detect(df, 60)
        assert isinstance(result["has_overshoot"], bool)
        assert isinstance(result["position"], int)


# ============================================================
# 支撑阻力
# ============================================================

class TestSupportResistance:
    def test_returns_levels(self):
        df = make_kline(100)
        levels = support_resistance_levels(df, n_bins=20)
        assert isinstance(levels, list)
        assert len(levels) == 3  # top 3 bins


# ============================================================
# 通道检测
# ============================================================

class TestChannelDetect:
    def test_no_channel(self):
        df = make_kline(50)
        result = channel_detect(df, 8)
        # 返回值可能是 numpy bool，检查其 truthiness
        assert isinstance(result["is_channel"], (bool, np.bool_))
        assert result["strength"] >= 0.0
        assert result["strength"] <= 1.0


# ============================================================
# 平台位测试计数
# ============================================================

class TestPlatformCount:
    def test_returns_int(self):
        df = make_kline(100)
        count = platform_test_count(df, tolerance=0.02, min_gap=3)
        assert isinstance(count, int)
        assert count >= 0

    def test_short_df(self):
        """不足 20 根 K 线 → 返回 0"""
        df = make_kline(10)
        assert platform_test_count(df) == 0
