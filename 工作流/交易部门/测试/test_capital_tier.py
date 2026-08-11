"""R-050 资金配置单元测试（2026-08-11 老板拍板：阶梯比例 + 无限制上限）

覆盖：阶梯比例（<2 万 0.025 / ≥2 万 0.012855 封顶、单向降档）/ apply_inject
注入登记 / 资金回落不自动降风险额 / max_risk_per_trade 环境缩放。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 分析决策.风控 import capital as cap


def _tmp_file(tmp_path, data: dict):
    p = tmp_path / "capital.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestRiskRatioRule:
    def test_default_ratio_is_r050(self, monkeypatch, tmp_path):
        """缺省档位 0.025（8401 < 2 万初始档）"""
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26}))
        assert cap.get_risk_ratio() == 0.025

    def test_risk_amount_at_8401(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26, "risk_ratio": 0.025}))
        assert cap.max_risk_per_trade() == pytest.approx(210.03)  # 8401.26×0.025

    def test_risk_amount_at_16k(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 16000.0, "risk_ratio": 0.025}))
        assert cap.max_risk_per_trade() == 400.0  # 16000×0.025（<2 万档）

    def test_risk_amount_at_100k(self, monkeypatch, tmp_path):
        """10 万统一 0.025（R-050 定案，≥2 万降档暂不采纳）"""
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 100000.0, "risk_ratio": 0.025}))
        assert cap.max_risk_per_trade() == 2500.0  # 100000×0.025

    def test_half_scale(self, monkeypatch, tmp_path):
        """0.5R 日：风险额减半（环境弱档）"""
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26, "risk_ratio": 0.025}))
        assert cap.max_risk_per_trade(scale=0.5) == pytest.approx(105.02)  # 8401.26×0.025×0.5


class TestApplyInject:
    def test_inject_keeps_tier(self, monkeypatch, tmp_path):
        """注入未过 2 万：保持现档位 0.025，风险额 = 0.025×新资金"""
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26, "risk_ratio": 0.025}))
        r = cap.apply_inject(5000.0)
        assert r["capital"] == 13401.26
        assert r["risk_ratio"] == 0.025
        assert r["risk_amt"] == pytest.approx(335.03)  # 13401.26×0.025
        # 落盘核对
        data = json.loads(cap.CAPITAL_FILE.read_text(encoding="utf-8"))
        assert data["capital"] == 13401.26

    def test_inject_over_20k_keeps_ratio(self, monkeypatch, tmp_path):
        """注入过 2 万不降档（R-050 老板暂不采纳 ≥2 万降档，统一 0.025）"""
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26, "risk_ratio": 0.025}))
        r = cap.apply_inject(15000.0)
        assert r["capital"] == 23401.26
        assert r["risk_ratio"] == 0.025
        assert r["risk_amt"] == pytest.approx(585.03)  # 23401.26×0.025
        # 资金回落保持 0.025
        cap.set_capital(19000.0)
        assert cap.get_risk_ratio() == 0.025

    def test_inject_zero_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26, "risk_ratio": 0.012855}))
        try:
            cap.apply_inject(0)
            assert False, "应拒绝非正注入"
        except ValueError:
            pass

    def test_no_auto_downgrade_on_loss(self, monkeypatch, tmp_path):
        """资金回落不自动降风险额：set_capital 只改资金，风险比例保持"""
        monkeypatch.setattr(cap, "CAPITAL_FILE", _tmp_file(tmp_path, {"capital": 8401.26, "risk_ratio": 0.025}))
        cap.set_capital(7500.0)  # 净值回落
        data = json.loads(cap.CAPITAL_FILE.read_text(encoding="utf-8"))
        assert data["capital"] == 7500.0
        assert data["risk_ratio"] == 0.025  # 比例规则不变（连续）
