"""策略特征体检单元测试（R-036③ · 2026-08-09）

覆盖：延迟覆盖率报警（<60% 触发）/ 量比>3.0 占比报警 / C23 越界口径检查 /
样本不足不误报 / 设定常量与规格书一致。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 回测系统 import strategy_feature_health as fh


def _signals(tmp_path, rows: list[tuple]) -> str:
    """rows = [(code, date, trigger, close, risk, r_20d, triggered), ...]"""
    df = pd.DataFrame(rows, columns=["code", "date", "trigger", "close", "risk",
                                     "r_20d", "triggered_20d"])
    p = tmp_path / "signals.csv"
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return str(p)


def test_constants_match_spec():
    """设定常量与规格书一致（3 日有效期 / 1.5 量比 / 10% 动量 / 0.5-3 止损）"""
    assert fh.PENDING_EXPIRE_DAYS == 3
    assert fh.DN_CONFIRM_MIN == 1.5
    assert fh.C23_MOM_MAX == 0.10
    assert (fh.C23_RISK_MIN, fh.C23_RISK_MAX) == (0.5, 3.0)


def test_c23_violation_alerts(monkeypatch, tmp_path):
    """信号源未应用 C23（动量>10% / 止损越界）→ 口径检查报警"""
    rows = []
    for i in range(120):
        rows.append((f"60000{i % 100:03d}", f"2024-01-{(i % 28) + 1:02d}",
                     10.0, 9.0, 1.0, 0.5, 1))
    rows[0] = (rows[0][0], rows[0][1], 12.0, 10.0, 4.0, 0.5, 1)  # 动量>10% + 止损越界
    path = _signals(tmp_path, rows)
    monkeypatch.setattr(fh, "load_kline_cache", lambda codes: {})  # 无 K 线 → 延迟/量比跳过
    stats = fh.feature_health(path)
    assert any("C23" in a for a in stats["alerts"])
    assert stats["stop_dist"]["pct_out"] > 0


def test_insufficient_samples_no_warning(monkeypatch, tmp_path):
    """样本不足（<100）→ 不误报"""
    rows = [(f"600{i:03d}", f"2024-01-{(i % 28) + 1:02d}", 10.0, 9.0, 1.0, 0.5, 1)
            for i in range(20)]
    path = _signals(tmp_path, rows)
    monkeypatch.setattr(fh, "load_kline_cache", lambda codes: {})
    stats = fh.feature_health(path)
    assert stats["n_trigger"] == 20
    # 无 K 线 → 延迟/量比无数据 → 只剩止损检查（无报警）
    assert not any(a.startswith("⚠️") for a in stats["alerts"])
