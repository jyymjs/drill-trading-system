"""示例策略 - 均线金叉 + 放量突破

条件:
1. MA5 上穿 MA20（金叉）
2. 收盘价在 MA20 上方
3. 成交量放大（> 5日均量的1.5倍）
4. RSI 在 30-70 之间（非超买超卖）
"""
import pandas as pd
from strategy.base import BaseStrategy
from strategy.conditions import (
    has_ma_cross,
    price_above_ma,
    volume_increase,
    rsi_in_range,
)


class GoldenCrossStrategy(BaseStrategy):
    name = "均线金叉+放量"
    description = "MA5上穿MA20 + 放量 + 收盘在MA20上方 + RSI适中"

    def filter(self, df: pd.DataFrame) -> bool:
        if df.empty or len(df) < 60:
            return False

        return (
            has_ma_cross(df, "MA5", "MA20")
            and price_above_ma(df, "MA20")
            and volume_increase(df, 1.5)
            and rsi_in_range(df, 30, 70)
        )

    def get_params(self) -> dict:
        return {
            "短期均线": "MA5",
            "长期均线": "MA20",
            "放量阈值": "1.5倍",
            "RSI范围": "30-70",
        }
