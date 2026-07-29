"""技术指标计算 - 纯 pandas 实现"""
import pandas as pd
import numpy as np


def ma(series: pd.Series, n: int) -> pd.Series:
    """移动平均线"""
    return series.rolling(window=n).mean()


def _put(df: pd.DataFrame, needed: set, mapping: dict[str, pd.Series]) -> None:
    """按需写入指标列（减少重复的 if col in needed 分支）"""
    for col, val in mapping.items():
        if col in needed:
            df[col] = val


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
    """最近连续满足条件的次数（向量化实现）"""
    mask = series.map(condition)
    if mask.empty or not mask.iloc[-1]:
        return 0
    # 从尾部开始累计 True，遇到 False 终止
    rev_mask = mask.iloc[::-1]
    first_false = (~rev_mask).idxmax()
    if first_false == rev_mask.index[0] and rev_mask.iloc[0]:
        return len(rev_mask)  # 全部满足
    pos = rev_mask.index.get_loc(first_false)
    return int(rev_mask.iloc[:pos].sum())


def platform_test_count(df: pd.DataFrame, tolerance: float = 0.01, min_gap: int = 3) -> int:
    """平台位测试次数计数（PT 维度核心）

    统计价格对同一水平位的反复测试次数。
    两次测试间隔 < min_gap 根K线算同一次。

    Args:
        df: K线DataFrame
        tolerance: 同一水平位的容忍范围（比例）
        min_gap: 两次测试最小间隔K线数

    Returns:
        有效测试次数
    """
    if len(df) < 20:
        return 0

    close = df["收盘"].values
    high = df["最高"].values
    low = df["最低"].values

    # 取最近60根K线的主要价格水平（用均值聚类）
    n = min(60, len(df))
    prices = close[-n:]

    # 用简单方法：找价格反复触及的区域
    # 计算每个价格水平附近的触及次数
    levels = []
    counts = []
    last_touch = []  # 记录每次测试的位置

    for i in range(len(prices)):
        p = prices[i]
        matched = False
        for j, lv in enumerate(levels):
            if abs(p - lv) / (lv + 1e-8) <= tolerance:
                # 检查间隔
                if last_touch and i - last_touch[j] < min_gap:
                    last_touch[j] = i
                    matched = True
                    break
                counts[j] += 1
                last_touch[j] = i
                matched = True
                break
        if not matched:
            levels.append(p)
            counts.append(1)
            last_touch.append(i)

    return max(counts) if counts else 0


def profile_compactness(df: pd.DataFrame, window: int = 20) -> float:
    """轮廓紧凑度评分（LK 维度核心）

    计算K线实体占波幅比例的平均值。
    >0.5 = 紧凑，<0.3 = 松散。

    Args:
        df: K线DataFrame
        window: 计算窗口

    Returns:
        紧凑度评分 0~1
    """
    if len(df) < window:
        window = len(df)
    recent = df.tail(window)
    hl = recent["最高"] - recent["最低"]
    body = (recent["收盘"] - recent["开盘"]).abs()
    ratios = (body / hl).fillna(1.0).clip(upper=1.0)
    return float(ratios.mean())


def retracement_detect(df: pd.DataFrame, lookback: int = 15) -> dict:
    """回踩轨迹检测

    检测最近的"上→下→上"摆动结构。
    "直接往上冲不做"——必须检测到回踩动作。

    Args:
        df: K线DataFrame
        lookback: 回溯K线数

    Returns:
        {"has_retracement": bool, "quality": "good"/"weak"/"none"}
    """
    if len(df) < lookback:
        return {"has_retracement": False, "quality": "none"}

    recent = df.tail(lookback)
    highs = recent["最高"].values
    lows = recent["最低"].values

    # 检测是否存在先升→后降→再升的摆动
    n = len(highs)
    mid = n // 2

    first_half_high = highs[:mid].max()
    first_half_low = lows[:mid].min()
    second_half_high = highs[mid:].max()
    second_half_low = lows[mid:].min()

    # 完整回踩：先升(前段高点高) → 降(中段低点低) → 再升(后段高点>前段高点)
    if (second_half_high > first_half_high * 0.98
            and first_half_low < second_half_low * 1.02
            and first_half_high > first_half_high * 0.95):
        return {"has_retracement": True, "quality": "good"}

    # 微弱回踩
    if second_half_high > first_half_high * 0.95:
        return {"has_retracement": True, "quality": "weak"}

    return {"has_retracement": False, "quality": "none"}


