"""技术指标计算 - 纯 pandas 实现"""
import pandas as pd
import numpy as np


def ma(series: pd.Series, n: int) -> pd.Series:
    """移动平均线"""
    return series.rolling(window=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    """指数移动平均线"""
    return series.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 指标

    Returns:
        (dif, dea, macd_bar) — DIF线, DEA线, MACD柱状图
    """
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    macd_bar = 2 * (dif - dea)
    return dif, dea, macd_bar


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """相对强弱指标 RSI"""
    diff = close.diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    avg_gain = gain.rolling(window=n).mean()
    avg_loss = loss.rolling(window=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3):
    """KDJ 随机指标

    Returns:
        (k, d, j)
    """
    low_min = low.rolling(window=n).min()
    high_max = high.rolling(window=n).max()
    rsv = (close - low_min) / (high_max - low_min).replace(0, np.nan) * 100

    # 向量化 K/D 计算：ewm(alpha=1/3, adjust=False) 的递归公式为
    # y[t] = (1-alpha) * y[t-1] + alpha * x[t]，与 K/D 递归公式完全一致
    # K = 2/3 * prev_K + 1/3 * RSV
    # D = 2/3 * prev_D + 1/3 * K
    # 前导 NaN 填充 50（对应初始值），中间 NaN 填充前值（对应保持逻辑）
    k = rsv.ffill().fillna(50.0)
    k = k.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def boll(close: pd.Series, n: int = 20, std: int = 2):
    """布林带

    Returns:
        (mid, upper, lower)
    """
    mid = ma(close, n)
    std_dev = close.rolling(window=n).std()
    upper = mid + std * std_dev
    lower = mid - std * std_dev
    return mid, upper, lower


def volume_ratio(volume: pd.Series, n: int = 5) -> pd.Series:
    """量比 = 当前成交量 / N日均量"""
    avg_volume = ma(volume, n)
    return volume / avg_volume.replace(0, np.nan)


def ma_cross(short_ma: pd.Series, long_ma: pd.Series) -> pd.Series:
    """判断金叉/死叉信号

    Returns:
        1 = 金叉（上穿）, -1 = 死叉（下穿）, 0 = 无信号
    """
    signal = pd.Series(0, index=short_ma.index)
    cross = (short_ma > long_ma).astype(int)
    diff = cross.diff()
    signal[diff == 1] = 1    # 金叉
    signal[diff == -1] = -1  # 死叉
    return signal


def macd_cross(dif: pd.Series, dea: pd.Series) -> pd.Series:
    """MACD 金叉/死叉信号"""
    return ma_cross(dif, dea)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """平均真实波幅 (Average True Range)"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=n).mean()


def rolling_volatility(close: pd.Series, n: int = 20) -> pd.Series:
    """滚动波动率（收益率标准差）"""
    returns = close.pct_change()
    return returns.rolling(window=n).std()


def body_to_range_ratio(row) -> float:
    """K线实体占波幅比例 (单行)"""
    hl = row["最高"] - row["最低"]
    if hl == 0:
        return 1.0
    return abs(row["收盘"] - row["开盘"]) / hl


def body_to_range_series(df: pd.DataFrame) -> pd.Series:
    """K线实体占波幅比例 (序列)"""
    hl = df["最高"] - df["最低"]
    body = (df["收盘"] - df["开盘"]).abs()
    return (body / hl).fillna(1.0).clip(upper=1.0)


def consecutive_count(series: pd.Series, condition) -> int:
    """最近连续满足条件的次数"""
    count = 0
    for val in series[::-1]:
        if condition(val):
            count += 1
        else:
            break
    return count


def support_resistance_levels(df: pd.DataFrame, n_bins: int = 20) -> list[float]:
    """简易支撑/阻力位检测：价格分布峰值

    Args:
        df: 包含最高/最低的 DataFrame
        n_bins: 价格区间划分数

    Returns:
        关键价位列表
    """
    prices = pd.concat([df["最高"], df["最低"]])
    hist, edges = np.histogram(prices, bins=n_bins)
    peak_bins = np.argsort(hist)[-3:]  # 取密度最高的3个区间
    levels = [(edges[i] + edges[i + 1]) / 2 for i in sorted(peak_bins)]
    return levels


def all_indicators(df: pd.DataFrame,
                   needed_cols: list[str] | None = None) -> pd.DataFrame:
    """计算技术指标，支持按需计算

    Args:
        df: 原始K线DataFrame
        needed_cols: 需要的指标列名列表。
                     None = 计算全部（向后兼容）；
                     空列表 = 只返回基础K线列。

    Returns:
        带指标列的DataFrame
    """
    result = df.copy()

    # None = 计算全部（向后兼容）
    if needed_cols is None:
        needed_cols = [
            "MA5", "MA10", "MA20", "MA60", "MA120",
            "VOL_MA5", "VOL_MA10",
            "DIF", "DEA", "MACD", "RSI",
            "K", "D", "J",
            "BOLL_MID", "BOLL_UPPER", "BOLL_LOWER",
            "VOL_RATIO", "ATR", "VOLATILITY", "BODY_RATIO", "MA_CROSS",
        ]
    needed = set(needed_cols)

    # 均线（依赖：收盘）
    ma_cols = {"MA5": 5, "MA10": 10, "MA20": 20, "MA60": 60, "MA120": 120}
    needed_ma = {k: v for k, v in ma_cols.items() if k in needed}
    for col, period in needed_ma.items():
        result[col] = ma(result["收盘"], period)

    # 成交量均线（依赖：成交量）
    if "VOL_MA5" in needed:
        result["VOL_MA5"] = ma(result["成交量"], 5)
    if "VOL_MA10" in needed:
        result["VOL_MA10"] = ma(result["成交量"], 10)

    # MACD（依赖：收盘）
    if "DIF" in needed or "DEA" in needed or "MACD" in needed:
        dif, dea, macd_bar = macd(result["收盘"])
        if "DIF" in needed:
            result["DIF"] = dif
        if "DEA" in needed:
            result["DEA"] = dea
        if "MACD" in needed:
            result["MACD"] = macd_bar

    # RSI（依赖：收盘）
    if "RSI" in needed:
        result["RSI"] = rsi(result["收盘"])

    # KDJ（依赖：最高、最低、收盘）
    if "K" in needed or "D" in needed or "J" in needed:
        k, d, j = kdj(result["最高"], result["最低"], result["收盘"])
        if "K" in needed:
            result["K"] = k
        if "D" in needed:
            result["D"] = d
        if "J" in needed:
            result["J"] = j

    # 布林带（依赖：收盘）
    if "BOLL_MID" in needed or "BOLL_UPPER" in needed or "BOLL_LOWER" in needed:
        mid, upper, lower = boll(result["收盘"])
        if "BOLL_MID" in needed:
            result["BOLL_MID"] = mid
        if "BOLL_UPPER" in needed:
            result["BOLL_UPPER"] = upper
        if "BOLL_LOWER" in needed:
            result["BOLL_LOWER"] = lower

    # 量比（依赖：成交量）
    if "VOL_RATIO" in needed:
        result["VOL_RATIO"] = volume_ratio(result["成交量"])

    # 波动率指标（依赖：最高/最低/收盘）
    if "ATR" in needed:
        result["ATR"] = atr(result["最高"], result["最低"], result["收盘"])
    if "VOLATILITY" in needed:
        result["VOLATILITY"] = rolling_volatility(result["收盘"])
    if "BODY_RATIO" in needed:
        result["BODY_RATIO"] = body_to_range_series(result)

    # 均线交叉信号（依赖：MA5, MA20）
    if "MA_CROSS" in needed:
        if "MA5" not in result.columns:
            result["MA5"] = ma(result["收盘"], 5)
        if "MA20" not in result.columns:
            result["MA20"] = ma(result["收盘"], 20)
        result["MA_CROSS"] = ma_cross(result["MA5"], result["MA20"])

    return result
