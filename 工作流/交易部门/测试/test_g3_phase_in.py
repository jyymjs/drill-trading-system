"""G3 分步建仓测试（2026-08-06 · 2024-06-29 周会原文定案）

定案语义：0.5R = 分步建仓的第一步（非终局减半）——先进 0.5R → 下一根收线
确认（收下去/动能接受）→ 再补 0.5R（总 1R）；收线不确认 → 马上平仓
（"觉得优势不突出，动能无法接受，就马上平仓了"）。
原文（周会录屏/raw/2024-06-29周会.txt）：「先进个二分之一，然后等下一个收线，
比如说收下去，我再进二分之一」「你0.5R是百分之百，因为认为前面有问题才做0.5R」。

覆盖三层：
1. indicators.half_position_confirm 确认规则（C1 收下去 / C2 动能延续 / C3 非放量阴线 / 止损优先）
2. sim_trading 模拟层全链路（0.5R 起步 → 确认补仓 / 不确认平仓 / 触止损 / 等待）
3. tracking 回测层 phase_in（prebreak/normal 分步确认，默认关=现有行为）
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

from 回测系统.tracking import Signal, track_signal

from 分析决策.分析.indicators import half_position_confirm
from 分析决策.跟踪 import sim_trading
from 分析决策.风控 import capital

# ──────────────────────────────────────────────
# 数据构造辅助
# ──────────────────────────────────────────────

def mk_kline(rows: list[tuple]) -> pd.DataFrame:
    """构造 K 线 DataFrame：rows = [(开盘, 最高, 最低, 收盘, 成交量), ...]"""
    return pd.DataFrame({
        "日期": pd.bdate_range("2025-01-01", periods=len(rows)),
        "开盘": [r[0] for r in rows],
        "最高": [r[1] for r in rows],
        "最低": [r[2] for r in rows],
        "收盘": [r[3] for r in rows],
        "成交量": [r[4] if len(r) > 4 else 1_000_000 for r in rows],
    })


# 固定开仓日 = 2025-01-06（周一，df 索引 3）；确认日 = 2025-01-07（索引 4）
_OPEN_IDX = 3
_OPEN_DATE = "2025-01-06"
_CONFIRM_DATE = "2025-01-07"


class _FakeNow:
    """固定系统时钟（2025-01-06 周一）：sim_open 记录日期与测试 df 对齐"""

    @staticmethod
    def now():
        return pd.Timestamp("2025-01-06 09:30:00")


# ──────────────────────────────────────────────
# 1. indicators.half_position_confirm 确认规则
# ──────────────────────────────────────────────

class TestHalfPositionConfirm:
    def test_confirm_close_above_entry(self):
        """C1+C2 满足（收下去 + 动能延续），无放量阴线 → 确认"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),   # 开仓日前
            (9.95, 10.15, 9.9, 10.1, 1e6),  # 开仓日（收 10.1）
            (10.2, 10.5, 10.1, 10.4, 1e6),  # 确认日：收 10.4 ≥ 进 10.1 且 ≥ 开仓日收 10.1，阳线
        ])
        v = half_position_confirm(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert v["confirmed"] and not v["reject"] and not v["stopped"]
        assert v["close"] == 10.4

    def test_reject_close_below_entry(self):
        """收线跌破进场价（没收下去）→ 不确认 → 平仓"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),  # 开仓日（收 10.1）
            (10.0, 10.2, 9.92, 10.02, 1e6),  # 确认日：收 10.02 < 进 10.1（最低 9.92 > 止损 9.9）
        ])
        v = half_position_confirm(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert v["reject"] and not v["confirmed"]
        assert "跌破进场价" in v["reason"]

    def test_reject_weaker_than_open_day(self):
        """收盘未跌破进场价但较开仓日转弱（动能延续缺失）→ 不确认"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (10.05, 10.25, 10.0, 10.2, 1e6),  # 开仓日（收 10.2）
            (10.1, 10.4, 10.05, 10.15, 1e6),  # 确认日：收 10.15 ≥ 进 10.1 但 < 开仓日收 10.2
        ])
        v = half_position_confirm(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert v["reject"]
        assert "转弱" in v["reason"]

    def test_reject_volume_down_bar(self):
        """放量阴线（量比>1.5 且收阴 = 动能拒绝形态）→ 不确认"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),
            (9.9, 10.3, 9.85, 9.95, 1e6),
            (10.0, 10.2, 9.9, 10.05, 1e6),   # 确认日前（前 5 日均量 1e6）
            (10.05, 10.25, 10.0, 10.2, 1e6),  # 开仓日
            (10.4, 10.6, 10.0, 10.05, 2e6),   # 确认日：量 2e6 > 1.5e6 且收阴 → 拒绝
        ])
        v = half_position_confirm(df.iloc[:6], entry_price=10.2, stop_loss=9.9)
        assert v["reject"]
        assert "放量阴线" in v["reason"]

    def test_stop_priority(self):
        """确认日最低 ≤ 止损价 → 层面1 止损优先（即使收盘在进场价上方）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),  # 开仓日
            (10.3, 10.6, 9.85, 10.5, 1e6),  # 确认日：收 10.5 ≥ 进 10.1，但最低 9.85 ≤ 止损 9.9
        ])
        v = half_position_confirm(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert v["stopped"] and not v["confirmed"] and not v["reject"]
        assert "止损触发" in v["reason"]

    def test_wait_insufficient_bars(self):
        """只有开仓日一根（收线未出现）→ wait，继续持有"""
        df = mk_kline([(9.95, 10.15, 9.9, 10.1, 1e6)])
        v = half_position_confirm(df.iloc[:1], entry_price=10.1, stop_loss=9.9)
        assert v["wait"] and not v["confirmed"] and not v["reject"]


# ──────────────────────────────────────────────
# 2. sim_trading 模拟层全链路
# ──────────────────────────────────────────────

@pytest.fixture
def sim_env(monkeypatch, tmp_path):
    """隔离 journal + 固定时钟 + 固定资金（G9：5600 × 2% = 112）"""
    monkeypatch.setattr(sim_trading, "datetime", _FakeNow)
    monkeypatch.setattr(sim_trading, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(sim_trading, "SIM_FILE", tmp_path / "sim_journal.csv")
    monkeypatch.setattr(capital, "get_capital", lambda: 5600)
    monkeypatch.setattr(capital, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(capital, "CAPITAL_FILE", tmp_path / "capital.json")
    sim_trading._day_env_cache.clear()
    yield
    sim_trading._day_env_cache.clear()


def _open_half(monkeypatch, kline: pd.DataFrame, price: float = 10.1, stop: float = 9.9,
               vol0: int = 200):
    """开一笔 0.5R 分步起步仓（手动 risk_scale=0.5，绕过环境判定链）

    股数基线（G9 2%）：0.5R 风险额 = 5600×2%×0.5 = 56 元；每股风险 0.2 元
    → 56//0.2 = 280 → 整手 200 股。
    """
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda c, use_cache=True: kline)
    out = sim_trading.sim_open("000001", price=price, stop=stop, name="测试股",
                               risk_scale=0.5)
    assert "0.5R" in out and "分步" in out
    rows = sim_trading._read_all()
    assert len(rows) == 1 and rows[0]["phase"] == "half"
    assert int(rows[0]["volume"]) == vol0
    return rows[0]


class TestSimTradingPhaseIn:
    def test_open_half_marks_phase(self, sim_env, monkeypatch, tmp_path):
        """0.5R 起步 → journal phase="half"（分步待确认）；1R 直接仓 phase 为空"""
        kline = mk_kline([(9.9, 10.1, 9.8, 10.0, 1e6)] * 6)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda c, use_cache=True: kline)
        out1 = sim_trading.sim_open("000001", price=10.1, stop=9.9, name="A", risk_scale=0.5)
        out2 = sim_trading.sim_open("000002", price=10.1, stop=9.9, name="B", risk_scale=1.0)
        assert "分步起步" in out1
        assert "分步" not in out2
        rows = sim_trading._read_all()
        assert rows[0]["phase"] == "half"
        assert rows[1]["phase"] == ""

    def test_confirm_adds_half(self, sim_env, monkeypatch, tmp_path):
        """确认收线（收下去/动能接受）→ 补 0.5R：volume 翻倍、phase→confirmed、
        entry_price = 两笔加权平均（R 基准 = 总股数 × 每股风险）"""
        kline = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),   # 开仓日 2025-01-06（收 10.1）
            (10.2, 10.5, 10.1, 10.4, 1e6),   # 确认日 2025-01-07：收 10.4 ≥ 10.1 且阳线
        ])
        _open_half(monkeypatch, kline)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda c, use_cache=True: kline)
        out = sim_trading.sim_check()
        assert "分步确认" in out and "补 0.5R" in out
        rows = sim_trading._read_all()
        r = rows[0]
        assert r["status"] == "open" and r["phase"] == "confirmed"
        # 起步 200 股（0.5R：56 元风险额 ÷ 每股风险 0.2 = 280 → 整手 200）；
        # 补仓等额 200 股 → 总 400 股（= 1R 风险预算 112 元）
        assert int(r["volume"]) == 400
        # 补仓价 10.4：加权平均 = (10.1×200 + 10.4×200)/400 = 10.25
        assert float(r["entry_price"]) == pytest.approx(10.25)

    def test_reject_exits_half(self, sim_env, monkeypatch, tmp_path):
        """收线未确认（收盘跌破进场价）→ 0.5R 马上平仓（按确认日收盘价）"""
        kline = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),   # 开仓日
            (10.0, 10.2, 9.92, 10.02, 1e6),   # 确认日：收 10.02 < 进 10.1（最低 9.92 > 止损）→ 不确认
        ])
        _open_half(monkeypatch, kline)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda c, use_cache=True: kline)
        out = sim_trading.sim_check()
        assert "分步建仓收线未确认" in out
        rows = sim_trading._read_all()
        r = rows[0]
        assert r["status"] == "closed"
        assert "分步" in r["exit_reason"] and "未确认" in r["exit_reason"]
        assert float(r["exit_price"]) == pytest.approx(10.02)
        assert r["exit_date"] == _CONFIRM_DATE
        # R = (10.02-10.1-费用)/0.2（半仓公式与全仓一致）
        assert float(r["r_multiple"]) < 0

    def test_stop_exits_half(self, sim_env, monkeypatch, tmp_path):
        """确认日触止损（层面1 优先）→ 按止损价平仓"""
        kline = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),   # 开仓日
            (10.3, 10.6, 9.85, 10.5, 1e6),   # 确认日：最低 9.85 ≤ 止损 9.9
        ])
        _open_half(monkeypatch, kline)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda c, use_cache=True: kline)
        out = sim_trading.sim_check()
        assert "止损触发" in out
        r = sim_trading._read_all()[0]
        assert r["status"] == "closed"
        assert float(r["exit_price"]) == pytest.approx(9.9)

    def test_wait_keeps_open(self, sim_env, monkeypatch, tmp_path):
        """收线未出现（数据只到开仓日）→ 保持持有等待"""
        kline = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),   # 开仓日（无下一根）
        ])
        _open_half(monkeypatch, kline)
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda c, use_cache=True: kline)
        out = sim_trading.sim_check()
        assert "待确认" in out
        r = sim_trading._read_all()[0]
        assert r["status"] == "open" and r["phase"] == "half"


