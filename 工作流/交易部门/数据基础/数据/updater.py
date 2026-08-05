"""数据更新模块 - pytdx + 并发加速

数据源优先级: pytdx(0.1-0.3秒/只) → baostock(2-3秒/只, fallback)

更新策略:
  - 快速更新: 用 pytdx 并发拉取最新数据（全市场约 10-20 秒）
  - 全量更新: 逐只 pytdx + ThreadPoolExecutor 并行
"""
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from 数据基础.数据.cache import cache_path, write_cache
from 数据基础.数据.fetcher import (
    _fetch_by_akshare,
    _fetch_by_baostock,
    _fetch_by_pytdx,
    get_daily_kline,
)
from 数据基础.配置.settings import KLINE_YEARS

MODE_SKIP = "skip"
MODE_OVERWRITE = "over"

# 并发线程数
MAX_WORKERS = 8


def _latest_date_in_cache(symbol: str) -> str | None:
    """检查缓存中最新的数据日期"""
    path = cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["日期"])
        if not df.empty:
            return df["日期"].iloc[-1].strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _is_data_upto_date(symbol: str, max_days: int = 3) -> bool:
    """判断缓存是否已包含最新数据"""
    latest = _latest_date_in_cache(symbol)
    if latest is None:
        return False
    latest_dt = datetime.strptime(latest, "%Y-%m-%d")
    return (datetime.now() - latest_dt).days <= max_days


def update_single_stock(symbol: str, mode: str = MODE_SKIP) -> str:
    """更新单只股票数据

    Returns: "skipped" | "updated" | "failed"
    """
    if mode == MODE_SKIP and _is_data_upto_date(symbol):
        return "skipped"
    try:
        df = get_daily_kline(symbol, use_cache=False)
        if df is not None and not df.empty:
            write_cache(symbol, df)
            return "updated"
        return "failed"
    except Exception:
        return "failed"


def update_all_stocks(
    stocks: list[dict] | None = None,
    mode: str = MODE_SKIP,
    progress_callback: Callable | None = None,
) -> dict:
    """全量逐只更新（并发 pytdx，推荐方式）

    Args:
        stocks: 股票列表 [{code, name}, ...]
        mode: MODE_SKIP（已有不覆盖）或 MODE_OVERWRITE
        progress_callback: fn(current, total, code, name, status)

    Returns:
        {"updated": N, "skipped": N, "failed": N}
    """
    if stocks is None:
        from 数据基础.配置.stock_pool import get_all_stocks
        stocks = get_all_stocks()

    total = len(stocks)
    result = {"updated": 0, "skipped": 0, "failed": 0}
    lock = __import__("threading").Lock()

    def _update_one(stock: dict) -> tuple[str, str, str]:
        """线程内更新单只"""
        code = stock["code"]
        name = stock.get("name", code)

        if mode == MODE_SKIP:
            try:
                if _is_data_upto_date(code):
                    return code, name, "skipped"
            except Exception:
                pass

        try:
            # 优先 pytdx
            df = _fetch_by_pytdx(code, KLINE_YEARS)
            if df is not None and not df.empty:
                write_cache(code, df)
                return code, name, "updated"

            # fallback: 直接走 baostock（跳过 get_daily_kline 的 pytdx 重试）
            start_str = f"{datetime.now().year - KLINE_YEARS}0101"
            end_str = datetime.now().strftime("%Y%m%d")
            df = _fetch_by_baostock(code, start_str, end_str)
            if df is not None and not df.empty:
                write_cache(code, df)
                return code, name, "updated"
            # 最后备选 akshare
            df = _fetch_by_akshare(code, start_str, end_str)
            if df is not None and not df.empty:
                write_cache(code, df)
                return code, name, "updated"
            return code, name, "failed"
        except Exception:
            return code, name, "failed"

    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_update_one, s): s for s in stocks}

        for future in as_completed(futures):
            try:
                code, name, status = future.result()
                with lock:
                    if status == "updated":
                        result["updated"] += 1
                    elif status == "skipped":
                        result["skipped"] += 1
                    else:
                        result["failed"] += 1
                    completed += 1
                if progress_callback:
                    progress_callback(completed, total, code, name, status)
            except Exception:
                with lock:
                    result["failed"] += 1
                    completed += 1

    return result


