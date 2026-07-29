"""数据更新模块 - 增量/全量更新

使用 baostock 批量 API 实现快速增量更新：
- 首次全量：逐只下载（已有缓存的重用）
- 每日增量：用 bulk API 批量拉取最新一天数据，约 10 秒搞定
"""
import os
import time
from datetime import datetime, timedelta
from typing import Callable
import pandas as pd

from data.fetcher import get_daily_kline, get_bulk_day, get_stock_list
from data.cache import cache_path, read_cache, write_cache
from config.settings import KLINE_YEARS

MODE_SKIP = "skip"
MODE_OVERWRITE = "over"


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


def _read_cache_dates(symbol: str) -> tuple[str | None, str | None]:
    """读取缓存中的最早和最晚日期"""
    path = cache_path(symbol)
    if not os.path.exists(path):
        return None, None
    try:
        df = pd.read_csv(path, parse_dates=["日期"])
        if df.empty:
            return None, None
        return df["日期"].min().strftime("%Y-%m-%d"), df["日期"].max().strftime("%Y-%m-%d")
    except Exception:
        return None, None


def _is_data_upto_date(symbol: str, max_days: int = 3) -> bool:
    """判断缓存是否已包含最新数据（最近max_days天内）"""
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


def incremental_update(
    mode: str = MODE_SKIP,
    include_etf: bool = True,
    progress_callback: Callable | None = None,
) -> dict:
    """增量更新 - 使用bulk API快速拉取最新一天数据

    这是主要更新函数。流程：
    1. 从 baostock bulk API 批量获取全市场最新一日 K 线
    2. 遍历每个标的，合并到本地缓存文件
    3. 支持跳过/覆盖模式

    Args:
        mode: MODE_SKIP（已有不覆盖）或 MODE_OVERWRITE（强制覆盖）
        include_etf: 是否包含ETF
        progress_callback: fn(current, total, code, name, status)

    Returns:
        {"updated": N, "skipped": N, "failed": N}
    """
    # Step 1: 批量拉取全市场最新数据
    latest_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    bulk_df = get_bulk_day(latest_date, include_etf=include_etf)

    if bulk_df.empty:
        # fallback: 尝试更早日期
        for days_back in range(2, 7):
            test_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            bulk_df = get_bulk_day(test_date, include_etf=include_etf)
            if not bulk_df.empty:
                latest_date = test_date
                break

    if bulk_df.empty:
        return {"updated": 0, "skipped": 0, "failed": 0, "info": "未获取到数据"}

    total = len(bulk_df)
    result = {"updated": 0, "skipped": 0, "failed": 0, "date": latest_date}

    for i, (_, row) in enumerate(bulk_df.iterrows()):
        code = row.get("code", "")
        if not code:
            continue

        status = _merge_bulk_row(code, row, latest_date, mode)

        if status == "updated":
            result["updated"] += 1
        elif status == "skipped":
            result["skipped"] += 1
        else:
            result["failed"] += 1

        if progress_callback:
            progress_callback(i + 1, total, code, "", status)

    return result


def _merge_bulk_row(code: str, new_row: pd.Series, date_str: str, mode: str) -> str:
    """将单日bulk数据合并到本地缓存

    如果缓存存在、且包含此日期 → skip
    如果缓存不存在 → 创建新缓存
    如果缓存不全 → 用 get_daily_kline 补全历史 + 追加新数据
    """
    path = cache_path(code)
    latest, oldest = _read_cache_dates(code)

    # 缓存已包含此日期
    if latest and latest >= date_str:
        if mode == MODE_SKIP:
            return "skipped"

    try:
        row_data = {
            "日期": pd.to_datetime(new_row.get("日期", date_str)),
            "开盘": float(new_row.get("开盘", 0)),
            "最高": float(new_row.get("最高", 0)),
            "最低": float(new_row.get("最低", 0)),
            "收盘": float(new_row.get("收盘", 0)),
            "成交量": float(new_row.get("成交量", 0)),
            "成交额": float(new_row.get("成交额", 0)),
            "换手率": float(new_row.get("换手率", 0)),
        }

        if os.path.exists(path) and latest:
            # 读取已有缓存
            df = pd.read_csv(path, parse_dates=["日期"])
            # 如果这个日期已经存在，删除旧行再追加
            mask = df["日期"].dt.strftime("%Y-%m-%d") != date_str
            new_df = pd.concat([df[mask], pd.DataFrame([row_data])], ignore_index=True)
            new_df = new_df.sort_values("日期").reset_index(drop=True)

            # 补涨跌幅
            if "收盘" in new_df.columns:
                new_df["涨跌幅"] = new_df["收盘"].pct_change() * 100
                new_df["涨跌额"] = new_df["收盘"].diff()
                new_df["振幅"] = (new_df["最高"] - new_df["最低"]) / new_df["最低"].replace(0, pd.NA) * 100
        else:
            # 无缓存 - 拉取全年历史 + 追加当日
            df = get_daily_kline(code, use_cache=False)
            if df is None or df.empty:
                return "failed"
            new_df = df

        write_cache(code, new_df)
        return "updated"

    except Exception:
        return "failed"


def update_all_stocks(
    stocks: list[dict] | None = None,
    mode: str = MODE_SKIP,
    progress_callback: Callable | None = None,
) -> dict:
    """全量逐只更新（适用于首次下载）

    性能较差，建议优先使用 incremental_update()
    仅用于首次全量下载或强制覆盖模式
    """
    if stocks is None:
        stocks = get_stock_list()

    total = len(stocks)
    result = {"updated": 0, "skipped": 0, "failed": 0}

    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock["name"]
        status = update_single_stock(code, mode)

        if status == "updated":
            result["updated"] += 1
        elif status == "skipped":
            result["skipped"] += 1
        else:
            result["failed"] += 1

        if progress_callback:
            progress_callback(i + 1, total, code, name, status)

        time.sleep(0.05)

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
    latest = None

    for f in files:
        code = f.replace(".csv", "")
        # ETF 代码: 51xxxx, 15xxxx, 16xxxx
        if (code.startswith("51") and len(code) == 6) or \
           (code.startswith("15") and len(code) == 6) or \
           (code.startswith("16") and len(code) == 6):
            etf_count += 1
        else:
            stock_count += 1

    # 获取最新日期
    try:
        dated_files = []
        for f in files[:100]:  # 检查前100个文件
            path = os.path.join(cache_dir, f)
            df = pd.read_csv(path, parse_dates=["日期"], nrows=1)
            if not df.empty:
                dated_files.append(df["日期"].iloc[0])
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
