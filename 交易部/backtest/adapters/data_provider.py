"""CacheDataProvider：直读 data/cache/{code}.csv（绕过1天有效期），缺失回退 get_daily_kline

离线、确定、可复现：缓存 CSV 为中文列名、日期已解析、无指标列（需复算）。
"""
from pathlib import Path

import pandas as pd

from analysis.indicators import all_indicators
from config.settings import DATA_DIR
from data.cache import cache_path
from data.fetcher import get_daily_kline

from backtest.adapters.base import DataProvider


class CacheDataProvider(DataProvider):
    """数据供应：缓存直读优先，网络回退兜底"""

    def load(self, code: str) -> pd.DataFrame:
        """直读缓存 CSV（绕过 read_cache 的 1 天有效期）；缺失/损坏回退 get_daily_kline"""
        path = Path(cache_path(code))
        if path.exists():
            try:
                df = pd.read_csv(path, parse_dates=["日期"])
                if not df.empty and {"日期", "收盘", "最高", "最低"} <= set(df.columns):
                    return df
            except Exception:
                pass
        # 回退：走现有管线（可能联网拉取并写缓存，但本类不依赖其结果写回）
        df = get_daily_kline(code, use_cache=True)
        return df

    def compute_indicators(self, df: pd.DataFrame, needed_cols: list[str]) -> pd.DataFrame:
        """按需计算指标（向量化、向后看算子，与 diagnose 同款入口 all_indicators）"""
        if df.empty:
            return df
        return all_indicators(df, needed_cols=needed_cols)
