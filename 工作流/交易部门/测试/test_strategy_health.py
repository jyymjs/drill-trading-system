"""策略三方体检单元测试（R-036② · 2026-08-08）

覆盖：回测基准从 signals.csv 计算（prebreak+触发+r_20d）/
模拟线从 sim_journal 已平仓行读取（不受 pending 污染）/
实盘线从 r_curve note=live 读取 / 判定规则（样本不足不误报、
累计 R < P5 预警、连败预警、avgR 偏差观察）。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from 分析决策.跟踪 import strategy_health as sh


def _signals_csv(tmp_path, rows: list[tuple]) -> str:
    """rows = [(mode, triggered_20d, r_20d), ...]"""
    df = pd.DataFrame(rows, columns=["mode", "triggered_20d", "r_20d"])
    p = tmp_path / "signals.csv"
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return str(p)


def _sim_rows(rs: list[float], tmp_path) -> str:
    """构造 sim_journal（含 pending + closed），返回 SIM_FILE 路径"""
    import csv
    cols = sh.sim_trading.SIM_COLUMNS
    path = tmp_path / "sim_journal.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for i, r in enumerate(rs):
            w.writerow({"trade_id": f"T{i}", "date": "2026-01-01",
                        "symbol": "600001", "name": "测试", "direction": "long",
                        "market": "stock", "entry_price": "10", "stop_loss": "9",
                        "volume": "100", "grade_at_entry": "S",
                        "ty_high": "0", "ty_low": "0",
                        "status": "closed", "exit_price": "9.5",
                        "exit_date": "2026-01-05", "exit_reason": "止损触发(层面1)",
                        "r_multiple": f"{r}", "pnl": "0", "env_scale": "1.0",
                        "phase": "", "created_date": ""})
        # pending 行：不参与统计
        w.writerow({"trade_id": "P0", "date": "2026-01-02", "symbol": "600002",
                    "name": "挂单", "direction": "long", "market": "stock",
                    "entry_price": "11", "stop_loss": "10", "volume": "100",
                    "grade_at_entry": "A", "ty_high": "0", "ty_low": "0",
                    "status": "pending", "exit_price": "", "exit_date": "",
                    "exit_reason": "", "r_multiple": "", "pnl": "",
                    "env_scale": "1.0", "phase": "", "created_date": "2026-01-02"})
    return str(path)


def test_backtest_baseline_stats(monkeypatch, tmp_path):
    """回测基准 = prebreak+触发行 r_20d 分布（normal/未触发行不计入）"""
    csv_path = _signals_csv(tmp_path, [
        ("prebreak", "1", "1.0"), ("prebreak", "1", "-0.5"),
        ("prebreak", "0", "5.0"),   # 未触发 → 不计
        ("normal", "1", "2.0"),     # 非 prebreak → 不计
        ("prebreak", "1", "0.5"),
    ])
    base = sh.load_backtest_baseline(csv_path)
    assert base["n"] == 3
    assert abs(base["avg_r"] - 0.333) < 0.01  # (1.0-0.5+0.5)/3
    assert base["winrate"] == 2 / 3 * 100


def test_sim_line_only_closed(monkeypatch, tmp_path):
    """模拟线只统计 closed 行（pending 不污染）"""
    monkeypatch.setattr(sh.sim_trading, "SIM_FILE", Path(_sim_rows([1.5, -0.5], tmp_path)))
    sim = sh.load_sim_line()
    assert sim["n"] == 2
    assert abs(sim["avg_r"] - 0.5) < 0.01


def test_insufficient_samples_no_false_alarm(monkeypatch, tmp_path):
    """样本不足（<5）→ 提示积累中，不误报预警"""
    monkeypatch.setattr(sh.sim_trading, "SIM_FILE", Path(_sim_rows([1.0], tmp_path)))
    csv_path = _signals_csv(tmp_path, [("prebreak", "1", "0.5")] * 20)
    base = sh.load_backtest_baseline(csv_path)
    sim = sh.load_sim_line()
    alerts = sh._judge(sim, {"n": 0}, base)
    assert any("样本不足" in a for a in alerts)
    assert not any(a.startswith("⚠️") or a.startswith("👀") for a in alerts)


def test_cum_r_below_p5_triggers_alert(monkeypatch, tmp_path):
    """模拟线累计 R < 回测 P5 → 预警触发排查"""
    monkeypatch.setattr(sh.sim_trading, "SIM_FILE",
                        Path(_sim_rows([-2.0] * 8, tmp_path)))
    csv_path = _signals_csv(tmp_path, [("prebreak", "1", "0.5")] * 50)
    base = sh.load_backtest_baseline(csv_path)
    sim = sh.load_sim_line()
    assert sim["n"] >= sh.MIN_SAMPLES
    alerts = sh._judge(sim, {"n": 0}, base)
    assert any("累计 R" in a and "触发排查" in a for a in alerts)


def test_max_streak_warn(monkeypatch, tmp_path):
    """连败 ≥12 → 预警"""
    monkeypatch.setattr(sh.sim_trading, "SIM_FILE",
                        Path(_sim_rows([-0.5] * sh.MAX_STREAK_WARN, tmp_path)))
    csv_path = _signals_csv(tmp_path, [("prebreak", "1", "0.5")] * 50)
    base = sh.load_backtest_baseline(csv_path)
    sim = sh.load_sim_line()
    alerts = sh._judge(sim, {"n": 0}, base)
    assert any("连败" in a and "P99" in a for a in alerts)


def test_health_report_runs(tmp_path, monkeypatch):
    """报告整体可运行（基准缺失/无数据时优雅降级）"""
    monkeypatch.setattr(sh, "DEFAULT_SIGNALS", tmp_path / "none.csv")
    text = sh.health_report()
    assert "回测预期" in text and "判定" in text