def channel_detect(df: pd.DataFrame, n: int = 8) -> dict:
    """通道上涨检测

    检测是否形成狭窄上升通道。"通道感不喜欢"——降级。

    Args:
        df: K线DataFrame
        n: 检测窗口

    Returns:
        {"is_channel": bool, "strength": float}
    """
    if len(df) < n:
        return {"is_channel": False, "strength": 0.0}

    recent = df.tail(n)
    highs = recent["最高"].values
    lows = recent["最低"].values

    # 检查高低点是否同步抬高
    high_increasing = all(highs[i] <= highs[i + 1] for i in range(n - 1))
    low_increasing = all(lows[i] <= lows[i + 1] for i in range(n - 1))

    if not (high_increasing or low_increasing):
        return {"is_channel": False, "strength": 0.0}

    # 通道稳定性：回归R²
    x = np.arange(n)
    try:
        coefs = np.polyfit(x, highs, 1)
        preds = np.polyval(coefs, x)
        ss_res = np.sum((highs - preds) ** 2)
        ss_tot = np.sum((highs - np.mean(highs)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    except Exception:
        r2 = 0

    # 波动范围 vs 趋势幅度
    total_range = highs.max() - lows.min()
    trend_range = abs(highs[-1] - highs[0])
    channel_ratio = total_range / trend_range if trend_range > 0 else 1.0

    is_channel = r2 > 0.7 and channel_ratio < 0.4
    return {"is_channel": is_channel, "strength": r2 if is_channel else 0.0}


def overshoot_detect(df: pd.DataFrame, window: int = 60) -> dict:
    """过高点检测

    检测结构内是否出现过"创新高后回落"的过高点。
    过高点后需从新位置重新计数结构。

    Args:
        df: K线DataFrame
        window: 检测窗口

    Returns:
        {"has_overshoot": bool, "position": int}
    """
    if len(df) < 30:
        return {"has_overshoot": False, "position": -1}

    close = df["收盘"].values
    n = min(window, len(df))
    recent_close = close[-n:]
    recent_high = df["最高"].values[-n:]

    # 找局部最高点
    local_high_idx = -1
    local_high_val = 0
    for i in range(10, n - 5):  # 排除边界
        if (recent_high[i] > recent_high[i - 1]
                and recent_high[i] >= recent_high[i - 2]
                and recent_high[i] > recent_high[i + 1]
                and recent_high[i] > recent_high[i + 2]):
            # 检查这个高点后是否回落超过2%
            if i < n - 1 and (close[i] - close[-1]) / close[i] > 0.02:
                if recent_high[i] > local_high_val:
                    local_high_val = recent_high[i]
                    local_high_idx = i

    # 过高点：创了收盘新高后回落
    if local_high_idx >= 0:
        # 检查此高点前是否有更低的起点
        pre_low = min(recent_close[:local_high_idx])
        post_low = recent_close[local_high_idx:].min()
        if pre_low < post_low < local_high_val:
            return {"has_overshoot": True, "position": local_high_idx}

    return {"has_overshoot": False, "position": -1}


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


# ══════════════════════════════════════════════════════════
# Qlib Alpha158 精选因子（手工实现，零外部依赖）
# 选取与钻潜6条件体系直接相关的因子
# ══════════════════════════════════════════════════════════

def roc(close: pd.Series, n: int = 10) -> pd.Series:
    """N日涨跌幅 (Rate of Change)

    对应: DN 动能辅助判断，跟踪价格趋势强度
    """
    return close.pct_change(periods=n)


def rolling_std(close: pd.Series, n: int = 20) -> pd.Series:
    """N日滚动标准差

    对应: LK 轮廓波动 — 标准差低 = 横盘紧凑
    """
    return close.rolling(window=n).std()


def volume_std(volume: pd.Series, n: int = 10) -> pd.Series:
    """N日成交量标准差

    对应: DN 放量检测 — 突然放量 = 异常信号
    """
    return volume.rolling(window=n).std()


def max_position(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    """最高价相对位置 (0~1)

    当前价格在N日区间中的位置，>0.8=右上角，<0.2=右下角。
    对应: DL 结构检测 — 右上角需谨慎
    """
    hh = high.rolling(window=n).max()
    ll = low.rolling(window=n).min()
    return (close - ll) / (hh - ll).replace(0, np.nan)


def klen(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """K线实体比例 (KLEN)

    (最高-最低)/收盘，值越小 = 整理越充分。
    对应: LK 实体比 + TY 窄幅检测
    """
    return (high - low) / close.replace(0, np.nan)


def wvma(close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    """量加权均价 (Volume Weighted Moving Average)

    对应: 支撑/阻力参考 — 量大的价位支撑更强
    """
    pv = close * volume
    return pv.rolling(window=n).sum() / volume.rolling(window=n).sum().replace(0, np.nan)


def price_volume_corr(close: pd.Series, volume: pd.Series, n: int = 10) -> pd.Series:
    """量价相关系数

    >0 = 量价同向（健康），<0 = 量价背离（警惕）。
    对应: 量价配合确认
    """
    return close.rolling(window=n).corr(volume)


def r_squared(high: pd.Series, low: pd.Series, n: int = 20) -> pd.Series:
    """线性回归 R²（通道感量化）

    >0.7 = 强烈通道感，应降级或排除。
    对应: 通道上涨检测（替代原有 channel_detect 函数）
    """
    x = np.arange(n)
    x_mean = x.mean()

    def calc_rsq(y):
        if len(y) < n:
            return np.nan
        coefs = np.polyfit(x, y, 1)
        preds = np.polyval(coefs, x)
        ss_res = np.sum((y - preds) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    mid = (high + low) / 2
    return mid.rolling(window=n).apply(calc_rsq, raw=True)


def bias(close: pd.Series, n: int = 5) -> pd.Series:
    """N日乖离率

    (收盘-MA)/MA，衡量短期偏离均线程度。
    对应: 短期偏离检测 — 乖离过大不追
    """
    ma_n = ma(close, n)
    return (close - ma_n) / ma_n.replace(0, np.nan)


def mad(close: pd.Series, n: int = 20) -> pd.Series:
    """价格离散度 (Mean Absolute Deviation)

    衡量K线的均匀程度。值越小=横盘越"密集"。
    对应: LK 轮廓松散度
    """
    ma_n = close.rolling(window=n).mean()
    mad_val = (close - ma_n).abs().rolling(window=n).mean()
    return mad_val / ma_n.replace(0, np.nan)


def illiquidity(close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    """非流动性指标 (Amihud Illiquidity)

    |收益率| / 成交额，值越大=流动性越差。
    对应: 品种过滤 — 流动性太差的品种不纳入候选池
    """
    ret = close.pct_change().abs()
    amount = volume * close  # 近似成交额
    illiq = ret / amount.replace(0, np.nan)
    return illiq.rolling(window=n).mean()


def turn_zscore(turnover: pd.Series, n: int = 20) -> pd.Series:
    """换手率 Z-Score

    检测换手率异常放大的时点。Z>2 = 异常活跃。
    对应: 异常换手检测 — 配合 DN 动能判断
    """
    ma_n = turnover.rolling(window=n).mean()
    std_n = turnover.rolling(window=n).std()
    return (turnover - ma_n) / std_n.replace(0, np.nan)


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
            # Qlib 精选因子
            "ROC10", "STD20", "VSTD10", "MAXPOS20", "KLEN",
            "WVMA20", "CORR10", "RSQR20", "BIAS5", "MAD20",
        ]
    needed = set(needed_cols)

    # === 指标计算映射表 ===
    # (依赖列, 计算函数, 输出映射)
    # 函数签名 fn(df, result) → 修改 result 原地
    c = result  # 简写

    _MA_SPECS = {"MA5": 5, "MA10": 10, "MA20": 20, "MA60": 60, "MA120": 120}
    _VOL_MA_SPECS = {"VOL_MA5": 5, "VOL_MA10": 10}

    # 1) 均线
    for col, period in _MA_SPECS.items():
        if col in needed:
            c[col] = ma(c["收盘"], period)

    # 2) 成交量均线
    for col, period in _VOL_MA_SPECS.items():
        if col in needed:
            c[col] = ma(c["成交量"], period)

    # 3) MACD
    if needed & {"DIF", "DEA", "MACD"}:
        dif, dea, macd_bar = macd(c["收盘"])
        _put(c, needed, {"DIF": dif, "DEA": dea, "MACD": macd_bar})

    # 4) RSI
    if "RSI" in needed:
        c["RSI"] = rsi(c["收盘"])

    # 5) KDJ
    if needed & {"K", "D", "J"}:
        k, d, j = kdj(c["最高"], c["最低"], c["收盘"])
        _put(c, needed, {"K": k, "D": d, "J": j})

    # 6) 布林带
    if needed & {"BOLL_MID", "BOLL_UPPER", "BOLL_LOWER"}:
        mid, upper, lower = boll(c["收盘"])
        _put(c, needed, {"BOLL_MID": mid, "BOLL_UPPER": upper, "BOLL_LOWER": lower})

    # 7) 量比
    if "VOL_RATIO" in needed:
        c["VOL_RATIO"] = volume_ratio(c["成交量"])

    # 8) 波动率指标
    if "ATR" in needed:
        c["ATR"] = atr(c["最高"], c["最低"], c["收盘"])
    if "VOLATILITY" in needed:
        c["VOLATILITY"] = rolling_volatility(c["收盘"])
    if "BODY_RATIO" in needed:
        c["BODY_RATIO"] = body_to_range_series(c)

    # 9) 均线交叉信号（依赖 MA5, MA20）
    if "MA_CROSS" in needed:
        if "MA5" not in c.columns:
            c["MA5"] = ma(c["收盘"], 5)
        if "MA20" not in c.columns:
            c["MA20"] = ma(c["收盘"], 20)
        c["MA_CROSS"] = ma_cross(c["MA5"], c["MA20"])

    # 10) Qlib 精选因子（12个）
    if "ROC10" in needed:
        c["ROC10"] = roc(c["收盘"], 10)
    if "STD20" in needed:
        c["STD20"] = rolling_std(c["收盘"], 20)
    if "VSTD10" in needed:
        c["VSTD10"] = volume_std(c["成交量"], 10)
    if "MAXPOS20" in needed:
        c["MAXPOS20"] = max_position(c["最高"], c["最低"], c["收盘"], 20)
    if "KLEN" in needed:
        c["KLEN"] = klen(c["最高"], c["最低"], c["收盘"])
    if "WVMA20" in needed:
        c["WVMA20"] = wvma(c["收盘"], c["成交量"], 20)
    if "CORR10" in needed:
        c["CORR10"] = price_volume_corr(c["收盘"], c["成交量"], 10)
    if "RSQR20" in needed:
        c["RSQR20"] = r_squared(c["最高"], c["最低"], 20)
    if "BIAS5" in needed:
        c["BIAS5"] = bias(c["收盘"], 5)
    if "MAD20" in needed:
        c["MAD20"] = mad(c["收盘"], 20)

    return result
