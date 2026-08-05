"""市场状态分段单元测试：判定规则/分段统计/无前视/报告降级（T-021）"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包（R-005 独立项目）

from 回测系统.market_regime import regime_series, regime_stats
from 回测系统.report import write_report
from 回测系统.tracking import Outcome, Signal, TrackedRecord


def make_signal(code="600000", mode="normal", grade="S", date="2024-01-08") -> Signal:
    return Signal(code=code, date=pd.Timestamp(date), mode=mode, grade=grade,
                  scores={}, close=10.0, trigger=0.0 if mode == "normal" else 10.5,
                  stop=9.0, risk=1.0)


def make_rec(sig: Signal, outcomes: dict[int, Outcome]) -> TrackedRecord:
    return TrackedRecord(signal=sig, outcomes=outcomes)


def out(hold: int, r: float, triggered: bool = True) -> Outcome:
    return Outcome(hold=hold, triggered=triggered, entry_price=10.0, exit_price=10.0 + r,
                   exit_date=pd.Timestamp("2024-02-01"), stopped=False, r=r)


def make_index(close_list: list[float], start="2023-01-02") -> pd.DataFrame:
    """构造指数日线（收盘价序列 → 日期升序，交易日连续）"""
    dates = pd.bdate_range(start, periods=len(close_list))
    return pd.DataFrame({"日期": dates, "收盘": close_list})


def make_bull_index(n=160):
    """单边上涨 160 根：120 日后 收盘>MA120 且 MA20>MA60（牛市）"""
    return make_index(list(np.linspace(3000, 4000, n)))


def make_bear_index(n=160):
    """单边下跌 160 根：收盘<MA120（熊市）"""
    return make_index(list(np.linspace(4000, 3000, n)))


def make_range_index(n=160):
    """纯横盘 160 根（收盘恒 3500）：均线全部重合，既非牛（不>MA120）也非熊（不<MA120）"""
    return make_index([3500.0] * n)


# ============================================================
# 判定规则
# ============================================================

class TestRegimeRules:
    def test_bull_detected(self):
        """单边上涨 → 尾部判定为牛（收盘>MA120 且 MA20>MA60）"""
        df = make_bull_index()
        s = regime_series(df)
        assert s.iloc[-1] == "牛"

    def test_bear_detected(self):
        """单边下跌 → 尾部判定为熊（收盘<MA120）"""
        df = make_bear_index()
        s = regime_series(df)
        assert s.iloc[-1] == "熊"

    def test_range_is_oscillation(self):
        """窄幅震荡 → 尾部判定为震荡（均线纠缠，20 未持续高于 60）"""
        df = make_range_index()
        s = regime_series(df)
        assert s.iloc[-1] == "震荡"

    def test_insufficient_history_oscillation(self):
        """均线窗口不足（前 119 根 MA120=NaN）→ 震荡，不误判牛/熊"""
        df = make_bull_index(n=130)
        s = regime_series(df)
        assert s.iloc[0] == "震荡"   # MA120 尚未成形
        assert s.iloc[-1] == "牛"    # 尾部窗口已够

    def test_no_lookahead(self):
        """无前视：某日状态只用 ≤ 当日数据（改尾部行情不影响前段判定）"""
        df_up = make_bull_index()
        df_dn = df_up.copy()
        # 把最后 10 根改成暴跌，其余不变 → 前段状态应一致
        df_dn.loc[df_dn.index[-10:], "收盘"] = df_up["收盘"].iloc[-11] * 0.9
        s_up = regime_series(df_up)
        s_dn = regime_series(df_dn)
        assert (s_up.iloc[:-10].values == s_dn.iloc[:-10].values).all()


# ============================================================
# 分段统计
# ============================================================

class TestRegimeStats:
    def test_bull_segment_stats(self):
        """牛市段：笔数/胜率/avgR/盈亏比 与手算一致"""
        df = make_bull_index()
        recs = [
            make_rec(make_signal(date="2023-08-01"), {10: out(10, 1.0)}),   # 牛市（后段）
            make_rec(make_signal(date="2023-08-02"), {10: out(10, 0.5)}),
            make_rec(make_signal(date="2023-08-03"), {10: out(10, -0.5)}),
        ]
        st = regime_stats(recs, df, holds=[10])
        bull = st["牛"]
        assert bull["n_participate"] == 3
        assert bull["n_win"] == 2
        assert bull["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert bull["avg_r"] == pytest.approx(1.0 / 3, abs=1e-4)
        assert bull["total_r"] == pytest.approx(1.0, abs=1e-4)
        # 盈亏比 = (1.0+0.5)/0.5 = 3.0
        assert bull["profit_factor"] == pytest.approx(3.0, abs=1e-4)

    def test_bear_segment_stats(self):
        """熊市段独立统计（与牛市段互不干扰）"""
        df = make_bear_index()
        recs = [
            make_rec(make_signal(date="2023-08-01"), {10: out(10, -1.0)}),
            make_rec(make_signal(date="2023-08-02"), {10: out(10, -0.5)}),
        ]
        st = regime_stats(recs, df, holds=[10])
        assert st["熊"]["n_participate"] == 2
        assert st["熊"]["total_r"] == pytest.approx(-1.5, abs=1e-4)
        assert st["熊"]["win_rate"] == 0.0
        # 全亏 → 盈亏比 0.0（盈利 0）；None 语义 = 全赢无穷大
        assert st["熊"]["profit_factor"] == 0.0
        assert st["牛"]["n_participate"] == 0

    def test_all_segments_cover_all(self):
        """牛+熊+震荡+未知 的参与笔数之和 = 全部参与笔数"""
        df = make_range_index()
        rng = np.random.default_rng(7)
        recs = []
        for i in range(50):
            date = pd.Timestamp("2023-06-01") + pd.Timedelta(days=i)
            recs.append(make_rec(make_signal(date=str(date.date())),
                                 {10: out(10, float(rng.normal(0, 1)))}))
        st = regime_stats(recs, df, holds=[10])
        total = sum(b["n_participate"] for b in st.values())
        assert total == 50

    def test_unknown_when_date_missing(self):
        """信号日不在指数日历 → 归'未知'段"""
        df = make_bull_index()
        recs = [make_rec(make_signal(date="2024-05-01"), {10: out(10, 1.0)})]  # 超出指数日历
        st = regime_stats(recs, df, holds=[10])
        assert st["未知"]["n_participate"] == 1
        assert st["牛"]["n_participate"] == 0

    def test_mode_filter(self):
        """mode 过滤：只看 prebreak 时 normal 不计入"""
        df = make_bull_index()
        recs = [
            make_rec(make_signal(mode="normal", date="2023-08-01"), {10: out(10, 1.0)}),
            make_rec(make_signal(mode="prebreak", date="2023-08-02"), {10: out(10, 2.0)}),
        ]
        st = regime_stats(recs, df, holds=[10], mode="prebreak")
        assert st["牛"]["n_participate"] == 1
        assert st["牛"]["total_r"] == pytest.approx(2.0, abs=1e-4)

    def test_determinism(self):
        """同输入两次分段统计结果完全一致"""
        df = make_range_index()
        rng = np.random.default_rng(3)
        recs = []
        for i in range(40):
            date = pd.Timestamp("2023-06-01") + pd.Timedelta(days=i)
            recs.append(make_rec(make_signal(date=str(date.date())),
                                 {10: out(10, float(rng.normal(0, 1))),
                                  20: out(20, float(rng.normal(0, 1)))}))
        a = regime_stats(recs, df, holds=[10, 20])
        b = regime_stats(recs, df, holds=[10, 20])
        for k in a:
            assert a[k] == b[k], k


# ============================================================
# 报告渲染（降级安全）
# ============================================================

class TestRegimeReport:
    def test_report_without_index_skips(self):
        """无指数数据 → 报告含'跳过'说明，不抛异常"""
        recs = [make_rec(make_signal(date="2024-01-08"), {10: out(10, 1.0)})]
        from 回测系统.params import BacktestParams
        text = write_report_str(recs, None, BacktestParams(holds=[10]))
        assert "未提供指数数据" in text

    def test_report_with_index_has_section(self):
        """有指数数据 → 报告含市场状态分段表（牛/熊/震荡行）"""
        df = make_bull_index()
        recs = [make_rec(make_signal(date="2023-08-01"), {10: out(10, 1.0)})]
        from 回测系统.params import BacktestParams
        text = write_report_str(recs, df, BacktestParams(holds=[10]))
        assert "市场状态分段" in text
        assert "| 牛 |" in text
        assert "| 熊 |" in text
        assert "| 震荡 |" in text


def write_report_str(records, index_df, params) -> str:
    """写报告到临时文件并返回文本（复用正式渲染管线）"""
    import tempfile
    from pathlib import Path

    from 回测系统.stats import group_stats
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.md"
        write_report(path, records, group_stats(records, params.holds), params,
                     meta={"processed": 1, "skipped": 0, "gate_counts": {}},
                     index_df=index_df)
        return path.read_text(encoding="utf-8")
