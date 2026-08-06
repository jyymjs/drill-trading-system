"""本地缓存管理

⚠️ deprecated（T-017 P5 主链路切换，2026-08-05）：
  权威数据源已切换为 duckdb 库（数据基础/行情数据/t017_p2.duckdb，全量历史 + 因子自算 qfq）。
  本 CSV 层降级为历史 fallback——不删除（旧文件保留可读，供对照/回退），
  读取仅发生在 duckdb 未命中之后；网络回退分支仍写缓存以便下次复用。
"""
import os
from datetime import datetime, timedelta

import pandas as pd
from 数据基础.配置.settings import DATA_DIR


def cache_path(symbol: str) -> str:
    """获取单只股票的缓存文件路径"""
    return str(DATA_DIR / f"{symbol}.csv")


def is_cache_valid(path: str, max_days: int = 1) -> bool:
    """判断缓存是否在有效期内"""
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    return datetime.now() - mtime < timedelta(days=max_days)


def read_cache(symbol: str, max_days: int = 1) -> pd.DataFrame | None:
    """读取缓存，过期返回None"""
    path = cache_path(symbol)
    if is_cache_valid(path, max_days):
        try:
            df = pd.read_csv(path, parse_dates=["日期"])
            return df
        except Exception:
            return None
    return None


def write_cache(symbol: str, df: pd.DataFrame) -> None:
    """写入缓存（原子操作：先写临时文件再 rename，防止中断时 CSV 截断）"""
    if df is not None and not df.empty:
        path = cache_path(symbol)
        tmp = path + ".tmp"
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)


def clear_cache() -> None:
    """清除所有缓存"""
    for f in DATA_DIR.glob("*.csv"):
        f.unlink()
