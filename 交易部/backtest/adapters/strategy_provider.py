"""ZuanQianProvider：转发 ZuanQianStrategy.grade()/prebreak_grade()/quick_prefilter()，零逻辑重写

同源复用铁律：回测只做"时间切片 + 调用"，不重写任何评分逻辑。
"""
from strategy.samples.zuanqian_strategy import ZuanQianStrategy

from backtest.adapters.base import StrategyProvider


class ZuanQianProvider(StrategyProvider):
    """钻潜评级策略 V2 适配器"""

    name = "zuanqian_strategy"

    def __init__(self) -> None:
        self._strategy = ZuanQianStrategy()

    @property
    def strategy(self) -> ZuanQianStrategy:
        """暴露原生策略实例（verify 同源抽查等场景需要）"""
        return self._strategy

    def required_indicators(self) -> list[str]:
        """策略所需指标 + 风险模型所需 ATR14（normal 止损口径）"""
        return list(self._strategy.required_indicators) + ["ATR"]

    def quick_prefilter(self, df) -> bool:
        return self._strategy.quick_prefilter(df)

    def grade(self, df) -> dict:
        return self._strategy.grade(df)

    def prebreak_grade(self, df) -> dict:
        return self._strategy.prebreak_grade(df)