def incremental_update(
    mode: str = MODE_SKIP,
    include_etf: bool = True,
    progress_callback: Callable | None = None,
) -> dict:
    """增量更新 - 使用 pytdx 并发拉取最新全市场数据

    这是推荐的日常更新方式。
    用 pytdx 并行拉取全市场数据，速度比 baostock bulk API 快 10 倍以上。

    Args:
        mode: MODE_SKIP（已有不覆盖）或 MODE_OVERWRITE
        include_etf: 是否包含ETF（暂未实现，仅更新股票）
        progress_callback: fn(current, total, code, name, status)

    Returns:
        {"updated": N, "skipped": N, "failed": N, "date": str}
    """
    from 数据基础.配置.stock_pool import get_all_stocks, get_etf_list

    # 获取全部股票
    stocks = get_all_stocks()
    if include_etf:
        etfs = get_etf_list()
        stocks += [{"code": e["code"], "name": e["name"]} for e in etfs]

    # 只保留需要更新的（跳过已是最新的）
    if mode == MODE_SKIP:
        to_update = [s for s in stocks if not _is_data_upto_date(s["code"])]
    else:
        to_update = stocks

    if not to_update:
        today = datetime.now().strftime("%Y-%m-%d")
        return {"updated": 0, "skipped": len(stocks), "failed": 0, "date": today}

    total = len(to_update)
    result = {"updated": 0, "skipped": 0, "failed": 0, "date": datetime.now().strftime("%Y-%m-%d")}
    lock = __import__("threading").Lock()

    def _update_one(code: str) -> tuple[str, str]:
        """线程内更新单只"""
        try:
            df = _fetch_by_pytdx(code, KLINE_YEARS)
            if df is not None and not df.empty:
                write_cache(code, df)
                return code, "updated"
            # fallback: 直接走 baostock（跳过 pytdx 重试）
            start_str = f"{datetime.now().year - KLINE_YEARS}0101"
            end_str = datetime.now().strftime("%Y%m%d")
            df = _fetch_by_baostock(code, start_str, end_str)
            if df is not None and not df.empty:
                write_cache(code, df)
                return code, "updated"
            df = _fetch_by_akshare(code, start_str, end_str)
            if df is not None and not df.empty:
                write_cache(code, df)
                return code, "updated"
            return code, "failed"
        except Exception:
            return code, "failed"

    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_update_one, s["code"]): s for s in to_update}

        for future in as_completed(futures):
            try:
                code, status = future.result()
                with lock:
                    if status == "updated":
                        result["updated"] += 1
                    else:
                        result["failed"] += 1
                    completed += 1
                if progress_callback:
                    progress_callback(completed, total, code, "", status)
            except Exception:
                with lock:
                    result["failed"] += 1
                    completed += 1

    # 把跳过的也算上
    result["skipped"] = len(stocks) - len(to_update)
    return result


def get_cache_stats() -> dict:
    """获取缓存统计信息"""
    cache_dir = os.path.dirname(cache_path("__dummy__"))
    if not os.path.exists(cache_dir):
        return {"stock_cached": 0, "etf_cached": 0, "total": 0, "latest_date": None}

    files = [f for f in os.listdir(cache_dir)
             if f.endswith(".csv") and not f.startswith("__")]

    stock_count = 0
    etf_count = 0

    for f in files:
        code = f.replace(".csv", "")
        if (code.startswith("51") and len(code) == 6) or \
           (code.startswith("15") and len(code) == 6) or \
           (code.startswith("16") and len(code) == 6):
            etf_count += 1
        else:
            stock_count += 1

    # 获取最新日期（读取每组最后100只的最新日期）
    latest = None
    try:
        dated_files = []
        # 从前、中、后各取一些文件，读取最后一行
        n = len(files)
        check_indices = list(range(min(50, n))) + \
                        list(range(n // 2, min(n // 2 + 30, n))) + \
                        list(range(max(0, n - 50), n))
        for idx in set(check_indices):
            if idx >= len(files):
                continue
            path = os.path.join(cache_dir, files[idx])
            df = pd.read_csv(path, parse_dates=["日期"], usecols=["日期"])
            if not df.empty:
                dated_files.append(df["日期"].iloc[-1])  # 取最后一行（最新）
        if dated_files:
            latest = max(dated_files).strftime("%Y-%m-%d")
    except Exception:
        pass

    return {
        "stock_cached": stock_count,
        "etf_cached": etf_count,
        "total": len(files),
        "latest_date": latest,
    }
