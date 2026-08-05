"""B1 环境闸门 + C3 量能过滤 单元测试（2026-08-05 优化方案第 3 波）

覆盖验收点（任务验收标准 1）：
  - 指数跌幅阈值触发/不触发（含边界 -2.0 整）
  - veto/downgrade 两模式
  - 无量判定边界（4999.99万→否决 / 5000万→放行）
  - 指数数据缺失策略（pass/veto）
  - index_data 缓存读写与排序（monkeypatch 网络，单测不联网）
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 分析决策.市场环境.gate import (  # noqa: E402
    MarketGateConfig, gate_verdict, volume_verdict, exec_verdict,
    index_pct_on,
)
from 分析决策.市场环境.index_data import (  # noqa: E402
    load_index_daily, _bars_to_cn, _cache_path,
)


# ── 测试数据工厂 ──


def make_index_df(dates: list[str], pcts: list[float]) -> pd.DataFrame:
    """构造指数日线（日期 + 涨跌幅）"""
    return pd.DataFrame({"日期": pd.to_datetime(dates), "涨跌幅": pcts,
                         "收盘": 3000.0})


def make_window(amounts: list[float]) -> pd.DataFrame:
    """构造截断 K 线窗口（仅成交额列有用，单位元）"""
    n = len(amounts)
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="B"),
        "收盘": [10.0] * n, "最高": [10.2] * n, "最低": [9.8] * n,
        "成交额": amounts,
    })


def cfg(**kw) -> MarketGateConfig:
    defaults = dict(enabled=True, index="上证指数", drop_pct=-2.0, mode="veto",
                    volume_filter=False, min_amount=5000.0, vol_window=5,
                    missing_index="pass")
    defaults.update(kw)
    return MarketGateConfig(**defaults)


SIG_DATE = pd.Timestamp("2024-06-03")  # 2024-06-03 周一（交易日）

# ── 指数涨跌幅提取 ──


class TestIndexPctOn:
    def test_正常取到涨跌幅(self):
        df = make_index_df(["2024-06-03"], [-3.2])
        assert index_pct_on(df, SIG_DATE) == pytest.approx(-3.2)

    def test_非交易日返回None(self):
        df = make_index_df(["2024-06-04"], [-1.0])
        assert index_pct_on(df, SIG_DATE) is None

    def test_空表返回None(self):
        assert index_pct_on(pd.DataFrame(), SIG_DATE) is None

    def test_缺失涨跌幅列返回None(self):
        df = pd.DataFrame({"日期": [pd.Timestamp("2024-06-03")]})
        assert index_pct_on(df, SIG_DATE) is None


# ── 环境闸门判定 ──


class TestGateVerdict:
    def test_关闭恒放行(self):
        c = cfg(enabled=False)
        df = make_index_df(["2024-06-03"], [-9.9])
        assert gate_verdict(c, df, SIG_DATE, "S")[0] == "keep"

    def test_指数暴跌触发否决(self):
        c = cfg()
        df = make_index_df(["2024-06-03"], [-2.5])
        action, info = gate_verdict(c, df, SIG_DATE, "S")
        assert action == "veto"
        assert "跌破阈值" in (info or "")

    def test_小幅下跌放行(self):
        c = cfg()
        df = make_index_df(["2024-06-03"], [-1.5])
        assert gate_verdict(c, df, SIG_DATE, "S")[0] == "keep"

    def test_边界_恰好负2不放行(self):
        """阈值语义：跌幅严格跌破（pct < drop_pct）才触发；-2.0 整为边界不触发"""
        c = cfg()
        df = make_index_df(["2024-06-03"], [-2.0])
        assert gate_verdict(c, df, SIG_DATE, "S")[0] == "keep"

    def test_边界_负2_01触发(self):
        c = cfg()
        df = make_index_df(["2024-06-03"], [-2.01])
        assert gate_verdict(c, df, SIG_DATE, "S")[0] == "veto"

    def test_上涨日放行(self):
        c = cfg()
        df = make_index_df(["2024-06-03"], [1.2])
        assert gate_verdict(c, df, SIG_DATE, "S")[0] == "keep"

    def test_降级模式_S变A(self):
        c = cfg(mode="downgrade")
        df = make_index_df(["2024-06-03"], [-3.0])
        action, new_g = gate_verdict(c, df, SIG_DATE, "S")
        assert (action, new_g) == ("downgrade", "A")

    def test_降级模式_逐级(self):
        c = cfg(mode="downgrade")
        df = make_index_df(["2024-06-03"], [-3.0])
        for g, expect in [("S", "A"), ("A", "B"), ("B", "C"), ("C", "C")]:
            action, new_g = gate_verdict(c, df, SIG_DATE, g)
            assert new_g == expect, f"{g} → {new_g}（期望 {expect}）"

    def test_指数数据缺失_pass放行(self):
        c = cfg(missing_index="pass")
        df = make_index_df(["2024-06-04"], [-3.0])  # 信号日无数据
        action, info = gate_verdict(c, df, SIG_DATE, "S")
        assert action == "missing"

    def test_指数数据缺失_veto否决(self):
        c = cfg(missing_index="veto")
        df = make_index_df(["2024-06-04"], [-3.0])
        action, info = gate_verdict(c, df, SIG_DATE, "S")
        assert action == "veto"
        assert "数据缺失" in (info or "")


# ── 量能硬过滤判定 ──


class TestVolumeVerdict:
    def test_关闭恒放行(self):
        c = cfg(volume_filter=False)
        assert volume_verdict(c, make_window([100.0]))[0] == "keep"

    def test_无量否决(self):
        c = cfg(volume_filter=True, min_amount=5000.0)
        # 5日均额 4999.9 万（元）→ 略低于阈值 → 否决
        win = make_window([4_999_9000.0] * 5)
        action, info = volume_verdict(c, win)
        assert action == "veto"
        assert "日均成交额" in (info or "")

    def test_无量否决_断崖式(self):
        c = cfg(volume_filter=True, min_amount=5000.0)
        win = make_window([100_000.0] * 5)  # 日均 10 万元
        assert volume_verdict(c, win)[0] == "veto"

    def test_边界_恰好5000万放行(self):
        c = cfg(volume_filter=True, min_amount=5000.0)
        win = make_window([50_000_000.0] * 5)
        assert volume_verdict(c, win)[0] == "keep"

    def test_放量放行(self):
        c = cfg(volume_filter=True, min_amount=5000.0)
        win = make_window([5e8, 6e8, 7e8, 8e8, 9e8])
        assert volume_verdict(c, win)[0] == "keep"

    def test_窗口截取_只用近N日(self):
        """窗口=5：前面暴涨不算，近 5 日无量 → 否决"""
        c = cfg(volume_filter=True, min_amount=5000.0)
        win = make_window([5e8] * 20 + [100_000.0] * 5)
        assert volume_verdict(c, win)[0] == "veto"

    def test_无成交额列_缺口放行(self):
        c = cfg(volume_filter=True, min_amount=5000.0)
        df = pd.DataFrame({"日期": [SIG_DATE], "收盘": [10.0]})
        action, info = volume_verdict(c, df)
        assert action == "missing"

    def test_成交额全零_缺口放行(self):
        c = cfg(volume_filter=True, min_amount=5000.0)
        win = make_window([0.0] * 5)
        assert volume_verdict(c, win)[0] == "missing"


# ── 汇总判定（引擎挂载点） ──


class TestExecVerdict:
    def test_全关放行(self):
        c = cfg(enabled=False, volume_filter=False)
        assert exec_verdict(c, None, SIG_DATE, "S", None)[0] == "keep"

    def test_环境否决优先于量能(self):
        c = cfg(enabled=True, volume_filter=True)
        idx = make_index_df(["2024-06-03"], [-3.0])
        win = make_window([100.0] * 5)  # 无量
        action, info, src = exec_verdict(c, idx, SIG_DATE, "S", win)
        assert (action, src) == ("veto", "env")

    def test_环境放行_量能否决(self):
        c = cfg(enabled=True, volume_filter=True)
        idx = make_index_df(["2024-06-03"], [-1.0])
        win = make_window([100.0] * 5)
        action, info, src = exec_verdict(c, idx, SIG_DATE, "S", win)
        assert (action, src) == ("veto", "volume")

    def test_降级模式_降级且量能过(self):
        c = cfg(enabled=True, mode="downgrade", volume_filter=True)
        idx = make_index_df(["2024-06-03"], [-3.0])
        win = make_window([5e8] * 5)
        action, new_g, src = exec_verdict(c, idx, SIG_DATE, "S", win)
        assert (action, new_g, src) == ("downgrade", "A", "env")

    def test_降级模式_量能不过则否决(self):
        c = cfg(enabled=True, mode="downgrade", volume_filter=True)
        idx = make_index_df(["2024-06-03"], [-3.0])
        win = make_window([100.0] * 5)
        action, info, src = exec_verdict(c, idx, SIG_DATE, "S", win)
        assert (action, src) == ("veto", "volume")

    def test_指数缺口_pass放行(self):
        c = cfg(enabled=True, missing_index="pass")
        idx = make_index_df(["2024-06-04"], [-3.0])
        action, info, src = exec_verdict(c, idx, SIG_DATE, "S", make_window([5e8] * 5))
        assert (action, src) == ("missing", "env")


# ── 指数数据层（缓存优先，monkeypatch 网络不实拉） ──


class TestIndexData:
    def _write_cache(self, tmp_path, market=1, code="000001", dates=None):
        """写一份测试缓存 CSV 并返回 path"""
        path = _cache_path(market, code, tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dates = dates or ["2024-01-02", "2024-01-03", "2024-01-04"]
        df = pd.DataFrame({
            "日期": pd.to_datetime(dates),
            "开盘": [3000.0] * 3, "收盘": [3000.0] * 3,
            "最高": [3010.0] * 3, "最低": [2990.0] * 3,
            "成交量": [1e8] * 3, "成交额": [3e11] * 3,
            "涨跌幅": [0.0, 0.5, -0.3],
        })
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def test_缓存命中_直接返回(self, tmp_path, monkeypatch):
        self._write_cache(tmp_path)
        monkeypatch.setattr("分析决策.市场环境.index_data._pull_index_all",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("不应联网")))
        df = load_index_daily("上证指数", cache_dir=tmp_path)
        assert len(df) == 3
        assert df["涨跌幅"].iloc[-1] == pytest.approx(-0.3)

    def test_min_date未覆盖_触发拉取(self, tmp_path, monkeypatch):
        """缓存起点晚于 min_date → 重新拉取（monkeypatch 假网络）"""
        self._write_cache(tmp_path, dates=["2024-06-01", "2024-06-02", "2024-06-03"])
        fake = pd.DataFrame({
            "日期": pd.to_datetime(["2021-01-04", "2021-01-05"]),
            "开盘": [3000.0, 3001.0], "收盘": [3000.0, 3001.0],
            "最高": [3010.0, 3011.0], "最低": [2990.0, 2991.0],
            "成交量": [1e8, 1e8], "成交额": [3e11, 3e11],
            "涨跌幅": [0.0, 0.03],
        })
        monkeypatch.setattr("分析决策.市场环境.index_data._pull_index_all",
                            lambda *a, **k: fake.copy())
        df = load_index_daily("上证指数", cache_dir=tmp_path, min_date="20210101")
        assert df["日期"].min() == pd.Timestamp("2021-01-04")
        # 重拉结果已回写缓存
        assert _cache_path(1, "000001", tmp_path).exists()

    def test_未知指数报错(self, tmp_path):
        with pytest.raises(ValueError):
            load_index_daily("不存在指数", cache_dir=tmp_path)

    def test_bars_to_cn_倒序去重(self):
        """pytdx 分段返回需规范化：乱序 → 升序、去重、算涨跌幅"""
        rows = [
            {"datetime": "2024-01-03 15:00", "open": 10, "close": 10.5,
             "high": 10.6, "low": 9.9, "vol": 100, "amount": 1e9},
            {"datetime": "2024-01-03 15:00", "open": 10, "close": 10.5,
             "high": 10.6, "low": 9.9, "vol": 100, "amount": 1e9},  # 重复
            {"datetime": "2024-01-02 15:00", "open": 9.8, "close": 10.0,
             "high": 10.1, "low": 9.7, "vol": 90, "amount": 9e8},
        ]
        df = _bars_to_cn(rows)
        assert list(df["日期"]) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
        assert df["涨跌幅"].iloc[1] == pytest.approx(5.0)

    def test_配置校验(self):
        with pytest.raises(ValueError):
            cfg(drop_pct=1.0).validate()      # 阈值必须负值
        with pytest.raises(ValueError):
            cfg(mode="xyz").validate()
        with pytest.raises(ValueError):
            cfg(min_amount=0).validate()
        with pytest.raises(ValueError):
            cfg(vol_window=0).validate()
        with pytest.raises(ValueError):
            cfg(missing_index="xyz").validate()
        cfg().validate()  # 默认配置合法
