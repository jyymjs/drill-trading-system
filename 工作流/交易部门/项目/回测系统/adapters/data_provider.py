"""CacheDataProvider：duckdb 权威源直读（T-017 P5），缺省回退 CSV 缓存 → 网络

- duckdb（数据基础/data/t017_p2.duckdb）：全量历史（1990 起），qfq 四价自算——
  解锁 D1 分段一致性检查 ≥3 年数据前提（旧 CSV 缓存仅 ~3 年窗口，无法成立）
- CSV 缓存：已 deprecated，降级为 fallback（旧文件保留可读）
- 网络：最后兜底（get_daily_kline 内部同样 duckdb 优先）
"""
from pathlib import Path

import pandas as pd
from 分析决策.分析.indicators import all_indicators
from 数据基础.数据.cache import cache_path
from 数据基础.数据.fetcher import get_daily_kline

from 回测系统.adapters.base import DataProvider


class CacheDataProvider(DataProvider):
    """数据供应：duckdb 全量优先，CSV/网络回退兜底"""

    def __init__(self, db_path=None):
        """db_path: duckdb 库路径（默认配置 DB_PATH；测试可注入临时库）"""
        self.db_path = db_path

    def load(self, code: str) -> pd.DataFrame:
        """duckdb 全量历史直读（qfq 自算）；缺省回退 CSV 缓存 → 网络"""
        df = self._load_duckdb(code)
        if df is not None and not df.empty:
            return df
        # 回退：deprecated CSV 缓存（旧文件）
        path = Path(cache_path(code))
        if path.exists():
            try:
                df = pd.read_csv(path, parse_dates=["日期"])
                if not df.empty and {"日期", "收盘", "最高", "最低"} <= set(df.columns):
                    return df
            except Exception:  # noqa: BLE001, S110 - CSV 损坏兜底，继续降级
                pass
        # 最后兜底：走现有管线（duckdb 优先 → 网络，可能联网拉取）
        df = get_daily_kline(code, use_cache=True)
        return df

    def _load_duckdb(self, code: str) -> pd.DataFrame | None:
        """直读 duckdb 全量历史（无日期窗口限制，解锁 D1 ≥3 年检查）"""
        try:
            from 数据基础.duckdb.reader import read_kline
            return read_kline(code, db_path=self.db_path)
        except Exception:  # noqa: BLE001 - 库缺失/损坏一律回退下一层
            return None

    def compute_indicators(self, df: pd.DataFrame, needed_cols: list[str]) -> pd.DataFrame:
        """按需计算指标（向量化、向后看算子，与 diagnose 同款入口 all_indicators）"""
        if df.empty:
            return df
        return all_indicators(df, needed_cols=needed_cols)
