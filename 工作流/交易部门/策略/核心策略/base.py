"""策略基类"""
from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    """策略基类

    子类只需实现 filter(df) 方法，返回 True=符合条件
    """

    name: str = "未命名策略"
    description: str = ""

    # 策略所需的指标列（用于按需计算，减少不必要的指标开销）
    # None = 需要全部指标；空列表 = 仅需基础K线列
    required_indicators: list[str] | None = None

    @abstractmethod
    def filter(self, df: pd.DataFrame) -> bool:
        """判断股票是否符合策略条件

        Args:
            df: 包含K线和技术指标的完整DataFrame

        Returns:
            True=符合条件，推荐关注
        """
        ...

    def debug_filter(self, df: pd.DataFrame) -> dict:
        """诊断模式：返回逐步检测结果

        基类默认实现为调用 filter() 并简化输出，
        子类可覆盖此方法提供更详细的诊断信息。

        Args:
            df: 包含K线和技术指标的完整DataFrame

        Returns:
            {"match": bool, "steps": {"条件名": {"passed": bool, "reason": str}}}
        """
        result = self.filter(df)
        return {
            "match": result,
            "steps": {"最终结果": {"passed": result, "reason": ""}},
        }

    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        """快速预过滤：在指标计算前快速判断是否值得继续

        子类可覆盖此方法提供更高效的预过滤逻辑。
        返回 False = 直接跳过（不符合条件），True = 需要继续完整检测。

        Args:
            df: 仅含基础K线列的DataFrame（开盘/收盘/最高/最低/成交量/日期）
        """
        if len(df) < 60:
            return False

        close = df["收盘"].values
        high = df["最高"].values
        low = df["最低"].values

        # 1. 近期波动不能过大（60根K线内波幅不超过50%）
        recent_high = high[-60:].max()
        recent_low = low[-60:].min()
        if (recent_high - recent_low) / close[-1] > 0.50:
            return False

        # 2. 排除完全释放（涨幅过大）——从60日低点起涨幅>40%则排除
        low_60 = low[-60:].min()
        if low_60 > 0 and (close[-1] - low_60) / low_60 > 0.40:
            return False

        # 3. 排除通道上涨（沿着一条斜线慢慢上蹭）
        recent_highs = high[-8:]
        recent_lows = low[-8:]
        high_inc = all(recent_highs[i] <= recent_highs[i + 1] for i in range(min(7, len(recent_highs) - 1)))
        low_inc = all(recent_lows[i] <= recent_lows[i + 1] for i in range(min(7, len(recent_lows) - 1)))
        if high_inc and low_inc:
            # 检查幅度是否过小（狭窄通道）
            total_range = recent_highs.max() - recent_lows.min()
            trend_range = abs(recent_highs[-1] - recent_highs[0])
            if trend_range > 0 and total_range / trend_range < 0.4:
                return False

        return True

    def get_params(self) -> dict:
        """返回策略参数，用于展示"""
        return {}

    def to_trade_signal(self, df: pd.DataFrame) -> dict | None:
        """预留实盘信号接口（将来对接 vnpy / CTP / XTP）

        子类可覆盖此方法，将策略判断结果转换为标准交易信号格式。

        Returns:
            None = 无信号（不交易）
            {
                "action": "buy" | "sell",
                "symbol": str,       # 代码
                "price": float,      # 委托价（0=市价）
                "volume": int,       # 手数
                "stop_loss": float,  # 止损价
                "take_profit": float,# 止盈价
            }
        """
        return None

    def __str__(self) -> str:
        return f"[{self.name}] {self.description}"
