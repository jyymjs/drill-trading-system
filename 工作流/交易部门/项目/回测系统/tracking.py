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
    prbook_warn: str | None = None  # C1 财报日避让（2026-08-05 老板拍板）：
    #   持仓期内跨过预约披露日 → 警示文本（如 "2026-08-20（报告期 2026-06-30）"）；
    #   第一层设计：只警示不强制平仓。None = 无披露日警示。


def _find_signal_index(df: pd.DataFrame, signal_date: pd.Timestamp) -> int:
    """按日期定位信号日在基础K线中的行索引（确定性：取首个匹配）"""
    dates = df["日期"].values
    for i, d in enumerate(dates):
        if pd.Timestamp(d) == signal_date:
            return i
    raise KeyError(f"信号日 {signal_date} 不在 {len(df)} 行K线中")


# ── 交易成本（2026-08-04 老板确认费率；D2 倍率扩展见下）──
# 股票：佣金 万1.3（最低 1 元）+ 印花税 卖出 万5（ETF 免）
COMMISSION = 0.00013
STAMP = 0.0005
# 滑点基线 万1（2026-08-05 D2 方案引入：基线回测不含滑点，2 倍成本压力下按倍率翻倍计入）
SLIPPAGE = 0.0001


def _trade_cost(entry: float, exit_price: float, enable: bool, multiplier: float = 1.0) -> float:
    """单笔交易成本（元/股口径：按成交金额比例折算）

    Args:
        entry: 进场价
        exit_price: 出场价
        enable: 是否启用成本模型
        multiplier: 成本倍率（D2 压力测试=2.0；1.0=基线佣金+印花税）

    Returns:
        每股成本（用于 R 倍数扣减）

    设计依据（方案 D 类 2026-08-05 老板拍板）：
      - 基线（multiplier=1.0）：佣金 万1.3 + 印花税 万5，与 sim_capital/calc_trade_fee 口径一致，无滑点；
      - 压力（multiplier=2.0）：佣金+印花税 ×2（万2.6+万10），滑点随倍率翻倍（万1 → 万2）；
        数学上等价于"基线口径（含滑点 万1）×2"，压力只增不减。
    """
    if not enable:
        return 0.0
    buy_fee = entry * COMMISSION
    sell_fee = exit_price * (COMMISSION + STAMP)
    cost = (buy_fee + sell_fee) * multiplier
    if multiplier > 1.0:
        # D2 压力：滑点翻倍计入（万1 × multiplier → 万2 单边）
        cost += (entry + exit_price) * SLIPPAGE * multiplier
    return round(cost, 6)


def track_signal(signal: Signal, df: pd.DataFrame, hold: int, enable_cost: bool = True,
                 cost_multiplier: float = 1.0, moving_stop: bool = False) -> Outcome:
    """跟踪一笔信号在 hold 个交易日内的出场

    Args:
        signal: 信号（含 T 收盘/trigger/stop/risk）
        df: 该股完整基础K线（日期/开盘/收盘/最高/最低/成交量）
        hold: 观察窗长度（交易日）
        enable_cost: 是否计入交易成本（佣金+印花税）
        cost_multiplier: 成本倍率（D2 2倍成本压力测试用，2026-08-05）
        moving_stop: C5 移动止损开关（2026-08-05 老板拍板）：
            持仓中每确认"新结构低点"（买入后新高之后的回调低点）→ 止损上移到 低点×0.99；
            日线收盘判定（信号日视角）。默认关 = 现有「止损+hold到期收盘」行为。

    Returns:
        Outcome（预突破未触发时 triggered=False，不参与统计）
    """
    t = _find_signal_index(df, signal.date)
    n = len(df)
    end = min(t + hold, n - 1)            # hold 窗口末（数据边界截断）
    if t + 1 > end:                       # 信号日在数据末端，无跟踪空间
        return Outcome(hold, True, signal.close, signal.close, signal.date, False, 0.0)

    if signal.mode == "normal":
        return _track_normal(signal, df, t, end, hold, enable_cost, cost_multiplier, moving_stop)
    return _track_prebreak(signal, df, t, end, hold, enable_cost, cost_multiplier, moving_stop)


# ── C5 移动止损（2026-08-05 老板拍板 · 方案 C5 · 先回测后上线）──
# 出处：知识库《价格行为学入门·04 突破单和移动止损篇》（线索c 2026-07-24）核心方法
#   "把止损不断移动到重要的低点——每确认一个新的结构低点（台阶式抬高），就把止损抬到它下方一点"；
#   知识库《出场体系·六层出场》第 3 层移动获利硬规则："移动获利点必须在进场位正向"
#   （做多新止损必须高于进场价，2023-03-04 老师原话，2026-08-04 补齐）。
# 判定口径（C5 定案）：日线收盘判定（信号日视角）；先回测验证后上线，不直接改生产出场行为。

