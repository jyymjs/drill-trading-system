"""策略收紧条件测试包（T-024）单元测试：统计口径 + 过滤分组逻辑"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

from 回测系统.tighten_compare import build_groups, group_stats


def make_df() -> pd.DataFrame:
    """8 笔信号：覆盖 量比甜点/巨量、动量高低、止损近/远/适中 各象限"""
    return pd.DataFrame([
        # code, date, r_20d, vol_ratio, mom20, risk
        {"code": 1, "date": "2024-01-01", "r_20d": 1.0, "vol_ratio": 1.7, "mom20": 0.05, "risk": 0.8},
        {"code": 2, "date": "2024-01-02", "r_20d": -0.5, "vol_ratio": 2.5, "mom20": 0.12, "risk": 0.4},
        {"code": 3, "date": "2024-01-03", "r_20d": 0.25, "vol_ratio": 3.0, "mom20": 0.20, "risk": 2.0},
        {"code": 4, "date": "2024-01-04", "r_20d": -0.3, "vol_ratio": 1.8, "mom20": 0.08, "risk": 0.3},
        {"code": 5, "date": "2024-01-05", "r_20d": 0.8, "vol_ratio": 1.6, "mom20": 0.09, "risk": 1.5},
        {"code": 6, "date": "2024-01-06", "r_20d": -0.2, "vol_ratio": 4.0, "mom20": 0.30, "risk": 4.0},
        {"code": 7, "date": "2024-01-07", "r_20d": 0.5, "vol_ratio": 2.2, "mom20": 0.11, "risk": 0.6},
        {"code": 8, "date": "2024-01-08", "r_20d": 0.1, "vol_ratio": 1.9, "mom20": 0.14, "risk": 2.5},
    ])


# ============================================================
# 统计口径（与 dn_confirm_compare.summarize 同口径）
# ============================================================

class TestGroupStats:
    def test_win_rate_avg_r_pf_total(self):
        """R=[1.0, -0.5, 0.25] → 胜率 2/3、avgR 0.25、盈亏比 1.25/0.5、累计R 0.75"""
        s = group_stats([1.0, -0.5, 0.25])
        assert s["n"] == 3
        assert s["win_rate"] == pytest.approx(round(2 / 3, 4))  # 脚本口径：round(x, 4)
        assert s["avg_r"] == pytest.approx(0.25)
        assert s["profit_factor"] == pytest.approx(1.25 / 0.5)
        assert s["total_r"] == pytest.approx(0.75)

    def test_all_win_no_loss_infinite_pf(self):
        """无亏损 → 盈亏比 ∞"""
        s = group_stats([0.5, 1.0])
        assert s["profit_factor"] == float("inf")
        assert s["win_rate"] == 1.0

    def test_empty(self):
        """空序列 → 全 0"""
        s = group_stats([])
        assert s == {"n": 0, "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0, "total_r": 0.0}


# ============================================================
# 过滤分组逻辑
# ============================================================

class TestBuildGroups:
    def setup_method(self):
        self.df = make_df()
        self.g = build_groups(self.df, mom=0.10)

    def test_baseline_all(self):
        assert self.g["基线(全触发)"] == self.df["r_20d"].tolist()

    def test_g1_vol_ratio(self):
        """量比甜点 (1.5, 2.0]：code 1/4/5/8 → 4 笔"""
        assert self.g["G1 量比≤2.0"] == [1.0, -0.3, 0.8, 0.1]

    def test_g3_risk_band(self):
        """止损 0.5~3.0：code 1/3/5/7/8 → 5 笔"""
        assert self.g["G3 止损0.5~3.0"] == [1.0, 0.25, 0.8, 0.5, 0.1]

    def test_g2_momentum(self):
        """动量≤10%：code 1/4/5 → 3 笔"""
        assert self.g["G2 动量≤10%"] == [1.0, -0.3, 0.8]

    def test_c23_mom_risk(self):
        """组合 动量≤10% + 止损适中：code 1/5 → 2 笔（code4 risk=0.3 被滤）"""
        assert self.g["C23 动量+止损"] == [1.0, 0.8]

    def test_c123_all(self):
        """全组合：code 1/5 → 2 笔"""
        assert self.g["C123 全组合"] == [1.0, 0.8]

    def test_detail_buckets(self):
        """细分桶：量比>2.0 三笔、止损<0.5 两笔、动量10~15% 两笔"""
        assert self.g["细-量比>2.0"] == [-0.5, 0.25, -0.2, 0.5]
        assert self.g["细-止损<0.5"] == [-0.5, -0.3]
        assert self.g["细-动量10~15%"] == [-0.5, 0.5, 0.1]