# ──────────────────────────────────────────────
# 3. tracking 回测层 phase_in（prebreak/normal）
# ──────────────────────────────────────────────

def _sig(mode: str = "prebreak", date="2024-01-08", trigger: float = 10.5,
         close: float = 10.0, stop: float = 9.5, risk: float = 1.0) -> Signal:
    return Signal(code="600000", date=pd.Timestamp(date), mode=mode, grade="S",
                  scores={}, close=close, trigger=trigger if mode == "prebreak" else 0.0,
                  stop=stop, risk=risk)


def _kline(closes, highs, lows, vols=None) -> pd.DataFrame:
    n = len(closes)
    vols = vols or [100_000] * n
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="B"),
        "开盘": closes, "收盘": closes, "最高": highs, "最低": lows, "成交量": vols,
    })


class TestTrackingPhaseIn:
    def test_prebreak_confirm_keeps_tracking(self):
        """prebreak：触发后次日收线确认 → 补至 1R 继续跟踪（到期出场）"""
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 11.0,
                  11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9]
        # 信号日 2024-01-08（idx5，close 10.5）；触发日 idx6（high 10.7 ≥ trigger 10.5）；
        # 确认日 idx7（收 10.8 ≥ 进 10.5 且 ≥ 触发日收 10.6）→ 确认 → 持有到 hold 末
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        df = _kline(closes, highs, lows)
        oc = track_signal(_sig(trigger=10.5), df, hold=10, phase_in=True)
        assert oc.triggered and not oc.stopped
        # 确认后续跟踪：到期收盘 idx15 = 11.5
        assert oc.exit_price == closes[5 + 10]

    def test_prebreak_reject_exits_confirm_day(self):
        """prebreak：触发后次日收线未确认（跌破进场价）→ 确认日收盘平仓（0.5R）"""
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.3, 10.4, 10.5, 10.6,
                  10.7, 10.8, 10.9, 11.0, 11.1]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        df = _kline(closes, highs, lows)
        # 信号日 idx5（收 10.5）；触发日 idx6（high 10.8 ≥ trigger 10.5，收 10.6）；
        # 确认日 idx7：收 10.3 < 进 10.5 → 不确认 → 以 10.3 平仓
        oc = track_signal(_sig(trigger=10.5), df, hold=10, phase_in=True)
        assert oc.triggered and not oc.stopped
        assert oc.exit_price == 10.3
        assert oc.exit_date == pd.Timestamp("2024-01-10")
        # R = (10.3 - 10.5 - 费用)/1.0 < 0
        assert oc.r < 0

    def test_prebreak_phase_in_stop_priority(self):
        """prebreak：确认日触止损 → 层面1 止损价出场（stopped=True）"""
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.4, 10.5, 10.6]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        lows[7] = 9.4  # 确认日 idx7 最低 ≤ 止损 9.5 → 止损出场
        df = _kline(closes, highs, lows)
        oc = track_signal(_sig(trigger=10.5), df, hold=10, phase_in=True)
        assert oc.triggered and oc.stopped
        assert oc.exit_price == 9.5

    def test_normal_phase_in_reject(self):
        """normal：T+1 收线未确认 → 确认日收盘平仓（0.5R）"""
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.2, 10.3, 10.4, 10.5, 10.6]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        df = _kline(closes, highs, lows)
        # 信号日 idx5 收盘 10.5 进场；确认日 idx6 收 10.2 < 进 10.5 → 平仓
        oc = track_signal(_sig(mode="normal", close=10.5), df, hold=10, phase_in=True)
        assert oc.triggered and not oc.stopped
        assert oc.exit_price == 10.2
        assert oc.r < 0

    def test_phase_in_default_off_same_as_base(self):
        """phase_in 默认关（False）→ 与现有行为一致（不受分步影响）"""
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.2, 10.3, 10.4, 10.5, 10.6,
                  10.7, 10.8, 10.9, 11.0, 11.1]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        df = _kline(closes, highs, lows)
        base = track_signal(_sig(trigger=10.5), df, hold=10)
        assert base.triggered
        # 未触发 phase_in → 现有行为（触发日 idx6 后跟踪到 hold 末）
        assert base.exit_price == closes[5 + 10]
