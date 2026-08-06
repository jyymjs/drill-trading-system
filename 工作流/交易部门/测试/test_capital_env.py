"""G3 0.5R 环境仓位接线测试（补完计划 · 2026-08-06）

知识库出处：经验型模式/知识卡.md 仓位与环境
「环境好（非右下角）→ 正常 1R；环境不好（右下角）→ 0.5R」（2024-06-22/29）

接入链路：indicators.environment_quality（个股 60 日窗口右下角特征判定）
→ capital.max_risk_per_trade(scale) → sim_trading.check_affordability /
sim_open 自动判定。与 B1 环境闸门（gate.py，大盘指数执行层否决/降级）
维度不同：B1 管大盘"做不做"，G3 管个股环境"做多少"。
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 分析决策.跟踪 import sim_trading
from 分析决策.风控 import capital


def make_df(quality: str) -> pd.DataFrame:
    """构造指定环境质量的 K 线 df（60+ 根）"""
    rng = np.random.default_rng(5)
    n = 80
    if quality == "good":
        close = 20 + np.arange(n) * 0.05 + rng.normal(0, 0.05, n).cumsum()
    else:  # weak/bad：后半段低点下移 + 反弹弱
        base = 20 + np.arange(n) * 0.02
        down = np.concatenate([np.zeros(n // 2), np.linspace(0, -3.0, n - n // 2)])
        close = base + down + rng.normal(0, 0.1, n).cumsum() * 0.2
    return pd.DataFrame({
        "开盘": close - 0.05, "收盘": close,
        "最高": close + 0.2, "最低": close - 0.2,
    })


class TestMaxRiskScale:
    def test_default_1r(self, monkeypatch):
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        assert capital.max_risk_per_trade() == pytest.approx(84.0)

    def test_half_risk(self, monkeypatch):
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        assert capital.max_risk_per_trade(scale=0.5) == pytest.approx(42.0)

    def test_scale_default_compat(self, monkeypatch):
        """默认 1.0 与旧调用兼容"""
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        assert capital.max_risk_per_trade(scale=1.0) == capital.max_risk_per_trade()


class TestCheckAffordabilityScale:
    def test_scale_halves_shares(self, monkeypatch):
        """0.5R 缩放 → 风险额减半 → 手数减半"""
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        # 每股风险 0.42 元：1R → 84/0.42 = 200 股（2 手）
        #                   0.5R → 42/0.42 = 100 股（1 手）
        s1, _ = sim_trading.check_affordability(price=10.0, risk_per_share=0.42, risk_scale=1.0)
        s05, _ = sim_trading.check_affordability(price=10.0, risk_per_share=0.42, risk_scale=0.5)
        assert s1 == 200
        assert s05 == 100


class TestEnvRiskScale:
    """_env_risk_scale：环境质量 → 缩放系数"""

    @pytest.mark.parametrize("quality,expect_scale", [
        ("good", 1.0),
        ("weak", 0.5),
        ("bad", 0.5),
    ])
    def test_quality_mapping(self, monkeypatch, quality, expect_scale):
        df = make_df(quality)

        def fake_kline(code, use_cache=True):
            return df

        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline", fake_kline)
        scale, _note = sim_trading._env_risk_scale("000001")
        assert scale == expect_scale
        assert ("0.5R" if expect_scale == 0.5 else "1R") in _note

    def test_missing_data_default_1r(self, monkeypatch):
        def fake_empty(code, use_cache=True):
            return None

        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline", fake_empty)
        scale, _note = sim_trading._env_risk_scale("000001")
        assert scale == 1.0

    def test_exception_default_1r(self, monkeypatch):
        def fake_error(code, use_cache=True):
            raise RuntimeError("网络故障")

        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline", fake_error)
        scale, _note = sim_trading._env_risk_scale("000001")
        assert scale == 1.0


class TestSimOpenEnvScale:
    """sim_open 自动环境判定（G3）"""

    def test_weak_env_halves_risk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sim_trading, "JOURNAL_DIR", tmp_path)
        monkeypatch.setattr(sim_trading, "SIM_FILE", tmp_path / "sim_journal.csv")
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: make_df("bad"))
        out = sim_trading.sim_open("000001", price=10.0, stop=9.79, name="测试")
        assert "0.5R" in out
        assert "上限42" in out

    def test_good_env_full_risk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sim_trading, "JOURNAL_DIR", tmp_path)
        monkeypatch.setattr(sim_trading, "SIM_FILE", tmp_path / "sim_journal.csv")
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: make_df("good"))
        out = sim_trading.sim_open("000001", price=10.0, stop=9.79, name="测试")
        assert "1R" in out
        assert "上限84" in out

    def test_manual_scale_override(self, monkeypatch, tmp_path):
        """手动指定 risk_scale 优先于自动判定"""
        monkeypatch.setattr(sim_trading, "JOURNAL_DIR", tmp_path)
        monkeypatch.setattr(sim_trading, "SIM_FILE", tmp_path / "sim_journal.csv")
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        out = sim_trading.sim_open("000001", price=10.0, stop=9.79, name="测试",
                                   risk_scale=0.5)
        assert "手动缩放0.5" in out
        assert "上限42" in out
