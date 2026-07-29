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
        # 默认：至少有足够数据
        return len(df) >= 60

    def get_params(self) -> dict:
        """返回策略参数，用于展示"""
        return {}

    def __str__(self) -> str:
        return f"[{self.name}] {self.description}"
