"""持仓数据模型"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    """单个持仓记录

    追踪一笔交易从开仓到平仓的全生命周期。
    """
    symbol: str                     # 股票代码
    name: str = ""                  # 股票名称
    direction: str = "long"         # long / short
    entry_price: float = 0.0        # 进场价
    entry_time: Optional[datetime] = None  # 进场时间
    volume: int = 0                 # 股数
    initial_stop: float = 0.0       # 原始止损价
    current_stop: float = 0.0       # 当前止损价（动态更新）
    highest_price: float = 0.0      # 持有期最高价
    lowest_price: float = 1e9       # 持有期最低价
    ty_high: float = 0.0            # TY统一区间上沿
    ty_low: float = 0.0             # TY统一区间下沿
    grade_at_entry: str = ""        # 进场时评级
    status: str = "open"            # open / closed
    exit_price: float = 0.0         # 平仓价
    exit_time: Optional[datetime] = None  # 平仓时间
    exit_reason: str = ""           # 离场原因
    entry_fee: float = 0.0          # 进场手续费
    exit_fee: float = 0.0           # 出场手续费

    def risk_per_share(self) -> float:
        """每股风险 = 进场价 - 原始止损（多头）"""
        if self.direction == "long":
            return self.entry_price - self.initial_stop
        return self.initial_stop - self.entry_price

    def total_risk(self) -> float:
        """总风险 = 每股风险 × 股数"""
        return self.risk_per_share() * self.volume

    def current_r_multiple(self, current_price: float) -> float:
        """当前盈亏比倍数（R倍数）"""
        risk = self.risk_per_share()
        if risk <= 0:
            return 0.0
        if self.direction == "long":
            return (current_price - self.entry_price) / risk
        return (self.entry_price - current_price) / risk

    def update_price(self, high: float, low: float, close: float) -> None:
        """用新K线更新价格极值"""
        self.highest_price = max(self.highest_price, high)
        self.lowest_price = min(self.lowest_price, low)

    def close_position(self, exit_price: float, reason: str = "") -> float:
        """平仓，返回盈亏金额"""
        self.exit_price = exit_price
        self.exit_time = datetime.now()
        self.exit_reason = reason
        self.status = "closed"

        if self.direction == "long":
            pnl = (exit_price - self.entry_price) * self.volume
        else:
            pnl = (self.entry_price - exit_price) * self.volume
        return pnl

    def summary(self) -> dict:
        """持仓摘要"""
        risk = self.risk_per_share()
        r = self.current_r_multiple(self.exit_price if self.status == "closed" else self.highest_price)
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": self.entry_price,
            "current_stop": self.current_stop,
            "risk_per_share": round(risk, 2),
            "r_multiple": round(r, 2),
            "status": self.status,
            "pnl": round((self.exit_price - self.entry_price) * self.volume, 2) if self.status == "closed" else 0,
        }


@dataclass
class TradeRecord:
    """完成的交易记录（用于持久化到 CSV）"""
    trade_id: str
    symbol: str
    name: str
    direction: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    volume: int
    stop_loss: float
    r_multiple: float
    pnl: float
    grade_at_entry: str
    exit_reason: str
