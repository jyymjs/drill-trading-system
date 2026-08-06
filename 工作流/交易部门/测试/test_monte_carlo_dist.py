"""蒙特卡洛分布诊断单元测试：偏度峰度/去尾稳定性/档位分布/区间胜率/连亏直方图/分段归属"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 回测系统.monte_carlo_dist import (
    _trades_by_regime,
    bucket_final_equities,
    final_quantiles,
    profit_r_thresholds,
    r_bucket_dist,
    r_stats,
    segment_simulate,
    streak_histogram,
    tail_stability,
)

# ── r_stats：偏度/峰度/最大赢家依赖度 ──


def test_r_stats_symmetric_distribution():
    """对称分布：偏度≈0（正态近 0）；均匀分布峰度≈-1.2（负峰=平坦）"""
    rng = np.random.default_rng(7)
    rs = rng.normal(0.2, 1.0, 2000).tolist()
    st = r_stats(rs)
    assert st["n"] == 2000
    assert abs(st["skew"]) < 0.2
    assert abs(st["avg_r"] - 0.2) < 0.1
    assert 0.4 < st["win_rate"] < 0.6


def test_r_stats_right_skew():
    """右偏分布：偏度显著为正（大赢家拖尾）"""
    rs = [-1.0, -0.9, -0.8, -0.7, -0.6, -0.5, 0.1, 0.2, 0.3, 5.0]
    st = r_stats(rs)
    assert st["skew"] > 0.5


def test_r_stats_max_share():
    """最大单笔占总收益比（依赖度）"""
    rs = [1.0, 1.0, 1.0, -1.0, 1.0, 1.0, 20.0]
    st = r_stats(rs)
    # 累计 = 1+1+1-1+1+1+20 = 24；max=20 → share 20/24
    assert abs(st["max_r_share"] - 20.0 / 24.0) < 1e-9


# ── tail_stability：去尾稳定性 ──


def test_tail_stability_counts_and_values():
    """去尾笔数（1% of 100 = 1 笔）与重算 avgR 正确"""
    rs = list(range(1, 101))            # 1..100，去尾去掉最大收益那部分
    rows = tail_stability(rs, (0.01, 0.05, 0.10))
    assert len(rows) == 4               # 基准 + 3 档
    assert rows[0]["pct"] == 0.0
    assert rows[1]["n_trim"] == 1
    assert rows[1]["n_keep"] == 99
    # 去尾 1%：去掉 100 → 保留 1..99，均值 = 50
    assert abs(rows[1]["avg_r"] - 50.0) < 1e-9
    # 去尾 5%：去掉 96..100 → 保留 1..95，均值 = 48
    assert abs(rows[2]["avg_r"] - 48.0) < 1e-9
    # 去尾 10%：去掉 91..100 → 保留 1..90，均值 = 45.5
    assert abs(rows[3]["avg_r"] - 45.5) < 1e-9


def test_tail_stability_crash_flag():
    """依赖大赢家序列：去尾后 avgR 转负 → crashed=True"""
    rs = [-1.0] * 90 + [30.0] * 10      # 90 亏 1R + 10 笔 30R 大赢家
    rows = tail_stability(rs, (0.01, 0.05, 0.10))
    base = rows[0]
    assert base["avg_r"] > 0            # 全量期望为正（靠大赢家）
    assert rows[1]["crashed"] or rows[2]["crashed"] or rows[3]["crashed"]


def test_tail_stability_healthy():
    """中间夯实序列：去尾后不崩"""
    rs = [1.0] * 60 + [-0.5] * 40       # 60% 胜 +1R / 40% 亏 0.5R
    rows = tail_stability(rs, (0.01, 0.05, 0.10))
    for r in rows[1:]:
        assert not r["crashed"]


# ── r_bucket_dist：R 档位直方图 ──


def test_bucket_boundaries():
    """档位边界归属：1.0→1~2R / 0.99→0~1R / 2.99→2~3R / 10.0→10R+ / 负值→负收益"""
    rs = [-2.0, 0.5, 1.0, 1.5, 2.99, 4.0, 7.0, 10.0, 12.0]
    rows = r_bucket_dist(rs)
    by = {r["label"]: r["n"] for r in rows}
    assert by["负收益 (<0)"] == 1
    assert by["0~1R"] == 1
    assert by["1~2R"] == 2        # 1.0, 1.5
    assert by["2~3R"] == 1        # 2.99
    assert by["3~5R"] == 1        # 4.0
    assert by["5~10R"] == 1       # 7.0
    assert by["10R+"] == 2        # 10.0, 12.0


def test_bucket_share_sums():
    """各档占比合计 = 100%"""
    rng = np.random.default_rng(11)
    rs = rng.normal(0.3, 1.2, 500).tolist()
    rows = r_bucket_dist(rs)
    assert abs(sum(r["pct"] for r in rows) - 1.0) < 1e-9
    assert sum(r["n"] for r in rows) == 500


# ── 区间内胜率分档 / 七分位 / 连亏直方图 ──


def test_bucket_final_equities():
    fin = np.array([-5.0, 0.0, 3.0, 7.0, 12.0])
    out = bucket_final_equities(fin, [("≥0R", 0.0), ("≥5R", 5.0)])
    assert out["≥0R"] == 4 / 5
    assert out["≥5R"] == 2 / 5


def test_final_quantiles():
    fin = np.arange(1.0, 101.0)          # 1..100
    q = final_quantiles(fin)
    assert abs(q[50] - 50.5) < 1e-9      # P50 = (50+51)/2 = 50.5
    assert abs(q[99] - 99.01) < 1e-6


def test_profit_r_thresholds():
    """+10%/+20% 本金（560/1120 元）→ R 阈值"""
    th = profit_r_thresholds(100.0)
    assert th[0] == ("≥+0%", 0.0)
    assert abs(th[1][1] - 5.6) < 1e-9    # 560/100
    assert abs(th[2][1] - 11.2) < 1e-9   # 1120/100


def test_streak_histogram():
    streaks = np.array([1, 2, 3, 4, 5, 6, 9, 12, 20, 25])
    h = streak_histogram(streaks)
    assert h["0-2"] == 2 / 10            # 1, 2
    assert h["3-5"] == 3 / 10            # 3, 4, 5
    assert h["21+"] == 1 / 10            # 25
    assert abs(sum(v for k, v in h.items() if not k.startswith("p")) - 1.0) < 1e-9


# ── 分段归属 / 分段蒙卡 ──


def test_trades_by_regime():
    """成交按信号日归牛/熊/震荡（series 注入，无前视）"""
    series = pd.Series("震荡", index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    series.loc[pd.to_datetime("2024-01-02")] = "牛"
    trades = [
        {"date": "2024-01-02", "pnl": 200.0, "risk_actual": 100.0},
        {"date": "2024-01-03", "pnl": -50.0, "risk_actual": 100.0},
        {"date": "2024-01-04", "pnl": 30.0, "risk_actual": 50.0},   # 指数日历外 → 未知
    ]
    out = _trades_by_regime(trades, series)
    assert len(out["牛"]) == 1
    assert abs(out["牛"][0][0] - 2.0) < 1e-9
    assert len(out["震荡"]) == 1
    assert len(out["未知"]) == 1


def test_segment_simulate_skip():
    """段样本 < SEG_MIN_TRADES → skipped 标注"""
    rows = segment_simulate({"牛": [1.0, -0.5, 0.3]}, 100.0)
    assert rows[0]["skipped"] is True
    assert rows[0]["n"] == 3


def test_segment_simulate_runs():
    """段样本充足 → 跑 100 次小规模（simulate 组件路径可用）"""
    from 回测系统.monte_carlo_dist import N_SIMULATIONS

    from 分析决策.跟踪.monte_carlo import simulate
    rs = [1.0] * 15 + [-0.5] * 10
    mc = simulate([{"r_multiple": r} for r in rs], n_simulations=100)
    assert "error" not in mc
    assert mc["n_trades"] == 25
    assert 0.0 < mc["prob_profit"] < 1.0
    assert N_SIMULATIONS == 10_000
