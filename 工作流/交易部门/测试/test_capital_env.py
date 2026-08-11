"""G3 0.5R 环境仓位接线测试（补完计划 · 2026-08-06）

知识库出处：经验型模式/知识卡.md 仓位与环境
「环境好（非右下角）→ 正常 1R；环境不好（右下角）→ 0.5R」（2024-06-22/29）
「同一市场环境头寸统一：不混合 1R 和 0.5R」（2024-06-01）

接入链路（第二批定案 2026-08-06）：
indicators.environment_quality（60 日窗口右下角特征判定，个股/指数同源）
→ 当日市场环境档（上证指数 60 日窗口，_market_env_scale）
→ capital.max_risk_per_trade(scale) → sim_trading.check_affordability /
sim_open 自动判定（当日头寸统一：同日不混合 1R/0.5R，journal env_scale 列锚定）。
与 B1 环境闸门（gate.py，大盘指数执行层否决/降级）维度不同：
B1 管大盘"做不做"，G3 管当日市场环境"做多少"（统一档）。
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


@pytest.fixture(autouse=True)
def _clean_day_cache():
    """每个测试前清空当日档进程缓存，保证判定链从零开始"""
    sim_trading._day_env_cache.clear()
    yield
    sim_trading._day_env_cache.clear()


class TestMaxRiskScale:
    def test_default_1r(self, monkeypatch):
        # R-050（2026-08-11）：比例 0.025（5600 × 0.025 = 140）
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        assert capital.max_risk_per_trade() == pytest.approx(140.0)
        assert capital.RISK_RATIO == 0.025

    def test_half_risk(self, monkeypatch):
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        assert capital.max_risk_per_trade(scale=0.5) == pytest.approx(70.0)

    def test_scale_default_compat(self, monkeypatch):
        """默认 1.0 与旧调用兼容"""
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        assert capital.max_risk_per_trade(scale=1.0) == capital.max_risk_per_trade()


class TestCheckAffordabilityScale:
    def test_scale_halves_shares(self, monkeypatch):
        """0.5R 缩放 → 风险额减半 → 手数减半"""
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        # R-050（实盘资金 8401.26）：每股风险 0.42 元：1R → 210/0.42 = 500 → 500 股
        #                                           0.5R → 105/0.42 = 250 → 200 股
        s1, _ = sim_trading.check_affordability(price=10.0, risk_per_share=0.42, risk_scale=1.0)
        s05, _ = sim_trading.check_affordability(price=10.0, risk_per_share=0.42, risk_scale=0.5)
        assert s1 == 500
        assert s05 == 200


class TestEnvRiskScale:
    """_env_risk_scale：当日市场环境档 → 缩放系数（指数不可得 → 回退个股）"""

    @pytest.mark.parametrize("quality,expect_scale", [
        ("good", 1.0),
        ("weak", 0.5),
        ("bad", 0.5),
    ])
    def test_quality_mapping(self, monkeypatch, quality, expect_scale):
        df = make_df(quality)

        def fake_kline(code, use_cache=True):
            return df

        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: None)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline", fake_kline)
        scale, _note = sim_trading._env_risk_scale("000001")
        assert scale == expect_scale
        assert ("0.5R" if expect_scale == 0.5 else "1R") in _note

    def test_market_env_weak_unifies(self, monkeypatch):
        """指数环境弱（当日市场环境统一）：不查个股，直接 0.5R"""
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: 0.5)
        scale, note = sim_trading._env_risk_scale("000001")
        assert scale == 0.5
        assert "当日市场环境统一" in note

    def test_market_env_good_unifies(self, monkeypatch):
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: 1.0)
        scale, note = sim_trading._env_risk_scale("000001")
        assert scale == 1.0
        assert "当日市场环境统一" in note

    def test_day_cache_reused(self, monkeypatch):
        """当日档进程内缓存：同日内第二次判定不重复走数据链"""
        calls = []
        monkeypatch.setattr(sim_trading, "_market_env_scale",
                            lambda: calls.append(1) or 0.5)
        sim_trading._env_risk_scale("000001")
        scale2, note2 = sim_trading._env_risk_scale("000002")
        assert scale2 == 0.5
        assert "统一" in note2
        assert len(calls) == 1  # 第二次走缓存

    def test_missing_data_default_1r(self, monkeypatch):
        def fake_empty(code, use_cache=True):
            return None

        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: None)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline", fake_empty)
        scale, _note = sim_trading._env_risk_scale("000001")
        assert scale == 1.0

    def test_exception_default_1r(self, monkeypatch):
        def fake_error(code, use_cache=True):
            raise RuntimeError("网络故障")

        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: None)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline", fake_error)
        scale, _note = sim_trading._env_risk_scale("000001")
        assert scale == 1.0


class TestSimOpenDayUnified:
    """sim_open 当日头寸统一（G3 · 2026-08-06 定案）"""

    def _open(self, monkeypatch, tmp_path, code, df, price=10.0, stop=9.79,
              risk_scale=None):
        monkeypatch.setattr(sim_trading, "JOURNAL_DIR", tmp_path)
        monkeypatch.setattr(sim_trading, "SIM_FILE", tmp_path / "sim_journal.csv")
        monkeypatch.setattr(capital, "get_capital", lambda: 5600)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda c, use_cache=True: df)
        return sim_trading.sim_open(code, price=price, stop=stop, name=code,
                                    risk_scale=risk_scale)

    def test_weak_market_env_halves_risk(self, monkeypatch, tmp_path):
        """当日市场环境弱（指数统一 0.5R）→ 即使个股环境好也按 0.5R"""
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: 0.5)
        out = self._open(monkeypatch, tmp_path, "000001", make_df("good"))
        assert "0.5R" in out
        assert "上限1250" in out  # 模拟线 10 万 × 0.025 × 0.5R（V4）

    def test_good_market_env_full_risk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: 1.0)
        out = self._open(monkeypatch, tmp_path, "000001", make_df("bad"))
        assert "1R" in out
        assert "上限2500" in out  # 模拟线 10 万 × 0.025 × 1R（V4）

    def test_day_journal_unifies_second_open(self, monkeypatch, tmp_path):
        """当日头寸统一：第一笔 0.5R（个股环境差）→ 第二笔（个股环境好）
        沿用当日档 0.5R，不再混合 1R/0.5R（2024-06-01 知识卡）"""
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: None)
        out1 = self._open(monkeypatch, tmp_path, "000001", make_df("bad"))
        assert "0.5R" in out1
        out2 = self._open(monkeypatch, tmp_path, "000002", make_df("good"))
        assert "0.5R" in out2
        assert "当日统一沿用0.5R" in out2

    def test_day_journal_1r_then_good_stays_1r(self, monkeypatch, tmp_path):
        """第一笔 1R（个股环境好）→ 第二笔沿用 1R"""
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: None)
        out1 = self._open(monkeypatch, tmp_path, "000001", make_df("good"))
        assert "1R" in out1
        out2 = self._open(monkeypatch, tmp_path, "000002", make_df("good"))
        assert "1R" in out2
        assert "当日统一沿用1R" in out2

    def test_manual_scale_override(self, monkeypatch, tmp_path):
        """手动指定 risk_scale 优先于自动判定"""
        out = self._open(monkeypatch, tmp_path, "000001", make_df("good"),
                         risk_scale=0.5)
        assert "手动缩放0.5" in out
        assert "上限1250" in out  # 模拟线 10 万 × 0.025 × 0.5R（V4）

    def test_manual_scale_sets_day_unified(self, monkeypatch, tmp_path):
        """手动 0.5R 写入当日统一锚：后续自动开仓沿用 0.5R（当日出现降档信号
        → 保持全是 0.5R，2024-06-01 风控纪律：不混合）"""
        monkeypatch.setattr(sim_trading, "_market_env_scale", lambda: None)
        out1 = self._open(monkeypatch, tmp_path, "000001", make_df("good"),
                          risk_scale=0.5)
        assert "手动缩放0.5" in out1
        out2 = self._open(monkeypatch, tmp_path, "000002", make_df("good"))
        assert "0.5R" in out2
        assert "当日统一沿用0.5R" in out2
