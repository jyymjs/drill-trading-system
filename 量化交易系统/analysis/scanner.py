"""扫描器 - 对股票池执行策略筛选"""
import time
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from tqdm import tqdm

from config.settings import (
    KLINE_YEARS, SCAN_MAX_WORKERS, SCAN_PROGRESS, SCAN_RETRY
)
from config.stock_pool import get_all_stocks, get_etf_list, get_stock_names
from data.fetcher import get_daily_kline
from analysis.indicators import all_indicators
from strategy.base import BaseStrategy
from utils.logger import logger


def scan_single_stock(
    stock: dict,
    strategy: BaseStrategy,
    years: int = KLINE_YEARS,
) -> dict | None:
    """对单只股票执行策略筛选

    Returns:
        {"code":, "name":, "match": bool, "price":, ...} 或 None
    """
    code = stock["code"]
    name = stock["name"]

    for attempt in range(SCAN_RETRY):
        try:
            df = get_daily_kline(code, use_cache=True)
            if df.empty or len(df) < 60:
                return None

            # 快速预过滤：在计算所有指标前快速排除（仅用基础K线列）
            if not strategy.quick_prefilter(df):
                return None

            # 按需计算指标：策略声明的 required_indicators → 只算需要的列
            needed = strategy.required_indicators
            df = all_indicators(df, needed_cols=needed)

            # 执行策略
            match = strategy.filter(df)

            if match:
                latest = df.iloc[-1]
                return {
                    "code": code,
                    "name": name,
                    "price": latest.get("收盘", 0),
                    "涨幅%": latest.get("涨跌幅", 0),
                    "换手率%": latest.get("换手率", 0),
                    "成交量": latest.get("成交量", 0),
                    "MA5": latest.get("MA5", 0),
                    "MA20": latest.get("MA20", 0),
                    "RSI": round(latest.get("RSI", 0), 1),
                    "策略": strategy.name,
                }
            return None

        except Exception as e:
            if attempt < SCAN_RETRY - 1:
                time.sleep(0.5)
                continue
            logger.debug("扫描 %s(%s) 失败: %s", name, code, e)
            return None


def scan(
    strategy: BaseStrategy,
    max_workers: int = SCAN_MAX_WORKERS,
    show_progress: bool = SCAN_PROGRESS,
    progress_callback: Callable | None = None,
    security_type: str = "stock",
) -> list[dict]:
    """全市场扫描

    Args:
        strategy: 策略实例
        max_workers: 并发线程数
        show_progress: 是否显示 tqdm 进度条
        progress_callback: Streamlit 进度回调 fn(current, total, stock_name)
        security_type: "stock"=仅股票, "etf"=仅ETF, "all"=全部

    Returns:
        符合条件的标的列表 [{code, name, price, ...}, ...]
    """
    logger.info("开始扫描 | 策略: %s | 类型: %s | 并发: %d",
                strategy.name, security_type, max_workers)
    logger.info("策略说明: %s", strategy.description)

    # 获取股票/ETF池
    if security_type == "etf":
        stocks = get_etf_list()
    elif security_type == "all":
        all_stocks = get_all_stocks()
        all_etfs = get_etf_list()
        stocks = all_stocks + [{"code": e["code"], "name": e["name"]} for e in all_etfs]
    else:
        stocks = get_all_stocks()
    logger.info("股票池数量: %d 只", len(stocks))

    results = []
    total = len(stocks)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scan_single_stock, s, strategy): s
            for s in stocks
        }

        iterator = as_completed(futures)
        if show_progress and not progress_callback:
            iterator = tqdm(iterator, total=total, desc="扫描中", ncols=80)

        for i, future in enumerate(iterator):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception:
                pass

            if progress_callback:
                progress_callback(i + 1, total, "")

    # 按涨幅排序
    results.sort(key=lambda x: x.get("涨幅%", 0), reverse=True)

    logger.info("扫描完成 | 符合条件: %d 只", len(results))
    return results
