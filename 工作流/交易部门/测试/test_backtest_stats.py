"""回测统计单元测试：统计正确性/SAB分列/确定性"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包（R-005 独立项目）

from 回测系统.stats import _max_drawdown_from_r, group_stats, merge_monthly, mode_stats
from 回测系统.tracking import Outcome, Signal, TrackedRecord


def make_signal(code="600000", mode="normal", grade="S", date="2024-01-08") -> Signal:
    return Signal(code=code, date=pd.Timestamp(date), mode=mode, grade=grade,
                  scores={}, close=10.0, trigger=0.0 if mode == "normal" else 10.5,
                  stop=9.0, risk=1.0)


def make_rec(sig: Signal, outcomes: dict[int, Outcome]) -> TrackedRecord:
    return TrackedRecord(signal=sig, outcomes=outcomes)


def out(hold: int, r: float, triggered: bool = True, stopped: bool = False) -> Outcome:
    return Outcome(hold=hold, triggered=triggered, entry_price=10.0, exit_price=10.0 + r,
                   exit_date=pd.Timestamp("2024-02-01"), stopped=stopped, r=r)


# ============================================================
# 统计正确性
# ============================================================

class TestStatsCorrectness:
    def test_win_rate_avg_r(self):
        """3 笔 R=[1.0, -0.5, 0.25] → 胜率 2/3，平均R 0.25"""
        recs = [
            make_rec(make_signal(date="2024-01-08"), {10: out(10, 1.0)}),
            make_rec(make_signal(date="2024-01-09"), {10: out(10, -0.5)}),
            make_rec(make_signal(date="2024-01-10"), {10: out(10, 0.25)}),
        ]
        buckets = group_stats(recs, holds=[10])
        b = buckets["normal|S|10"]
        assert b.n_signals == 3
        assert b.n_participate == 3
        assert b.n_win == 2
        assert b.win_rate == pytest.approx(2 / 3, abs=1e-4)
        assert b.avg_r == pytest.approx(0.25, abs=1e-4)
        assert b.total_r == pytest.approx(0.75, abs=1e-4)

    def test_max_drawdown(self):
        """累计 R [1, -0.5, 1] → 峰值1.5，回撤 0.5"""
        assert _max_drawdown_from_r([1.0, -0.5, 1.0]) == pytest.approx(0.5, abs=1e-4)
        # 单调盈利 → 回撤 0
        assert _max_drawdown_from_r([0.5, 0.5, 0.5]) == 0.0
        # 单笔亏损 → 回撤 = 亏损额
        assert _max_drawdown_from_r([-0.8]) == pytest.approx(0.8, abs=1e-4)

    def test_prebreak_untracked_excluded(self):
        """prebreak 未触发计信号数/触发率，不参与胜率/平均R"""
        trig = make_rec(make_signal(mode="prebreak", grade="A", date="2024-01-08"),
                        {5: out(5, 0.5, triggered=True)})
        untrig = make_rec(make_signal(mode="prebreak", grade="A", date="2024-01-09"),
                          {5: out(5, -99.0, triggered=False)})
        buckets = group_stats([trig, untrig], holds=[5])
        b = buckets["prebreak|A|5"]
        assert b.n_signals == 2
        assert b.n_triggered == 1
        assert b.trigger_rate == pytest.approx(0.5, abs=1e-4)
        assert b.n_participate == 1
        assert b.win_rate == 1.0
        assert b.avg_r == pytest.approx(0.5, abs=1e-4)

    def test_sab_breakdown(self):
        """S/A/B 分列独立统计"""
        recs = [
            make_rec(make_signal(grade="S", date="2024-01-08"), {5: out(5, 1.0)}),
            make_rec(make_signal(grade="S", date="2024-01-09"), {5: out(5, -0.5)}),
            make_rec(make_signal(grade="A", date="2024-01-10"), {5: out(5, 0.2)}),
            make_rec(make_signal(grade="B", date="2024-01-11"), {5: out(5, -0.2)}),
        ]
        buckets = group_stats(recs, holds=[5])
        assert buckets["normal|S|5"].n_signals == 2
        assert buckets["normal|A|5"].n_signals == 1
        assert buckets["normal|B|5"].n_signals == 1
        assert buckets["normal|S|5"].win_rate == pytest.approx(0.5, abs=1e-4)
        assert buckets["normal|A|5"].win_rate == 1.0
        assert buckets["normal|B|5"].win_rate == 0.0

    def test_monthly_distribution(self):
        """月度分布按信号日聚合"""
        recs = [
            make_rec(make_signal(date="2024-01-08"), {5: out(5, 1.0)}),
            make_rec(make_signal(date="2024-01-20"), {5: out(5, 1.0)}),
            make_rec(make_signal(date="2024-02-01"), {5: out(5, 1.0)}),
            make_rec(make_signal(mode="prebreak", date="2024-01-09"), {5: out(5, 1.0, triggered=False)}),
        ]
        buckets = group_stats(recs, holds=[5])
        merged = merge_monthly(buckets, "normal")
        assert merged["2024-01"] == 2
        assert merged["2024-02"] == 1
        # prebreak 未触发也计信号数 → 月度
        merged_pb = merge_monthly(buckets, "prebreak")
        assert merged_pb["2024-01"] == 1

    def test_multi_hold_independent(self):
        """多 hold 各自独立统计"""
        recs = [
            make_rec(make_signal(date="2024-01-08"),
                     {5: out(5, 1.0), 10: out(10, -1.0)}),
        ]
        buckets = group_stats(recs, holds=[5, 10])
        assert buckets["normal|S|5"].n_signals == 1
        assert buckets["normal|S|5"].avg_r == pytest.approx(1.0, abs=1e-4)
        assert buckets["normal|S|10"].avg_r == pytest.approx(-1.0, abs=1e-4)

    def test_mode_stats_totals(self):
        """模式合计：跨等级跨 hold"""
        recs = [
            make_rec(make_signal(grade="S", date="2024-01-08"),
                     {5: out(5, 1.0), 10: out(10, 0.5)}),
            make_rec(make_signal(grade="B", date="2024-01-09"), {5: out(5, -0.5)}),
            make_rec(make_signal(mode="prebreak", grade="A", date="2024-01-10"),
                     {5: out(5, 0.3, triggered=False)}),
        ]
        m = mode_stats(recs, "normal", holds=[5, 10])
        assert m["n_signals"] == 3
        assert m["n_participate"] == 3
        assert m["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert m["avg_r"] == pytest.approx((1.0 + 0.5 - 0.5) / 3, abs=1e-4)
        mp = mode_stats(recs, "prebreak", holds=[5, 10])
        assert mp["n_signals"] == 1 and mp["n_triggered"] == 0
        assert mp["n_participate"] == 0


# ============================================================
# 确定性
# ============================================================

class TestDeterminism:
    def test_group_stats_stable(self):
        """同输入两次统计结果完全一致（dict 全等）"""
        rng = np.random.default_rng(42)
        recs = []
        for i in range(30):
            mode = "normal" if i % 2 == 0 else "prebreak"
            grade = ["S", "A", "B"][i % 3]
            triggered = True if mode == "normal" else (i % 4 != 0)
            recs.append(make_rec(
                make_signal(mode=mode, grade=grade, date=f"2024-01-{i % 28 + 1:02d}"),
                {5: out(5, float(rng.normal(0, 0.5)), triggered=triggered),
                 10: out(10, float(rng.normal(0, 0.5)), triggered=triggered)}))
        a = group_stats(recs, holds=[5, 10])
        b = group_stats(recs, holds=[5, 10])
        for key in a:
            assert a[key].__dict__ == b[key].__dict__, key

    def test_bucket_keys_stable_order(self):
        """空输入也产出稳定结构（缺失组合补零块，评级按 A/B/S 字母序）"""
        buckets = group_stats([], holds=[5, 10])
        assert list(buckets.keys()) == [
            "normal|A|5", "normal|A|10", "normal|B|5", "normal|B|10",
            "normal|S|5", "normal|S|10", "prebreak|A|5", "prebreak|A|10",
            "prebreak|B|5", "prebreak|B|10", "prebreak|S|5", "prebreak|S|10",
        ]
        assert buckets["normal|A|5"].n_signals == 0
