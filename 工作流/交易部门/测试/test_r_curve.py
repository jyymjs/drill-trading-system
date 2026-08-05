"""R 值曲线单元测试（2026-08-06 老板拍板功能）

覆盖：
  - R 值计算（做多/做空/边界）
  - 最大回撤（与回测 stats.py 口径对拍，保证同构）
  - 统计（胜率/平均R/盈亏比/连亏/期望值）
  - 数据往返（录入/读取/删除/同日多笔）
  - 渲染不抛错
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 回测系统包根（对拍口径用）：项目/回测系统/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 分析决策.跟踪 import r_curve
from 分析决策.跟踪.r_curve import (
    add_record,
    calc_r,
    compute_stats,
    delete_record,
    get_records,
    max_drawdown_from_r,
    render_terminal_report,
)

# ============================================================
# 数据文件隔离：测试用 tmp_path，不碰真实账本
# ============================================================

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """把记录文件重定向到临时目录"""
    monkeypatch.setattr(r_curve, "R_CURVE_FILE", tmp_path / "r_curve.csv")
    return tmp_path


# ============================================================
# R 值计算（老师 24 节口径）
# ============================================================

class TestCalcR:
    def test_long_profit(self):
        """做多盈利：R = (出场-入场)/(入场-止损)"""
        assert calc_r(10, 9, 12) == 2.0

    def test_long_stop_loss(self):
        """做多止损平仓：R = -1"""
        assert calc_r(10, 9, 9) == -1.0

    def test_long_breakeven(self):
        """做多平局：R = 0"""
        assert calc_r(10, 9, 10) == 0.0

    def test_long_partial_win(self):
        """做多小赚：R 为分数"""
        assert calc_r(10, 8, 11) == 0.5

    def test_long_zero_risk_rejected(self):
        """止损价等于入场价：风险为 0 无法算 R"""
        with pytest.raises(ValueError):
            calc_r(10, 10, 12)

    def test_long_stop_above_entry_rejected(self):
        """做多止损高于入场：参数非法"""
        with pytest.raises(ValueError):
            calc_r(10, 11, 12)

    def test_short_profit(self):
        """做空盈利：R = (入场-出场)/(止损-入场)"""
        assert calc_r(10, 11, 8, "short") == 2.0

    def test_short_stop_loss(self):
        """做空止损平仓：R = -1"""
        assert calc_r(10, 11, 11, "short") == -1.0

    def test_bad_direction(self):
        with pytest.raises(ValueError):
            calc_r(10, 9, 12, "sideways")

    def test_non_positive_price(self):
        with pytest.raises(ValueError):
            calc_r(0, 9, 12)


# ============================================================
# 最大回撤（与回测 stats.py 口径对拍）
# ============================================================

class TestMaxDrawdown:
    def test_empty(self):
        assert max_drawdown_from_r([]) == 0.0

    def test_monotonic_up(self):
        """单调上涨无回撤"""
        assert max_drawdown_from_r([1, 1, 2, 3]) == 0.0

    def test_simple_dip(self):
        """[1, -2, 1]：累计 1 → -1 → 0，峰值 1 回撤 2R"""
        assert max_drawdown_from_r([1, -2, 1]) == 2.0

    def test_multi_episode_takes_max(self):
        """多段回撤取最深的一段"""
        # 累计：2 → 1 → 3 → 1.5 → 4 → 3.5
        # 段1回撤 1R，段2回撤 1.5R，段3回撤 0.5R → 最大 1.5
        assert max_drawdown_from_r([2, -1, 2, -1.5, 2.5, -0.5]) == 1.5

    def test_drawdown_capped_at_peak(self):
        """全亏光只到峰值（1R 等权组合口径，不跌破起点）"""
        # 累计：1 → -2 → -1 → -3 → 0，最大回撤 = 峰值1 → 最低-3 = 4R
        assert max_drawdown_from_r([1, -3, 1, -2, 3]) == 4.0

    def test_matches_backtest_stats(self):
        """与回测 stats.py 的 _max_drawdown_from_r 结果完全一致（口径同构）"""
        from 回测系统.stats import _max_drawdown_from_r as bt_dd
        sequences = [
            [1.5, -1.0, 2.0, -1.0, 0.5],
            [0.3, 0.2, -0.8, 0.1, -0.4, 0.6],
            [-1, -1, 2, -1, -1, -1, 4],
            [2, -1, 2, -1.5, 2.5, -0.5, 1, -3, 2],
        ]
        for seq in sequences:
            assert max_drawdown_from_r(seq) == bt_dd(seq)


# ============================================================
# 统计（胜率/平均R/盈亏比/连亏/期望值）
# ============================================================

class TestComputeStats:
    def _recs(self, r_values, start="2026-08-03"):
        """构造按日期递增的记录列表"""
        from datetime import date, timedelta
        recs = []
        d = date.fromisoformat(start)
        for i, r in enumerate(r_values):
            recs.append({
                "id": i + 1, "date": (d + timedelta(days=i)).isoformat(),
                "r": r, "entry": "", "stop": "", "exit": "",
                "symbol": "", "note": "",
            })
        return recs

    def test_empty(self):
        assert compute_stats([]) == {"n_trades": 0}

    def test_hand_calculated(self):
        """固定序列手算验证"""
        # R: +1.5, -1.0, +2.0, -1.0, +0.5
        stats = compute_stats(self._recs([1.5, -1.0, 2.0, -1.0, 0.5]))
        assert stats["n_trades"] == 5
        assert stats["n_win"] == 3
        assert stats["win_rate"] == 0.6
        assert stats["total_r"] == 2.0
        assert stats["avg_r"] == 0.4
        # 盈亏比 = (4.0/3) / 1.0
        assert stats["payoff_ratio"] == pytest.approx(1.3333)
        # 最大回撤：累计 1.5→0.5→2.5→1.5→2.0，峰值 2.5 回撤 1.0
        assert stats["max_drawdown"] == 1.0
        assert stats["cum_curve"] == [1.5, 0.5, 2.5, 1.5, 2.0]

    def test_all_wins_payoff_inf(self):
        """全胜：盈亏比未定义（None，渲染为 ∞）"""
        stats = compute_stats(self._recs([1.0, 0.5, 2.0]))
        assert stats["payoff_ratio"] is None
        assert stats["win_rate"] == 1.0
        assert stats["max_drawdown"] == 0.0

    def test_loss_streaks(self):
        """连亏：最大连亏与当前连亏"""
        # 结尾连续 2 亏
        stats = compute_stats(self._recs([2.0, 1.0, -0.5, -1.0]))
        assert stats["max_loss_streak"] == 2
        assert stats["current_loss_streak"] == 2
        # 中间 3 连亏，结尾盈利
        stats = compute_stats(self._recs([2.0, -1.0, -1.0, -1.0, 1.0]))
        assert stats["max_loss_streak"] == 3
        assert stats["current_loss_streak"] == 0

    def test_expectancy(self):
        """期望值 = 胜率×平均盈R - 败率×平均亏R"""
        # 胜率 0.6，平均盈 4/3 R，平均亏 1.0 R → 0.6×(4/3) - 0.4×1.0 = 0.4
        stats = compute_stats(self._recs([1.5, -1.0, 2.0, -1.0, 0.5]))
        assert stats["expectancy"] == pytest.approx(0.4, abs=1e-3)


# ============================================================
# 数据层：录入 / 读取 / 删除 / 同日多笔
# ============================================================

class TestDataLayer:
    def test_add_and_get(self, isolated):
        add_record("2026-08-03", 1.5, entry=10, stop=9, exit_price=12)
        add_record("2026-08-04", -1.0, entry=10, stop=9, exit_price=9)
        recs = get_records()
        assert len(recs) == 2
        assert [r["r"] for r in recs] == [1.5, -1.0]

    def test_unsorted_input_sorted_output(self, isolated):
        """乱序录入 → 读取按日期升序（曲线可复现）"""
        add_record("2026-08-05", 0.5)
        add_record("2026-08-03", 1.5)
        add_record("2026-08-04", -1.0)
        recs = get_records()
        assert [r["date"] for r in recs] == ["2026-08-03", "2026-08-04", "2026-08-05"]

    def test_same_day_multiple(self, isolated):
        """同日多笔交易：id 区分，全部保留"""
        a = add_record("2026-08-03", 1.5)
        b = add_record("2026-08-03", -1.0)
        c = add_record("2026-08-03", 2.0)
        recs = get_records()
        assert len(recs) == 3
        assert [r["id"] for r in recs] == [a["id"], b["id"], c["id"]]

    def test_delete(self, isolated):
        add_record("2026-08-03", 1.5)
        mid = add_record("2026-08-04", -1.0)
        add_record("2026-08-05", 2.0)
        assert delete_record(mid["id"]) is True
        recs = get_records()
        assert len(recs) == 2
        assert [r["r"] for r in recs] == [1.5, 2.0]
        assert delete_record(999) is False

    def test_record_r_direct(self, isolated):
        """直接录 R 值（最省事模式）"""
        add_record("2026-08-03", -1.0, note="止损")
        recs = get_records()
        assert len(recs) == 1
        assert recs[0]["r"] == -1.0
        assert recs[0]["note"] == "止损"

    def test_extreme_r_warns(self, isolated, capsys):
        """R 值低于常见亏损区间 → 告警提示"""
        add_record("2026-08-03", -3.5)
        out = capsys.readouterr().out
        assert "低于常见亏损区间" in out

    def test_bad_date_rejected(self, isolated):
        """非法日期格式报错"""
        with pytest.raises(ValueError):
            add_record("2026/08/03", 1.0)


# ============================================================
# 渲染
# ============================================================

class TestRender:
    def test_empty_report(self):
        text = render_terminal_report({"n_trades": 0})
        assert "暂无 R 值记录" in text

    def test_report_contains_key_metrics(self, isolated):
        add_record("2026-08-03", 1.5, symbol="600419")
        add_record("2026-08-04", -1.0)
        recs = get_records()
        stats = compute_stats(recs)
        text = render_terminal_report(stats, recs)
        assert "胜率" in text
        assert "平均 R" in text
        assert "盈亏比" in text
        assert "最大回撤" in text
        assert "600419" in text

    def test_plot_no_save_no_error(self, isolated):
        add_record("2026-08-03", 1.5)
        add_record("2026-08-04", -1.0)
        path = r_curve.plot_r_curve(get_records(), save=False)
        assert path == ""
