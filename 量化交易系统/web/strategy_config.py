"""策略配置组件"""
import streamlit as st
from strategy.base import BaseStrategy
from strategy.samples.demo_strategy import GoldenCrossStrategy
from strategy.samples.zuanqian_strategy import ZuanQianStrategy

# 注册可用策略
AVAILABLE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "均线金叉+放量": GoldenCrossStrategy,
    "钻潜评级策略": ZuanQianStrategy,
}


def register_strategy(name: str, strategy_class: type[BaseStrategy]):
    """注册新策略"""
    AVAILABLE_STRATEGIES[name] = strategy_class


def select_strategy(key: str = "strategy_select") -> BaseStrategy | None:
    """策略选择器

    Returns:
        策略实例
    """
    strategy_names = list(AVAILABLE_STRATEGIES.keys())
    selected = st.selectbox(
        "选择策略",
        strategy_names,
        key=key,
    )

    if selected:
        strategy_class = AVAILABLE_STRATEGIES[selected]
        return strategy_class()
    return None


def show_strategy_info(strategy: BaseStrategy):
    """显示策略信息"""
    if strategy:
        with st.expander(f"策略详情: {strategy.name}", expanded=False):
            st.markdown(f"**说明**: {strategy.description}")
            params = strategy.get_params()
            if params:
                st.markdown("**参数**:")
                for k, v in params.items():
                    st.markdown(f"- {k}: {v}")
