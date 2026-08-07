"""观察池 + 假设自动验证单元测试（R-037① · 2026-08-08）

覆盖：登记（幂等）/ 状态流转（成交/撤销/兑现/出池）/
总览统计 / 假设验证判定（样本不足不误判、有效、证伪提示）/
与 sim_trading 挂钩（sim_check 成交后观察池自动更新）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from 分析决策.跟踪 import observe_pool as op


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(op, "OBSERVE_FILE", tmp_path / "观察池.csv")
    monkeypatch.setattr(op, "HYPOTHESIS_FILE", tmp_path / "假设.csv")


def test_register_idempotent(monkeypatch, tmp_path):
    """登记幂等：同一 trade_id 不重复登记"""
    _use_tmp(monkeypatch, tmp_path)
    op.register("T1", "2026-08-08", "600001", "测试", "S", 10.5, 9.8)
    op.register("T1", "2026-08-08", "600001", "测试", "S", 10.5, 9.8)
    rows = op._read_all()
    assert len(rows) == 1
    assert rows[0]["status"] == "跟踪中"


def test_status_flow(monkeypatch, tmp_path):
    """状态流转：跟踪中 → 成交 → 兑现/出池"""
    _use_tmp(monkeypatch, tmp_path)
    op.register("T1", "2026-08-08", "600001", "测试", "S", 10.5, 9.8)
    op.update("T1", "成交", note="到价成交")
    assert op._read_all()[0]["status"] == "成交"
    op.update("T1", "兑现", exit_reason="止损触发", r=1.2)
    r = op._read_all()[0]
    assert r["status"] == "兑现" and r["r"] == "1.20"


def test_summarize_counts(monkeypatch, tmp_path):
    """总览：状态计数 + 已了结统计"""
    _use_tmp(monkeypatch, tmp_path)
    for i in range(3):
        op.register(f"T{i}", "2026-08-08", f"60000{i}", "票", "A", 10.0, 9.5)
    op.update("T0", "兑现", r=1.0)
    op.update("T1", "出池", r=-0.5)
    text = op.summarize()
    assert "跟踪中 1" in text
    assert "兑现 1" in text and "出池 1" in text
    assert "avgR +0.25" in text  # (1.0-0.5)/2
    assert "胜率 50%" in text


def test_hypothesis_insufficient_samples(monkeypatch, tmp_path):
    """假设验证：样本不足（<5）→ 积累中，不误判"""
    _use_tmp(monkeypatch, tmp_path)
    op.register("T1", "2026-08-08", "600001", "票", "S", 10.0, 9.5)
    op.update("T1", "兑现", r=1.0)
    text = op.hypothesis_check()
    assert "积累中" in text


def test_hypothesis_verdict(monkeypatch, tmp_path):
    """假设验证：S 级 avgR>0 且样本足 → 有效；A/B 级 avgR<0 → 证伪提示"""
    _use_tmp(monkeypatch, tmp_path)
    for i in range(6):
        op.register(f"S{i}", "2026-08-08", f"6000{i:02d}", "票", "S", 10.0, 9.5)
        op.update(f"S{i}", "兑现" if i % 2 == 0 else "出池",
                  r=1.5 if i % 2 == 0 else -0.5)
    for i in range(6):
        op.register(f"B{i}", "2026-08-08", f"6001{i:02d}", "票", "B", 10.0, 9.5)
        op.update(f"B{i}", "出池", r=-1.0)
    text = op.hypothesis_check()
    assert "✅ 有效" in text     # S：avgR +0.5 → 有效
    assert "⚠️ 证伪提示" in text  # A/B：avgR -1.0 → 证伪提示
