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


def inject_limit_up(df: pd.DataFrame, idxs: list[int], pct: float = 0.10,
                    one_price: bool = False) -> pd.DataFrame:
    """把指定索引设为涨停（默认普通涨停：仅收盘/最高触线，开盘温和）

    2026-08-06 阈值定案后语义：普通涨停只计 limit_days（不跳空），
    一字涨停（one_price=True）同时计 limit+gap 两事件。
    """
    d = df.copy()
    c = d["收盘"].values.copy()
    op = d["开盘"].values.copy()
    hi = d["最高"].values.copy()
    lo = d["最低"].values.copy()
    for i in idxs:
        p = c[i - 1]
        c[i] = p * (1.0 + pct)
        if one_price:
            op[i], hi[i], lo[i] = c[i], c[i], c[i]
        else:
            op[i] = p * 1.01  # 温和高开，不触发跳空线
            hi[i] = c[i]
            lo[i] = min(lo[i], p * 0.99)
    d["收盘"], d["开盘"], d["最高"], d["最低"] = c, op, hi, lo
    return d


def inject_limit_sequential(df: pd.DataFrame, idxs: list[int],
                            pct: float = 0.10) -> pd.DataFrame:
    """顺序注入涨/跌停：注入点按前收（含前序平移）计算，次根联动消除假事件

    单测需精确控制"跳空/涨跌停"计数来源：若直接改价格，次根相对极端价会
    自然产生假跳空/假涨跌停（实盘真实事件但干扰断言）。本函数按索引顺序
    处理——注入点价格 = 当前前收 × (1+pct)（保持 1.1 关系不被平移稀释），
    注入后把次根及后续整体平移使次根收盘 = 注入点 × 0.995（微跌，不触
    9.5% 线；开盘随平移，相对前收微跌不触 4% 跳空线）。
    """
    d = df.copy()
    c = d["收盘"].values.astype(float).copy()
    op = d["开盘"].values.astype(float).copy()
    hi = d["最高"].values.astype(float).copy()
    lo = d["最低"].values.astype(float).copy()
    for i in sorted(idxs):
        p = c[i - 1]
        c[i] = p * (1.0 + pct)
        op[i] = p * 1.01  # 温和高开/低开，不触发跳空线
        hi[i] = c[i]
        if i + 1 < len(c):
            delta = c[i] * 0.995 - c[i + 1]
            if delta != 0:
                c[i + 1:] += delta
                op[i + 1:] += delta
    d["收盘"] = c
    d["开盘"] = op
    d["最高"] = hi
    d["最低"] = lo
    return d


def inject_gap(df: pd.DataFrame, idxs: list[int], pct: float = 0.04) -> pd.DataFrame:
    """把指定索引设为跳空高开（开盘跳空 pct，收盘回落不触涨跌停线）"""
    d = df.copy()
    c = d["收盘"].values.copy()
    op = d["开盘"].values.copy()
    hi = d["最高"].values.copy()
    lo = d["最低"].values.copy()
    for i in idxs:
        p = c[i - 1]
        op[i] = p * (1.0 + pct)
        hi[i] = op[i]
        c[i] = p * (1.0 + pct * 0.5)  # 收盘仅涨 pct/2，不触涨跌停线
        lo[i] = min(lo[i], c[i] * 0.98)
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
    """G1 一票否决接入 _tier0_reject（2026-08-06 定案阈值：4% 跳空 + 分板块线 + ≥5 次）"""

    def test_normal_pass(self):
        df = make_base()
        strategy = ZuanQianStrategy()
        assert strategy._tier0_reject(df) is None

    def test_5_limit_up_rejected(self):
        """普通涨停 5 次（合计 5 事件）→ 排除（定案阈值 ≥5）。

        涨跌停交替注入（5 涨停 + 5 跌停）：涨停/跌停均计 limit_days（10 事件 ≥ 5），
        交替使最新价相对 60 日低点涨幅 < 40%，避开 _tier0_reject 完全释放否决；
        涨停次日紧接跌停（0.9×1.1=0.99×前收），次根自然衔接无假跳空。"""
        df = inject_limit_up(make_base(), [62, 65, 68, 71, 74])
        df = inject_limit_up(df, [63, 66, 69, 72, 75], pct=-0.10)
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "经常跳空/涨跌停" in reason

    def test_3_limit_up_not_rejected(self):
        """普通涨停 3 次（合计 3 事件）→ 放行（3→5 收紧后不再是"经常"）"""
        df = inject_limit_sequential(make_base(), [62, 65, 68])
        strategy = ZuanQianStrategy()
        assert strategy._tier0_reject(df) is None

    def test_5_gap_rejected(self):
        """跳空高开 5 次（收盘不触线）→ 排除"""
        df = inject_gap(make_base(), [62, 65, 68, 71, 74])
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "经常跳空/涨跌停" in reason

    def test_3_gap_not_rejected(self):
        """跳空 3 次 → 放行"""
        df = inject_gap(make_base(), [62, 65, 68])
        strategy = ZuanQianStrategy()
        assert strategy._tier0_reject(df) is None

    def test_20cm_20pct_rejected(self):
        """20cm 票：5 次 19.6% 一字跌停（涨跌停+跳空双计）→ 排除（19.5% 线触发）"""
        df = inject_limit_up(make_base(), [62, 65, 68, 71, 74], pct=-0.196,
                             one_price=True)
        df.attrs["code"] = "688001"
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "经常跳空/涨跌停" in reason

    def test_prebreak_mode_also_rejected(self):
        """prebreak 模式（不卡回踩）同样触发 G1"""
        df = inject_limit_up(make_base(), [62, 65, 68, 71, 74])
        df = inject_limit_up(df, [63, 66, 69, 72, 75], pct=-0.10)
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df, check_retracement=False)
        assert reason is not None
        assert "经常跳空/涨跌停" in reason

    def test_grade_full_link_match_false(self):
        """完整 grade 链路：触发 G1 → 一票否决 C 级"""
        df = inject_limit_up(make_base(), [62, 65, 68, 71, 74])
        df = inject_limit_up(df, [63, 66, 69, 72, 75], pct=-0.10)
        strategy = ZuanQianStrategy()
        res = strategy.grade(df)
        assert res["match"] is False
        assert res["grade"] == "C"
        assert "经常跳空" in res["scores"]["Tier0"][1]

    def test_prebreak_grade_full_link_match_false(self):
        """prebreak_grade 链路：触发 G1 → 一票否决 C 级"""
        df = inject_limit_up(make_base(), [62, 65, 68, 71, 74])
        df = inject_limit_up(df, [63, 66, 69, 72, 75], pct=-0.10)
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
        df = inject_limit_up(make_base(), [len(make_base()) - 1], one_price=True)
        strategy = ZuanQianStrategy()
        reason = strategy._tier0_reject(df)
        assert reason is not None
        assert "最新日一字封板" in reason
