"""适配层抽象接口 —— 回测只依赖这些接口，具体实现可替换"""
from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    """数据供应：加载基础K线 + 计算指标（指标一律向后看算子）"""

    @abstractmethod
    def load(self, code: str) -> pd.DataFrame:
        """加载单只股票基础K线（中文列名：日期/开盘/收盘/最高/最低/成交量...）"""

    @abstractmethod
    def compute_indicators(self, df: pd.DataFrame, needed_cols: list[str]) -> pd.DataFrame:
        """在给定 DataFrame 上计算所需指标列（调用方决定传入截断窗口与否）"""


class StrategyProvider(ABC):
    """策略供应：转发现有策略评分逻辑，零重写"""

    name: str = ""

    @abstractmethod
    def required_indicators(self) -> list[str]:
        """策略所需指标列（引擎会追加风险模型所需列）"""

    @abstractmethod
    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        """快速预过滤（指标计算前先行，~50µs/窗）"""

    @abstractmethod
    def grade(self, df: pd.DataFrame) -> dict:
        """normal 完整6条件评级：{"grade","scores","dl_start","match"}"""

    @abstractmethod
    def prebreak_grade(self, df: pd.DataFrame) -> dict:
        """prebreak 预突破5条件评级：{"grade","scores","trigger_price","stop_loss","risk_per_share","match"}"""


class RiskModel(ABC):
    """风险模型：出场价格口径

    v1 方法学口径（待老板确认的默认值，只改本类即可调整）：
      - normal 止损 = max(2×ATR14, 2%×进场价)（grade() 不返回止损位，v1 约定）
      - prebreak 直接用策略原生 trigger/stop（同源）
    """

    @abstractmethod
    def normal_stop(self, window: pd.DataFrame, entry_price: float) -> float:
        """normal 模式止损价（window 为截至信号日的截断窗口，含指标列）"""
