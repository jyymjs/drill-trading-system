"""统一可视化 dashboard 单测——2026-08-07 老板指令全图表化"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

import numpy as np
import pytest

from 分析决策.可视化 import dashboard as db
from 分析决策.跟踪.monte_carlo import simulate


@pytest.fixture()
def tmp_chart_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "CHART_DIR", tmp_path)
    return tmp_path


def test_theme_constants():
    """统一深色主题常量就位"""
    assert db.BG == "#121212"
    assert db.TEXT == "#e8e8e8"
    assert db.CHART_DIR.name == "图表"


def test_plot_r_buckets_smoke(tmp_chart_dir):
    """R 档位分布图生成（数据驱动）"""
    import pandas as pd
    df = pd.DataFrame({"r_20d": [1.0, -0.5, 2.5, 6.0, 0.3, -1.2, 11.0]})
    path = db.plot_r_buckets(df)
    assert path and Path(path).exists()
    assert Path(path).stat().st_size > 1000


def test_plot_tail_stability_smoke(tmp_chart_dir):
    """去尾稳定性图生成"""
    import pandas as pd
    rng = np.random.default_rng(7)
    df = pd.DataFrame({"r_20d": rng.normal(0.4, 1.2, 80).tolist()})
    path = db.plot_tail_stability(df)
    assert path and Path(path).exists()


def test_mc_histograms(tmp_chart_dir):
    """蒙卡分布三图生成（终值/回撤/连败）"""
    rng = np.random.default_rng(7)
    rs = rng.normal(0.4, 1.2, 60).tolist()
    mc = simulate([{"r_multiple": r} for r in rs], n_simulations=2000)
    p1 = db.plot_mc_final_equities(mc, 80.0)
    p2 = db.plot_mc_drawdowns(mc, 80.0)
    p3 = db.plot_mc_streaks(mc)
    assert all(p and Path(p).exists() for p in (p1, p2, p3))


def test_live_group_no_crash(tmp_chart_dir, monkeypatch):
    """实盘组：账本空 → 不崩溃（返回空或图）"""
    # 账本指向空临时目录（无 CSV → get_records 建空表）
    monkeypatch.setattr(db, "_ROOT", tmp_chart_dir.parent)
    out = db.plot_live_group()
    assert isinstance(out, list)


def test_render_overview():
    """总览报告含三组清单"""
    report = db.render_overview(["a.png", "b.png"], ["c.png"])
    assert "回测图表" in report
    assert "蒙特卡洛图表" in report
    assert "实盘图表" in report
    assert "a.png" in report and "c.png" in report
