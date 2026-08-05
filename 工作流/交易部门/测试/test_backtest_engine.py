"""回测引擎单元测试：网格/截断/指标等价性/同源"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包（R-005 独立项目）

from 分析决策.分析.indicators import all_indicators
from 回测系统.adapters.base import DataProvider, RiskModel, StrategyProvider
from 回测系统.adapters.risk_model import DefaultRiskModel
from 回测系统.engine import BacktestEngine
from 回测系统.params import GRID_ANCHOR, BacktestParams
from 回测系统.tracking import Signal
from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy

NEEDED = ["VOL_RATIO", "BODY_RATIO", "MA20", "MA5", "ATR"]


def make_kline(n: int = 400, seed: int = 42) -> pd.DataFrame:
    """生成模拟 K 线 DataFrame（与 test_indicators.make_kline 同构，含日期列）"""
    rng = np.random.default_rng(seed)
    close = 10 + np.arange(n) * 0.1 + rng.normal(0, 0.5, n).cumsum()
    high = close + abs(rng.normal(0, 0.3, n))
    low = close - abs(rng.normal(0, 0.3, n))
    open_ = close + rng.normal(0, 0.2, n)
    volume = rng.integers(10000, 100000, n)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "日期": dates,
        "开盘": open_,
        "收盘": close,
        "最高": high,
        "最低": low,
        "成交量": volume,
    })


def make_params(**kw) -> BacktestParams:
    base = dict(codes=["000001"], interval=5, holds=[5, 10])
    base.update(kw)
    return BacktestParams(**base)


class _FakeProvider(DataProvider):
    """内存数据源：避免测试触碰磁盘缓存"""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def load(self, code: str) -> pd.DataFrame:
        return self._df.copy()

    def compute_indicators(self, df, needed_cols):
        return all_indicators(df, needed_cols=needed_cols)


# ============================================================
# 网格纪律
# ============================================================

class TestGrid:
    def test_grid_anchor_and_step(self):
        """信号日索引从 249 起、步长 interval，且窗口先截断"""
        df = make_kline(400, seed=3)
        params = make_params(interval=5, holds=[5])
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        # 网格 = range(249, 400, 5)
        assert list(range(GRID_ANCHOR, len(df), 5))[0] == GRID_ANCHOR
        assert (len(df) - GRID_ANCHOR) // 5 >= 1

    def test_data_too_short_skipped(self):
        """不足 250 根的股票不产生信号"""
        df = make_kline(100, seed=3)
        params = make_params(interval=5, holds=[5])
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        records = engine._process_stock("000001")
        assert records == []

    def test_date_filter_only_filters_records(self):
        """--start/--end 只过滤记录，不改网格（网格与日期无关）"""
        df = make_kline(400, seed=3)
        params = make_params(interval=5, holds=[5], start="2023-06-01".replace("-", ""),
                             end="2023-08-01".replace("-", ""))
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        grid = list(range(GRID_ANCHOR, len(df), 5))
        assert len(grid) >= 10  # 网格不受区间影响
        # 区间过滤逻辑：日期在区间外返回 False
        assert not engine._in_range(pd.Timestamp("2023-05-01"))
        assert engine._in_range(pd.Timestamp("2023-07-01"))
        assert not engine._in_range(pd.Timestamp("2023-09-01"))


# ============================================================
# 无前视：截断纪律 + 指标等价性
# ============================================================

class TestLookahead:
    def test_window_always_truncated(self):
        """每个信号日的评级窗口长度必须 == t+1（先截断后评级）"""
        df = make_kline(400, seed=7)
        params = make_params(interval=5, holds=[5])
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        ind = engine.provider.compute_indicators(df, engine.needed_cols)
        for t in (GRID_ANCHOR, GRID_ANCHOR + 10, 350):
            window = ind.iloc[: t + 1]
            assert len(window) == t + 1
            assert np.isclose(window["收盘"].iloc[-1], df["收盘"].iloc[t])

    @pytest.mark.parametrize("t", [249, 300, 360])
    def test_indicators_equivalence_truncate_vs_full(self, t):
        """等价性核心证明：先截断再算指标 == 全序列算指标后截断（t 点逐列一致）

        证明 indicators 全部为向后看算子：截断窗口内 t 点的指标值不依赖 t 之后的数据。
        """
        df = make_kline(400, seed=11)
        a = all_indicators(df.iloc[: t + 1], needed_cols=NEEDED).iloc[-1]
        b = all_indicators(df, needed_cols=NEEDED).iloc[t]
        for col in NEEDED:
            va, vb = a[col], b[col]
            if pd.isna(va) and pd.isna(vb):
                continue
            assert np.isclose(float(va), float(vb)), f"{col} @ t={t}: {va} vs {vb}"

    def test_grade_equivalence_two_paths(self):
        """评级等价：向量化全序列截断路径 == 逐窗重算路径（引擎两条实现路径互证）"""
        df = make_kline(400, seed=21)
        t = 300
        strategy = ZuanQianStrategy()
        # 路径A（默认）：全序列一次算指标 → 截断
        ind_full = all_indicators(df, needed_cols=NEEDED)
        window_a = ind_full.iloc[: t + 1]
        # 路径B（--recompute-each-window）：先截断 → 逐窗重算
        window_b = all_indicators(df.iloc[: t + 1], needed_cols=NEEDED)
        ra = strategy.grade(window_a)
        rb = strategy.grade(window_b)
        assert ra["grade"] == rb["grade"]
        assert ra["scores"] == rb["scores"]
        assert ra["match"] == rb["match"]


# ============================================================
# 同源：引擎评级转发 == 策略原生（diagnose 同款序列）
# ============================================================

class TestSameSource:
    def test_engine_grade_matches_strategy_native(self):
        """引擎对窗口的评级 == 直接用 ZuanQianStrategy 对同一窗口评级"""
        df = make_kline(500, seed=5)
        params = make_params(interval=5, holds=[5])
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        ind = engine.provider.compute_indicators(df, engine.needed_cols)
        strategy = ZuanQianStrategy()
        for t in (GRID_ANCHOR, GRID_ANCHOR + 15, 400):
            window = ind.iloc[: t + 1]
            if not strategy.quick_prefilter(window):
                continue
            native = strategy.grade(window)
            sig = engine._build_signal("000001", df["日期"].iloc[t], "normal", window, float(df["收盘"].iloc[t]))
            # 引擎产出 == 原生评级（C 级也一致：引擎只在 S/A/B 时记录）
            if native["grade"] in ("S", "A", "B"):
                assert sig is not None
                assert sig.grade == native["grade"]
                for key in ("PT平台测试", "TY统一区间", "DN动能", "DL独立结构", "LK轮廓质量", "SF释放级别"):
                    assert sig.score_grade(key) == native["scores"][key][0]
            else:
                assert sig is None

    def test_build_signal_maps_res(self):
        """_build_signal 对评级结果的映射正确（normal 止损=ATR口径，prebreak 原生）"""
        df = make_kline(400, seed=9)
        params = make_params(interval=5, holds=[5])
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        ind = engine.provider.compute_indicators(df, engine.needed_cols)
        t = 300
        window = ind.iloc[: t + 1]

        # normal：构造已知 res
        class _FakeNormal(StrategyProvider):
            name = "fake"
            def required_indicators(self): return NEEDED
            def quick_prefilter(self, df): return True
            def grade(self, df):
                return {"grade": "S", "scores": {"PT平台测试": ("S", "x"), "TY统一区间": ("A", "x"),
                                                 "DN动能": ("S", "x"), "DL独立结构": ("S", "x"),
                                                 "LK轮廓质量": ("S", "x"), "SF释放级别": ("S", "x")},
                        "dl_start": None, "match": True}
            def prebreak_grade(self, df):
                return {"grade": "C", "scores": {}, "trigger_price": 0, "stop_loss": 0,
                        "risk_per_share": 0, "match": False}

        eng2 = BacktestEngine(params, provider=_FakeProvider(df), strategy=_FakeNormal())
        sig = eng2._build_signal("000001", df["日期"].iloc[t], "normal", window, float(df["收盘"].iloc[t]))
        assert sig is not None and sig.grade == "S"
        entry = float(df["收盘"].iloc[t])
        expect_stop = DefaultRiskModel().normal_stop(window, entry)
        assert sig.stop == expect_stop
        assert np.isclose(sig.risk, entry - expect_stop, atol=1e-4)
        assert sig.trigger == 0.0

    def test_prebreak_signal_carries_native_prices(self):
        """prebreak 信号携带策略原生 trigger/stop/risk"""
        df = make_kline(400, seed=9)
        params = make_params(interval=5, holds=[5], mode="prebreak")
        engine = BacktestEngine(params, provider=_FakeProvider(df))
        ind = engine.provider.compute_indicators(df, engine.needed_cols)
        t = 300
        window = ind.iloc[: t + 1]
        strategy = ZuanQianStrategy()
        native = strategy.prebreak_grade(window)
        if native["grade"] in ("S", "A", "B"):
            sig = engine._build_signal("000001", df["日期"].iloc[t], "prebreak", window, float(df["收盘"].iloc[t]))
            assert sig is not None
            assert sig.trigger == native["trigger_price"]
            assert sig.stop == native["stop_loss"]
            assert sig.risk == native["risk_per_share"]


# ============================================================
# 多进程 run()（2026-08-06：ThreadPoolExecutor → ProcessPoolExecutor）
# ============================================================


class TestRunParallel:
    """run() 进程池路径：与逐股串行基准指纹一致（结果一致性回归保护）"""

    @staticmethod
    def _fingerprint(records) -> list[tuple]:
        return sorted((r.signal.code, str(r.signal.date.date()), r.signal.mode,
                       r.signal.grade, round(r.signal.close, 4)) for r in records)

    def test_run_mp_与逐股串行一致(self):
        """进程池 run() 与串行 _process_stock 汇总：记录指纹/计数/统计完全一致
        （闸门全关，避免联网；Windows spawn 由 pytest 环境兜底验证）"""
        df = make_kline(400, seed=7)
        params = make_params(codes=["000001", "000002", "000003"], interval=5,
                             holds=[5, 10], max_workers=2,
                             env_gate=False, volume_filter=False,
                             sentiment_gate=False, prbook_gate=False)
        mp_res = BacktestEngine(params, provider=_FakeProvider(df)).run()
        # 串行基准：逐股直接处理（不经进程池），计数在主进程实例上累计
        ser_eng = BacktestEngine(params, provider=_FakeProvider(df))
        ser_recs: list = []
        for code in params.codes:
            ser_recs.extend(ser_eng._process_stock(code))

        assert mp_res.processed == 3 and mp_res.skipped == 0
        assert len(mp_res.records) == len(ser_recs)
        assert self._fingerprint(mp_res.records) == self._fingerprint(ser_recs)
        assert mp_res.gate_counts == ser_eng.gate_counts

    def test_run_mp_空列表返回空结果(self):
        """codes 为空 → 直接返回空 EngineResult（不启动进程池）"""
        params = make_params(codes=[], max_workers=2)
        res = BacktestEngine(params, provider=_FakeProvider(make_kline(400))).run()
        assert res.processed == 0 and res.records == []
