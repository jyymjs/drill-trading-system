"""蒙特卡洛输出标准（复刻级版式）单测——2026-08-06 老板拍板双标准"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

import numpy as np
import pytest

from 分析决策.跟踪.monte_carlo import simulate
from 回测系统.monte_carlo_style import (LINE, W, render_scenario_report,
                                          _money, _pct, _x)


@pytest.fixture(scope="module")
def mc():
    rng = np.random.default_rng(7)
    rs = rng.normal(0.4, 1.2, 80).tolist()
    return simulate([{"r_multiple": r} for r in rs], n_simulations=2000), rs


def test_title_format(mc):
    """标题 = SIMULATION REPORT: Middle 100.0% (N Scenarios)"""
    text = render_scenario_report(mc[0], rs=mc[1])
    assert "SIMULATION REPORT: Middle 100.0%" in text
    assert "(2000 Scenarios)" in text


def test_column_alignment(mc):
    """竖线对齐：所有数据行第一列竖线前的【显示宽度】一致（列宽 6:2.5:1.5）"""
    from 分析决策.跟踪.monte_carlo import _disp_w
    text = render_scenario_report(mc[0], rs=mc[1])
    for line in text.splitlines():
        if "|" in line:
            idxs = [i for i, c in enumerate(line) if c == "|"]
            assert len(idxs) == 2, f"竖线数量异常: {line!r}"
            # 第一处竖线前显示宽 = 2 前导 + 38 名称列 + 1 空格 = 41
            assert _disp_w(line[:idxs[0]]) == 41, f"列错位: {line!r}"


def test_sections_order(mc):
    """5 标准板块顺序固定 + 扩展板块可剥离"""
    text = render_scenario_report(mc[0], rs=mc[1], extended=True)
    order = ["CONFIGURATION", "EQUITY PERFORMANCE", "RISK PROFILE",
             "DRAWDOWN DEPTH", "STREAKS", "EXT: TAIL STABILITY"]
    pos = [text.index(s) for s in order]
    assert pos == sorted(pos), "板块顺序错误"
    # 剥离扩展：extended=False 无 EXT: 板块
    pure = render_scenario_report(mc[0], rs=mc[1], extended=False)
    assert "EXT:" not in pure
    assert "STREAKS" in pure


def test_numeric_format(mc):
    """数值格式：金额千分位 2 位 / 百分比 1 位带符号 / 次数 x 后缀"""
    assert _money(5600) == "5,600.00"
    assert _money(1234567.891) == "1,234,567.89"
    assert _pct(15.12) == "+15.1%"
    assert _pct(-23.24) == "-23.2%"
    assert _x(4.3) == "4.3 x"
    assert _x(10.0) == "10 x"


def test_ruin_threshold(mc):
    """破产线 = 初始 × 25%（默认）"""
    text = render_scenario_report(mc[0], rs=mc[1])
    assert "Ruin Threshold（破产线）" in text
    assert "1,400.00" in text          # 5600 × 25%
    assert "< 25%" in text


def test_quantiles_use_final_equities(mc):
    """QUANTILES 用终值分位（final_equities）——P50 金额 == EQUITY Median（自洽）"""
    import re
    result, rs = mc
    text = render_scenario_report(result, rs=rs)
    median_line = [l for l in text.splitlines()
                   if "Median Final Equity（中位权益）" in l][0]
    median_val = float(re.search(r"[\d,]+\.\d+", median_line).group()
                       .replace(",", ""))
    p50_line = [l for l in text.splitlines() if "P50（50% 分位）" in l][0]
    p50_val = float(re.search(r"[\d,]+\.\d+", p50_line).group()
                    .replace(",", ""))
    assert abs(median_val - p50_val) < 0.01, \
        f"P50({p50_val}) != Median({median_val})——QUANTILES 未用终值分位"


def test_render_error_path():
    """error 结果 → SIMULATION ERROR 文本"""
    assert render_scenario_report({"error": "无交易记录"}).startswith(
        "SIMULATION ERROR")


def test_line_width(mc):
    """全宽短横线 = W"""
    assert len(LINE) == W
    text = render_scenario_report(mc[0], rs=mc[1])
    assert LINE in text