def _track_window(high, low, close, dates, start, end, entry, stop,
                  enable_cost: bool, cost_multiplier: float, moving_stop: bool):
    """窗口内出场跟踪（含 C5 移动止损可选模式）

    无移动止损（moving_stop=False，现有行为）：
        逐日检查 最低≤止损 → 以止损价出场；否则 hold 末收盘出场。
    移动止损（moving_stop=True，C5 定案）：
        持仓中维护"最高价"与"候选结构低点"，逐日收盘判定：
          ① 候选确认：昨日候选低点 今日最低不再创新低（low[j] > low[cand]）→ 结构低点确认，
             止损上移到 低点×0.99（须高于当前止损 且 高于进场价——六层第3层正向硬规则）；
          ② 止损检查：当日最低 ≤ 当前止损 → 以止损价出场；
          ③ 新高更新：最高价刷新（"买入后新高"结构前提）；
          ④ 候选更新：已创新高后 当日创回调新低 → 记为候选（连续下跌逐日更新到最低点，
             直到某日不再创新低即确认）。
        无前视：所有判定只用 ≤ j 日数据（候选确认在 j 日收盘后完成）。

    Returns:
        (exit_price, exit_date, stopped)
    """
    if not moving_stop:
        for j in range(start, end + 1):
            if low[j] <= stop:
                return stop, pd.Timestamp(dates[j]), True
        return close[end], pd.Timestamp(dates[end]), False

    highest = entry          # 持仓期最高价（初始=进场价，"买入后新高"从此算起）
    candidate = None         # 候选结构低点索引（需次日不再创新低才确认）
    for j in range(start, end + 1):
        # ① 候选确认（j 日收盘判定：不再创新低 → 结构低点成立）
        if candidate is not None and low[j] > low[candidate]:
            new_stop = round(low[candidate] * 0.99, 2)   # 低点下方 ×0.99 缓冲
            if new_stop > stop and new_stop > entry:      # 高于当前止损 且 进场位正向（硬规则）
                stop = new_stop
            candidate = None
        # ② 止损检查（当日最低触及 → 以当前止损价出场）
        if low[j] <= stop:
            return stop, pd.Timestamp(dates[j]), True
        # ③ 新高更新（结构前提：必须有过买入后新高，回调低点才算"新结构低点"）
        highest = max(highest, high[j])
        # ④ 候选更新：已创新高后 当日创回调新低（无候选比昨日低，有候选比候选低）
        if highest > entry and low[j] < (low[candidate] if candidate is not None else low[j - 1]):
            candidate = j
    return close[end], pd.Timestamp(dates[end]), False


def _track_normal(signal: Signal, df: pd.DataFrame, t: int, end: int, hold: int,
                  enable_cost: bool = True, cost_multiplier: float = 1.0,
                  moving_stop: bool = False) -> Outcome:
    """normal：T 收盘进场，窗口内跟踪出场（C5 移动止损可选）"""
    entry = signal.close
    stop = signal.stop
    high = df["最高"].values
    low = df["最低"].values
    close = df["收盘"].values
    dates = df["日期"].values

    exit_price, exit_date, stopped = _track_window(
        high, low, close, dates, t + 1, end, entry, stop,
        enable_cost, cost_multiplier, moving_stop)

    cost = _trade_cost(entry, exit_price, enable_cost, cost_multiplier)
    r = (exit_price - entry - cost) / signal.risk if signal.risk > 0 else 0.0
    return Outcome(hold, True, entry, round(float(exit_price), 4), exit_date, stopped, round(float(r), 4))


def _track_prebreak(signal: Signal, df: pd.DataFrame, t: int, end: int, hold: int,
                    enable_cost: bool = True, cost_multiplier: float = 1.0,
                    moving_stop: bool = False) -> Outcome:
    """prebreak：窗口内首根 最高≥trigger 才进场（触发价成交）；触发后跟踪出场（C5 移动止损可选）"""
    trigger = signal.trigger
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
    # 2) 触发后跟踪出场（触发日次日 ~ hold 末；移动止损同 normal 口径）
    exit_price, exit_date, stopped = _track_window(
        high, low, close, dates, trig_idx + 1, end, entry, signal.stop,
        enable_cost, cost_multiplier, moving_stop)

    cost = _trade_cost(entry, exit_price, enable_cost, cost_multiplier)
    r = (exit_price - entry - cost) / risk if risk > 0 else 0.0
    return Outcome(hold, True, round(float(entry), 4), round(float(exit_price), 4), exit_date, stopped, round(float(r), 4))
