"""信号跟踪：hold 窗内出场价/R 计算

方法学口径（计划"待老板确认的默认值"）：
  - normal  进场 = 信号日 T 收盘价；止损 = max(2×ATR14, 2%×进场价)（engine 经 RiskModel 计算）
  - prebreak 进场 = 触发价（信号日之后首根最高≥trigger 才进场）；止损 = 策略原生 stop_loss
  - 出场简化：v1 仅"止损 + hold 到期收盘"两种（出场六层体系留后续版本）
  - prebreak 未触发：计信号数/触发率，不参与胜率/平均R/回撤
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Signal:
    """一笔信号（信号日 T 的评级结果 + 进出场参数）"""

    code: str
    date: pd.Timestamp        # 信号日
    mode: str                 # normal | prebreak
    grade: str                # S/A/B
    scores: dict              # 6条件 → (等级, 说明)
    close: float              # 信号日收盘价
    trigger: float            # prebreak 触发价（normal=0）
    stop: float               # 止损价（normal=ATR止损, prebreak=策略原生）
    risk: float               # 每股风险（normal=进场-止损, prebreak=策略原生）

    SCORE_KEYS = ("PT平台测试", "TY统一区间", "DN动能", "DL独立结构", "LK轮廓质量", "SF释放级别")

    def score_grade(self, key: str) -> str:
        """提取六条件单项等级（缺省 C）"""
        item = self.scores.get(key)
        if isinstance(item, (tuple, list)) and item:
            return str(item[0])
        return "C"


@dataclass
class Outcome:
    """单 hold 跟踪结果"""

    hold: int
    triggered: bool               # prebreak 是否触发（normal 恒 True）
    entry_price: float
    exit_price: float
    exit_date: pd.Timestamp | None
    stopped: bool                 # 是否止损出场（False=hold 到期收盘）
    r: float                      # 倍数R

    def participate(self) -> bool:
        """是否参与统计（normal 全参与；prebreak 仅触发者参与）"""
        return self.triggered


@dataclass
class TrackedRecord:
    """信号 + 各 hold 跟踪结果"""

    signal: Signal
    outcomes: dict[int, Outcome] = field(default_factory=dict)


def _find_signal_index(df: pd.DataFrame, signal_date: pd.Timestamp) -> int:
    """按日期定位信号日在基础K线中的行索引（确定性：取首个匹配）"""
    dates = df["日期"].values
    for i, d in enumerate(dates):
        if pd.Timestamp(d) == signal_date:
            return i
    raise KeyError(f"信号日 {signal_date} 不在 {len(df)} 行K线中")


# ── 交易成本（2026-08-04 老板确认费率）──
# 股票：佣金 万1.3（最低 1 元）+ 印花税 卖出 万5（ETF 免）
COMMISSION = 0.00013
STAMP = 0.0005


def _trade_cost(entry: float, exit_price: float, enable: bool) -> float:
    """单笔交易成本（元/股口径：按成交金额比例折算）

    Args:
        entry: 进场价
        exit_price: 出场价
        enable: 是否启用成本模型

    Returns:
        每股成本（用于 R 倍数扣减）
    """
    if not enable:
        return 0.0
    buy_fee = entry * COMMISSION
    sell_fee = exit_price * (COMMISSION + STAMP)
    return round(buy_fee + sell_fee, 6)


def track_signal(signal: Signal, df: pd.DataFrame, hold: int, enable_cost: bool = True) -> Outcome:
    """跟踪一笔信号在 hold 个交易日内的出场

    Args:
        signal: 信号（含 T 收盘/trigger/stop/risk）
        df: 该股完整基础K线（日期/开盘/收盘/最高/最低/成交量）
        hold: 观察窗长度（交易日）
        enable_cost: 是否计入交易成本（佣金+印花税）

    Returns:
        Outcome（预突破未触发时 triggered=False，不参与统计）
    """
    t = _find_signal_index(df, signal.date)
    n = len(df)
    end = min(t + hold, n - 1)            # hold 窗口末（数据边界截断）
    if t + 1 > end:                       # 信号日在数据末端，无跟踪空间
        return Outcome(hold, True, signal.close, signal.close, signal.date, False, 0.0)

    if signal.mode == "normal":
        return _track_normal(signal, df, t, end, hold, enable_cost)
    return _track_prebreak(signal, df, t, end, hold, enable_cost)


def _track_normal(signal: Signal, df: pd.DataFrame, t: int, end: int, hold: int,
                  enable_cost: bool = True) -> Outcome:
    """normal：T 收盘进场，窗口内 最低≤止损 → 止损出场；否则 hold 末收盘出场"""
    entry = signal.close
    stop = signal.stop
    low = df["最低"].values
    close = df["收盘"].values
    dates = df["日期"].values

    for j in range(t + 1, end + 1):
        if low[j] <= stop:
            exit_price = stop
            exit_date = pd.Timestamp(dates[j])
            stopped = True
            break
    else:
        exit_price = close[end]
        exit_date = pd.Timestamp(dates[end])
        stopped = False

    cost = _trade_cost(entry, exit_price, enable_cost)
    r = (exit_price - entry - cost) / signal.risk if signal.risk > 0 else 0.0
    return Outcome(hold, True, entry, round(float(exit_price), 4), exit_date, stopped, round(float(r), 4))


def _track_prebreak(signal: Signal, df: pd.DataFrame, t: int, end: int, hold: int,
                    enable_cost: bool = True) -> Outcome:
    """prebreak：窗口内首根 最高≥trigger 才进场（触发价成交）；触发后 最低≤stop → 止损，否则 hold 末收盘"""
    trigger = signal.trigger
    stop = signal.stop
    risk = signal.risk
    high = df["最高"].values
    low = df["最低"].values
    close = df["收盘"].values
    dates = df["日期"].values

    # 1) 找触发日（首根最高≥trigger）
    trig_idx = None
    for j in range(t + 1, end + 1):
        if high[j] >= trigger:
            trig_idx = j
            break
    if trig_idx is None:
        return Outcome(hold, False, 0.0, 0.0, None, False, 0.0)   # 未触发：不参与统计

    entry = trigger
    # 2) 触发后跟踪出场（触发日次日 ~ hold 末）
    for j in range(trig_idx + 1, end + 1):
        if low[j] <= stop:
            exit_price = stop
            exit_date = pd.Timestamp(dates[j])
            stopped = True
            break
    else:
        exit_price = close[end]
        exit_date = pd.Timestamp(dates[end])
        stopped = False

    cost = _trade_cost(entry, exit_price, enable_cost)
    r = (exit_price - entry - cost) / risk if risk > 0 else 0.0
    return Outcome(hold, True, round(float(entry), 4), round(float(exit_price), 4), exit_date, stopped, round(float(r), 4))
