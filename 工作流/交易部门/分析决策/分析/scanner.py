"""扫描器 - 对股票池执行策略筛选"""
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm
from 分析决策.分析.indicators import all_indicators
from 工具链.工具.logger import logger
from 数据基础.数据.fetcher import get_daily_kline
from 数据基础.配置.settings import (
    KLINE_YEARS,
    SCAN_MAX_WORKERS,
    SCAN_PROGRESS,
    SCAN_RETRY,
)
from 数据基础.配置.stock_pool import get_all_stocks, get_etf_list
from 策略.核心策略.base import BaseStrategy


def scan_single_stock(
    stock: dict,
    strategy: BaseStrategy,
    years: int = KLINE_YEARS,
    mode: str = "normal",
) -> dict | None:
    """对单只股票执行策略筛选

    Args:
        mode: "normal"=完整6条件, "prebreak"=预突破5条件（不含DN）

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
            if mode == "prebreak" and hasattr(strategy, 'prebreak_grade'):
                result = strategy.prebreak_grade(df)
                match = result.get("match", False)
                grade = result.get("grade", "C")
            elif hasattr(strategy, 'grade'):
                result = strategy.grade(df)
                match = result.get("match", False)
                grade = result.get("grade", "C")
            else:
                match = strategy.filter(df)
                grade = "?"
                result = {}

            if match:
                latest = df.iloc[-1]
                entry = {
                    "code": code,
                    "name": name,
                    "price": latest.get("收盘", 0),
                    "涨幅%": latest.get("涨跌幅", 0),
                    "换手率%": latest.get("换手率", 0),
                    "成交量": latest.get("成交量", 0),
                    "MA5": latest.get("MA5", 0),
                    "MA20": latest.get("MA20", 0),
                    "RSI": round(latest.get("RSI", 0), 1),
                    "评级": grade,
                    "策略": strategy.name,
                }

                # 预突破模式：附加条件单关键参数
                if mode == "prebreak":
                    entry["触发价"] = result.get("trigger_price", 0)
                    entry["止损价"] = result.get("stop_loss", 0)
                    entry["每股风险"] = result.get("risk_per_share", 0)
                    entry["TY高"] = result.get("ty_high", 0)
                    entry["TY低"] = result.get("ty_low", 0)

                return entry
            return None

        except Exception as e:
            if attempt < SCAN_RETRY - 1:
                time.sleep(0.5)
                continue
            logger.debug("扫描 %s(%s) 失败: %s", name, code, e, exc_info=True)
            return None


def scan(
    strategy: BaseStrategy,
    max_workers: int = SCAN_MAX_WORKERS,
    show_progress: bool = SCAN_PROGRESS,
    progress_callback: Callable | None = None,
    security_type: str = "stock",
    mode: str = "normal",
) -> list[dict]:
    """全市场扫描

    Args:
        strategy: 策略实例
        max_workers: 并发线程数
        show_progress: 是否显示 tqdm 进度条
        progress_callback: Streamlit 进度回调 fn(current, total, stock_name)
        security_type: "stock"=仅股票, "etf"=仅ETF, "all"=全部
        mode: "normal"=标准评级, "prebreak"=预突破模式

    Returns:
        符合条件的标的列表 [{code, name, price, ...}, ...]
    """
    mode_label = {"normal": "标准6条件", "prebreak": "预突破5条件"}.get(mode, mode)
    logger.info("开始扫描 | 策略: %s | 模式: %s | 类型: %s | 并发: %d",
                strategy.name, mode_label, security_type, max_workers)
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
            executor.submit(scan_single_stock, s, strategy, KLINE_YEARS, mode): s
            for s in stocks
        }

        iterator = as_completed(futures)
        if show_progress and not progress_callback:
            iterator = tqdm(iterator, total=total, desc="扫描中", ncols=80)

        for i, future in enumerate(iterator):
            try:
                result = future.result(timeout=30)  # 30秒超时，防止单只股票永久挂起
                if result:
                    results.append(result)
            except Exception:
                pass

            if progress_callback:
                progress_callback(i + 1, total, "")

    # 排序：预突破模式按评级优先，标准模式按涨幅
    if mode == "prebreak":
        grade_order = {"S": 0, "A": 1, "B": 2}
        results.sort(key=lambda x: grade_order.get(x.get("评级", "C"), 3))
    else:
        results.sort(key=lambda x: x.get("涨幅%", 0), reverse=True)

    logger.info("扫描完成 | 符合条件: %d 只", len(results))
    return results
