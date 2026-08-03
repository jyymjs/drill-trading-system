"""DefaultRiskModel：normal 用 ATR 止损；prebreak 用策略原生 trigger/stop（同源）

方法学口径（计划"待老板确认的默认值"）：
  1. normal 止损 = max(2×ATR14, 2%×进场价)——grade() 不返回止损位，v1 约定；
  2. prebreak 触发价/止损价/每股风险 = prebreak_grade() 原生返回值。
"""
import numpy as np
import pandas as pd

from backtest.adapters.base import RiskModel


class DefaultRiskModel(RiskModel):
    """v1 默认风险模型：仅 止损+hold到期收盘 两种出场"""

    ATR_MULT = 2.0        # 2×ATR14
    PCT_STOP = 0.02       # 2%×进场价

    def normal_stop(self, window: pd.DataFrame, entry_price: float) -> float:
        """normal 止损价 = 进场价 - max(2×ATR14, 2%×进场价)

        Args:
            window: 截至信号日的截断窗口（含 ATR 指标列，t≥249 保证 ATR 已收敛）
            entry_price: 信号日 T 收盘价（进场价）
        """
        atr_val = 0.0
        if "ATR" in window.columns and len(window) > 0:
            last = window["ATR"].iloc[-1]
            if isinstance(last, (int, float)) and not np.isnan(last):
                atr_val = float(last)
        stop_dist = max(self.ATR_MULT * atr_val, self.PCT_STOP * entry_price)
        stop_price = entry_price - stop_dist
        return round(float(stop_price), 4)
