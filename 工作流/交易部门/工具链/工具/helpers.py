"""通用工具函数"""
from datetime import datetime, timedelta
import pandas as pd


def date_range_str(days_back: int = 365) -> tuple[str, str]:
    """获取日期范围字符串 YYYYMMDD"""
    end = datetime.now()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fmt_percent(val: float) -> str:
    """格式化百分比"""
    if pd.isna(val):
        return "--"
    return f"{val:+.2f}%"


def fmt_price(val: float) -> str:
    """格式化价格"""
    if pd.isna(val) or val == 0:
        return "--"
    return f"{val:.2f}"
