"""G1/G2 一票否决接入测试（补完计划 · 2026-08-06）

知识库出处：
- G1 经常跳空/涨跌停：品种筛选/知识卡.md 一票否决#4「经常跳空/涨跌停品种——连续性不好」
- G2 一字形排列：品种筛选/知识卡.md 一票否决#5「一字形排列（调整全是一字形）」

接入位置：ZuanQianStrategy._tier0_reject（normal 与 prebreak 两模式共享入口，
scanner 扫描与回测引擎均经 grade/prebreak_grade 自动生效）。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy


def make_base(n: int = 80, seed: int = 3) -> pd.DataFrame:
    """温和随机游走 K 线（含成交量，可跑完整 grade 链路）"""
    rng = np.random.default_rng(seed)
    close = 20 + np.arange(n) * 0.02 + rng.normal(0, 0.1, n).cumsum()
    vol = rng.integers(10000, 100000, n)
    return pd.DataFrame({
        "日期": pd.bdate_range("2025-01-01", periods=n),
        "开盘": close - 0.02,
        "收盘": close,
        "最高": close + 0.10,
        "最低": close - 0.10,
        "成交量": vol,
    })


def inject_limit_up(df: pd.DataFrame, idxs: list[int]) -> pd.DataFrame:
    """把指定索引设为一字涨停（开=高=低=收=前收×1.10）"""
    d = df.copy()
    c = d["收盘"].values.copy()
    op = d["开盘"].values.copy()
    hi = d["最高"].values.copy()
    lo = d["最低"].values.copy()
    for i in idxs:
        p = c[i - 1]
        c[i] = p * 1.10
        op[i], hi[i], lo[i] = c[i], c[i], c[i]
    d["收盘"], d["开盘"], d["最高"], d["最低"] = c, op, hi, lo
    return d


def inject_one_line(df: pd.DataFrame, idxs: list[int]) -> pd.DataFrame:
    """把指定索引设为一字形（振幅<0.1% 且实体<0.1%）"""
    d = df.copy()
    c = d["收盘"].values.copy()
    op = d["开盘"].values.copy()
    hi = d["最高"].values.copy()
    lo = d["最低"].values.copy()
    for i in idxs:
        p = c[i - 1]
        c[i], op[i] = p, p
        hi[i], lo[i] = p * 1.0002, p * 0.9998
    d["收盘"], d["开盘"], d["最高"], d["最低"] = c, op, hi, lo
    return d


class TestTier0GapLimit:
    """G1 一票否决接入 _tier0_reject"""

    def test_normal_pass(self):
        df = make_base()
        strategy = ZuanQianStrategy()
        assert strategy._tier0_reject(df) is None

    def test_frequent_limit_up_rejected(self):
        df = inject_limit_up(make_base(), [70, 73, 76])
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "经常跳空/涨跌停" in reason

    def test_prebreak_mode_also_rejected(self):
        """prebreak 模式（不卡回踩）同样触发 G1"""
        df = inject_limit_up(make_base(), [70, 73, 76])
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df, check_retracement=False)
        assert reason is not None
        assert "经常跳空/涨跌停" in reason

    def test_grade_full_link_match_false(self):
        """完整 grade 链路：触发 G1 → 一票否决 C 级"""
        df = inject_limit_up(make_base(), [70, 73, 76])
        strategy = ZuanQianStrategy()
        res = strategy.grade(df)
        assert res["match"] is False
        assert res["grade"] == "C"
        assert "经常跳空" in res["scores"]["Tier0"][1]

    def test_prebreak_grade_full_link_match_false(self):
        """prebreak_grade 链路：触发 G1 → 一票否决 C 级"""
        df = inject_limit_up(make_base(), [70, 73, 76])
        strategy = ZuanQianStrategy()
        res = strategy.prebreak_grade(df)
        assert res["match"] is False
        assert res["grade"] == "C"


class TestTier0OneLine:
    """G2 一票否决接入 _tier0_reject"""

    def test_one_line_streak_rejected(self):
        df = inject_one_line(make_base(), [50, 55, 60, 65])
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "一字形排列" in reason

    def test_grade_full_link_match_false(self):
        df = inject_one_line(make_base(), [50, 55, 60, 65])
        strategy = ZuanQianStrategy()
        res = strategy.grade(df)
        assert res["match"] is False
        assert res["grade"] == "C"
        assert "一字形" in res["scores"]["Tier0"][1]

    def test_prebreak_grade_full_link_match_false(self):
        df = inject_one_line(make_base(), [50, 55, 60, 65])
        strategy = ZuanQianStrategy()
        res = strategy.prebreak_grade(df)
        assert res["match"] is False
        assert res["grade"] == "C"


class TestTier0LatestBlock:
    """G1 当日事件：最新日一字封板 → 一票否决"""

    def test_latest_limit_up_rejected(self):
        df = inject_limit_up(make_base(), [len(make_base()) - 1])
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "最新日一字封板" in reason
