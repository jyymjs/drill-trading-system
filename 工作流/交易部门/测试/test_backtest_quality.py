"""回测质检单元测试：D1 前后半一致性 + D2 2倍成本压力（方案 D 类 2026-08-05）"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包（R-005 独立项目）

from 回测系统.engine import BacktestEngine
from 回测系统.params import BacktestParams
from 回测系统.quality import (
    annualized_r,
    check_cost_stress,
    check_half_consistency,
    participating_r,
    span_days,
)
from 回测系统.report import write_report
from 回测系统.tracking import Outcome, Signal, TrackedRecord, _trade_cost


def make_signal(code="600000", mode="normal", grade="S", date="2023-01-10") -> Signal:
    return Signal(code=code, date=pd.Timestamp(date), mode=mode, grade=grade,
                  scores={}, close=10.0, trigger=0.0 if mode == "normal" else 10.5,
                  stop=9.0, risk=1.0)


def make_rec(sig: Signal, r: float) -> TrackedRecord:
    oc = Outcome(hold=10, triggered=True, entry_price=10.0, exit_price=10.0 + r,
                 exit_date=pd.Timestamp("2024-02-01"), stopped=False, r=r)
    return TrackedRecord(signal=sig, outcomes={10: oc})


def make_pair(date: str, r: float, grade: str = "S") -> TrackedRecord:
    """一笔参与统计的记录（hold=10，r 直接给定）"""
    return make_rec(make_signal(date=date, grade=grade), r)


# ============================================================
# D1 前后半一致性
# ============================================================

class TestHalfConsistency:
    def test_both_positive_ok(self):
        """两半累计 R 同为正 → ✅ 一致性合格（区间 3.4 年，各半 ≥1.5 年）"""
        recs = [
            make_pair("2022-01-10", 1.0), make_pair("2022-05-10", 0.5),
            make_pair("2024-01-10", 1.0), make_pair("2025-06-10", 0.5),
        ]
        r = check_half_consistency(recs, holds=[10])
        assert r["verdict"] == "✅"
        assert r["front_total_r"] == pytest.approx(1.5, abs=1e-4)
        assert r["back_total_r"] == pytest.approx(1.5, abs=1e-4)
        assert r["total_days"] / 365.25 >= 3.0

    def test_front_positive_back_negative_overfit(self):
        """前正后负 → ⚠️ 过拟合嫌疑（防过拟合红线）"""
        recs = [
            make_pair("2022-01-10", 1.0), make_pair("2022-05-10", 0.5),
            make_pair("2024-01-10", -1.0), make_pair("2025-06-10", -0.5),
        ]
        r = check_half_consistency(recs, holds=[10])
        assert r["verdict"] == "⚠️"
        assert "过拟合嫌疑" in r["reason"]

    def test_front_negative_back_positive_accepted(self):
        """前负后正 → 正常接受（风格适应慢，不判罪）"""
        recs = [
            make_pair("2022-01-10", -1.0), make_pair("2022-05-10", -0.5),
            make_pair("2024-01-10", 1.0), make_pair("2025-06-10", 0.5),
        ]
        r = check_half_consistency(recs, holds=[10])
        assert r["verdict"] == "正常"
        assert "风格适应慢" in r["reason"]

    def test_both_negative_warn(self):
        """两半皆负 → ⚠️ 整体亏损"""
        recs = [
            make_pair("2022-01-10", -1.0), make_pair("2022-05-10", -0.5),
            make_pair("2024-01-10", -1.0), make_pair("2025-06-10", -0.5),
        ]
        r = check_half_consistency(recs, holds=[10])
        assert r["verdict"] == "⚠️"
        assert "整体亏损" in r["reason"]

    def test_s_grade_must_both_positive(self):
        """S 级硬门槛：前正后负 → ⚠️ S 级未达标（全样本可能 OK 但 S 级单独判黄）"""
        recs = [
            make_pair("2022-01-10", 1.0, grade="S"),
            make_pair("2024-01-10", -1.0, grade="S"),
            make_pair("2025-06-10", 0.5, grade="S"),
        ]
        r = check_half_consistency(recs, holds=[10], grade="S")
        assert r["verdict"] == "⚠️"
        assert "S 级未达" in r["reason"]

    def test_s_grade_both_positive_ok(self):
        """S 级两半同为正 → ✅（区间 ≥3 年）"""
        recs = [
            make_pair("2022-01-10", 1.0, grade="S"),
            make_pair("2025-06-10", 1.0, grade="S"),
        ]
        r = check_half_consistency(recs, holds=[10], grade="S")
        assert r["verdict"] == "✅"

    def test_short_span_not_applicable(self):
        """区间 <3 年 → ⚠️ 检查不成立"""
        recs = [
            make_pair("2024-01-10", 1.0), make_pair("2024-03-10", 1.0),
            make_pair("2025-01-10", 1.0), make_pair("2025-06-10", 1.0),
        ]
        r = check_half_consistency(recs, holds=[10])
        assert r["verdict"] == "⚠️"
        assert "不成立" in r["reason"]

    def test_empty_records_skipped(self):
        """无参与统计信号 → 跳过"""
        r = check_half_consistency([], holds=[10])
        assert r["verdict"] == "跳过"
        assert r["start"] is None

    def test_prebreak_untracked_not_counted(self):
        """prebreak 未触发不参与（与 stats 同口径）"""
        untrig = make_signal(mode="prebreak", date="2022-01-10")
        rec = TrackedRecord(signal=untrig,
                            outcomes={10: Outcome(10, False, 0.0, 0.0, None, False, 0.0)})
        r = check_half_consistency([rec], holds=[10])
        assert r["verdict"] == "跳过"


# ============================================================
# D2 2倍成本压力
# ============================================================

class TestCostStress:
    def test_trade_cost_multiplier_math(self):
        """2 倍成本 = (佣金+印花税)×2 + 滑点翻倍（万1×2）；基线=1.0 时与现口径一致"""
        entry, exit_p = 10.0, 11.0
        base = _trade_cost(entry, exit_p, True, 1.0)
        assert base == pytest.approx(entry * 0.00013 + exit_p * (0.00013 + 0.0005), abs=1e-6)
        stress = _trade_cost(entry, exit_p, True, 2.0)
        expect = (entry * 0.00013 + exit_p * (0.00013 + 0.0005)) * 2 + (entry + exit_p) * 0.0001 * 2
        assert stress == pytest.approx(expect, abs=1e-6)
        # 等价性：2 倍成本 == "基线（含滑点万1）×2"
        with_slippage = entry * (0.00013 + 0.0001) + exit_p * (0.00013 + 0.0005 + 0.0001)
        assert stress == pytest.approx(with_slippage * 2, abs=1e-6)
        # 禁用成本 → 0
        assert _trade_cost(entry, exit_p, False, 2.0) == 0.0

    def test_annualized_r(self):
        """年化 R = 累计 R / 年数（1 年累计 10R → 年化 10）"""
        assert annualized_r(10.0, 365.25) == pytest.approx(10.0, abs=1e-4)
        assert annualized_r(10.0, 730.5) == pytest.approx(5.0, abs=1e-4)
        assert annualized_r(10.0, 0.0) == 0.0

    def test_stress_pass_when_annual_positive(self):
        """2 倍成本下年化 R 仍为正 → ✅ 抗压合格"""
        base = [make_pair("2022-01-10", 1.0), make_pair("2022-05-10", 1.0),
                make_pair("2024-01-10", 1.0), make_pair("2025-06-10", 1.0)]
        stress = [make_pair("2022-01-10", 0.3), make_pair("2022-05-10", 0.3),
                  make_pair("2024-01-10", 0.3), make_pair("2025-06-10", 0.3)]
        r = check_cost_stress(base, stress, holds=[10])
        assert r["verdict"] == "✅"
        assert "抗压合格" in r["reason"]
        assert r["base"]["total_r"] == pytest.approx(4.0, abs=1e-4)
        assert r["stress"]["total_r"] == pytest.approx(1.2, abs=1e-4)
        assert r["years"] >= 3.0

    def test_stress_warn_when_annual_not_positive(self):
        """2 倍成本下年化 R ≤ 0 → ⚠️ 利润太薄（实盘必亏）"""
        base = [make_pair("2022-01-10", 1.0), make_pair("2022-05-10", 1.0),
                make_pair("2024-01-10", 1.0), make_pair("2025-06-10", 1.0)]
        stress = [make_pair("2022-01-10", -0.3), make_pair("2022-05-10", -0.3),
                  make_pair("2024-01-10", -0.3), make_pair("2025-06-10", -0.3)]
        r = check_cost_stress(base, stress, holds=[10])
        assert r["verdict"] == "⚠️"
        assert "利润太薄" in r["reason"]

    def test_stress_zero_annual_warns(self):
        """2 倍成本年化 R = 0（刚好不亏）→ ⚠️（要求仍为正）"""
        base = [make_pair("2022-01-10", 1.0), make_pair("2025-06-10", 1.0)]
        stress = [make_pair("2022-01-10", 0.0), make_pair("2025-06-10", 0.0)]
        r = check_cost_stress(base, stress, holds=[10])
        assert r["verdict"] == "⚠️"

    def test_stress_empty_skipped(self):
        """2 倍成本重跑无信号 → ⚠️ 无法判定"""
        base = [make_pair("2022-01-10", 1.0), make_pair("2025-06-10", 1.0)]
        r = check_cost_stress(base, [], holds=[10])
        assert r["verdict"] == "⚠️"
        assert "无法判定" in r["reason"]


# ============================================================
# report 挂接（D3）
# ============================================================

class TestReportSections:
    def test_report_contains_both_sections(self, tmp_path):
        """report.md 含 D1 分段一致性 与 D2 2倍成本压力 两节"""
        recs = [make_pair("2022-01-10", 1.0), make_pair("2025-06-10", 1.0)]
        params = BacktestParams(holds=[10], grades=["S", "A", "B"])
        from 回测系统.stats import group_stats
        buckets = group_stats(recs, params.holds)
        out = tmp_path / "report.md"
        write_report(out, recs, buckets, params, stress_records=recs)
        text = out.read_text(encoding="utf-8")
        assert "## 分段一致性检查（D1" in text
        assert "## 2 倍成本压力测试（D2" in text
        assert "判定：**✅**" in text

    def test_report_omits_stress_without_double_run(self, tmp_path):
        """未提供 stress_records（未双跑）→ 省略 D2 节，D1 仍在"""
        recs = [make_pair("2022-01-10", 1.0), make_pair("2025-06-10", 1.0)]
        params = BacktestParams(holds=[10], grades=["S", "A", "B"])
        from 回测系统.stats import group_stats
        buckets = group_stats(recs, params.holds)
        out = tmp_path / "report.md"
        write_report(out, recs, buckets, params)
        text = out.read_text(encoding="utf-8")
        assert "## 分段一致性检查（D1" in text
        assert "2 倍成本压力测试" not in text


# ============================================================
# engine cost_multiplier 透传（D2 重跑同源）
# ============================================================

def test_engine_cost_multiplier_passthrough():
    """引擎 2 倍成本重跑：同信号 R 更小（成本多扣一份+滑点），信号集合不变"""
    from 分析决策.分析.indicators import all_indicators
    from 回测系统.adapters.base import DataProvider, StrategyProvider

    NEEDED = ["VOL_RATIO", "BODY_RATIO", "MA20", "MA5", "ATR"]

    def make_kline(n: int = 400, seed: int = 42) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        close = 10 + np.arange(n) * 0.1 + rng.normal(0, 0.5, n).cumsum()
        high = close + abs(rng.normal(0, 0.3, n))
        low = close - abs(rng.normal(0, 0.3, n))
        open_ = close + rng.normal(0, 0.2, n)
        volume = rng.integers(10000, 100000, n)
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        return pd.DataFrame({"日期": dates, "开盘": open_, "收盘": close,
                             "最高": high, "最低": low, "成交量": volume})

    class _FakeProvider(DataProvider):
        def __init__(self, df):
            self._df = df

        def load(self, code):
            return self._df.copy()

        def compute_indicators(self, df, needed_cols):
            return all_indicators(df, needed_cols=needed_cols)

    class _FakeNormal(StrategyProvider):
        """恒出 S 级信号：保证每个网格日都有记录可对比"""
        name = "fake"

        def required_indicators(self):
            return NEEDED

        def quick_prefilter(self, df):
            return True

        def grade(self, df):
            return {"grade": "S", "scores": {k: ("S", "x") for k in
                                             ("PT平台测试", "TY统一区间", "DN动能", "DL独立结构",
                                              "LK轮廓质量", "SF释放级别")},
                    "dl_start": None, "match": True}

        def prebreak_grade(self, df):
            return {"grade": "C", "scores": {}, "trigger_price": 0, "stop_loss": 0,
                    "risk_per_share": 0, "match": False}

    df = make_kline(400, seed=7)
    provider = _FakeProvider(df)
    # 本测试只验证成本倍率传递：显式全关闸门（B1/C3/C4），排除执行层过滤干扰信号集合
    base = BacktestEngine(BacktestParams(codes=["000001"], interval=5, holds=[10],
                                         env_gate=False, volume_filter=False,
                                         sentiment_gate=False),
                          provider=provider, strategy=_FakeNormal())
    stress = BacktestEngine(BacktestParams(codes=["000001"], interval=5, holds=[10],
                                           cost_multiplier=2.0,
                                           env_gate=False, volume_filter=False,
                                           sentiment_gate=False),
                            provider=provider, strategy=_FakeNormal())

    recs1 = base._process_stock("000001")
    recs2 = stress._process_stock("000001")
    assert len(recs1) == len(recs2) == len(list(range(249, len(df), 5)))  # 信号集合不变

    strict_less = False
    for a, b in zip(recs1, recs2):
        for hold, oc in a.outcomes.items():
            oc2 = b.outcomes[hold]
            assert oc2.triggered == oc.triggered          # 触发判定不受成本影响
            assert oc2.entry_price == oc.entry_price      # 进出场价不受成本影响
            assert oc2.r <= oc.r + 1e-6                   # 2 倍成本 R 只减不增
            if oc.triggered and oc.r - oc2.r > 1e-6:
                strict_less = True
    assert strict_less  # 至少一笔成本加倍后 R 变小


def test_participating_r_sorted_and_filtered():
    """参与 R 序列按日期升序、模式过滤"""
    recs = [make_pair("2025-06-10", 1.0), make_pair("2022-01-10", 0.5),
            make_pair("2023-03-10", -0.5, grade="A")]
    pairs = participating_r(recs)
    assert [str(d.date()) for d, _ in pairs] == ["2022-01-10", "2023-03-10", "2025-06-10"]
    assert span_days(recs) == pytest.approx((pd.Timestamp("2025-06-10") - pd.Timestamp("2022-01-10")).days)
