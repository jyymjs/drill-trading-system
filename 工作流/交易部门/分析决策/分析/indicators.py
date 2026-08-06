"""技术指标计算 - 纯 pandas 实现"""
import numpy as np
import pandas as pd
from numba import njit


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
    """相对强弱指标 RSI（Cutler's RSI — SMA 平滑，非 Wilder 递归平滑）"""
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
    cross = (short_ma > long_ma).fillna(False).astype(int)
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


@njit(cache=True)
def _platform_test_count_nb(close, high, n, tolerance, min_gap) -> int:
    """平台测试计数核心（njit，T-028 2026-08-06）：与 Python 版逐位一致

    - 同循环顺序、同运算（abs(p-lv)/(lv+1e-8)<=tolerance / 间隔合并）；
    - overshoot 集合用列表替代（n≤60，线性 in 开销可忽略，语义等价）；
    - close[i] 用全数组索引、highs 为 high[-n:] 视图——与原实现精确一致。
    """
    prices = close[-n:]
    highs = high[-n:]

    overshoot = []
    for i in range(10, n - 5):
        if (highs[i] > highs[i - 1]
                and highs[i] >= highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
                and (close[i] - close[-1]) / close[i] > 0.02):
            overshoot.append(i)

    levels = []
    counts = []
    last_touch = []

    for i in range(len(prices)):
        skip = False
        for oi in range(len(overshoot)):
            if overshoot[oi] == i:
                skip = True
                break
        if skip:
            continue
        p = prices[i]
        matched = False
        for j in range(len(levels)):
            lv = levels[j]
            if abs(p - lv) / (lv + 1e-8) <= tolerance:
                if len(last_touch) and i - last_touch[j] < min_gap:
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

    if len(counts) == 0:
        return 0
    best = counts[0]
    for c in counts[1:]:
        best = max(best, c)
    return best


def platform_test_count(df: pd.DataFrame, tolerance: float = 0.01, min_gap: int = 5) -> int:
    """平台位测试次数计数（PT 维度核心）

    统计价格对同一水平位的反复测试次数。
    2024年修正：两次测试间隔 < min_gap 根K线算同一次。
    过高点（创新高后回落>2%）不计入有效测试。

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

    # 取最近60根K线（T-028：核心循环 njit 化，逐位一致）
    n = min(60, len(df))
    return _platform_test_count_nb(close, high, n, tolerance, min_gap)


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
    window = min(window, len(df))
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
            and second_half_high > first_half_high * 0.95):
        return {"has_retracement": True, "quality": "good"}

    # 微弱回踩
    if second_half_high > first_half_high * 0.95:
        return {"has_retracement": True, "quality": "weak"}

    return {"has_retracement": False, "quality": "none"}


def pixelation_score(df: pd.DataFrame, window: int = 30) -> float:
    """像素感评分（0~1，越低越像素化=越差）

    老师："像素感直接pass掉，交投清淡不是真实意愿"

    综合三个维度：
    1) 影线占比 — 长影线多=像素感强
    2) K线连续性 — 同向K线比例低=像素感强
    3) 振幅一致性 — 单根异常振幅=像素感强

    Returns:
        0.0(严重像素感) ~ 1.0(完全正常)
    """
    if len(df) < window:
        return 0.5

    recent = df.tail(window)
    high = recent["最高"].values
    low = recent["最低"].values
    op = recent["开盘"].values
    cl = recent["收盘"].values

    # 1) 影线占比（实体 / 波幅）
    hl = high - low
    body = np.abs(cl - op)
    body_ratio = np.divide(body, hl, out=np.ones_like(body), where=hl > 0)
    body_ratio = np.clip(body_ratio, 0.0, 1.0)
    shadow_score = float(np.mean(body_ratio))  # 高=实体多=好

    # 2) K线连续性（连续同向比例；T-028 numpy 化：布尔逐元素比较 sum，整数计数逐位一致）
    same_dir = int(np.sum((cl[2:] >= cl[1:-1]) == (cl[1:-1] >= cl[:-2])))
    continuity = same_dir / max(len(cl) - 2, 1)
    # 太高或太低都不好，0.3~0.7 最佳（横盘整理）
    continuity_score = 1.0 - abs(continuity - 0.5) * 2
    continuity_score = max(0.0, continuity_score)

    # 3) 振幅一致性（异常振幅惩罚）
    ranges = hl
    mean_range = np.mean(ranges)
    if mean_range > 0:
        anomalies = np.sum(ranges > mean_range * 3) / len(ranges)
    else:
        anomalies = 0
    range_score = 1.0 - min(anomalies * 3, 1.0)  # 异常振幅越多分越低

    return float(shadow_score * 0.5 + continuity_score * 0.3 + range_score * 0.2)


def step_down_trace(df: pd.DataFrame, lookback: int = 20) -> dict:
    """向下踩的轨迹检测

    老师："无论依托还是回踩，必须有一个向下踩的动作"
    "向下踩到平台位后，下一根K线立即往上冲，速度快"

    检测模式：长下影线 → 紧接着阳线反弹

    Returns:
        {"has_trace": bool, "depth_pct": float, "rebound_pct": float, "quality": str}
    """
    if len(df) < 10:
        return {"has_trace": False, "depth_pct": 0, "rebound_pct": 0, "quality": "none"}

    recent = df.tail(lookback)
    op = recent["开盘"].values
    cl = recent["收盘"].values
    high = recent["最高"].values
    low = recent["最低"].values

    best_quality = 0.0
    best_result = {"has_trace": False, "depth_pct": 0, "rebound_pct": 0, "quality": "none"}

    for i in range(len(recent) - 1):
        body = abs(cl[i] - op[i])
        lower_shadow = min(op[i], cl[i]) - low[i]
        upper_shadow = high[i] - max(op[i], cl[i])

        # 长下影线：下影线 > 实体 × 1.5 且下影线 > 上影线 × 2
        if body > 0 and lower_shadow > body * 1.5 and lower_shadow > upper_shadow * 2:
            # 找到"踩"的动作
            depth_pct = lower_shadow / max(body, 0.001)

            # 下一根必须是阳线反弹
            abs(cl[i + 1] - op[i + 1])
            next_is_yang = cl[i + 1] > op[i + 1]
            next_hl = high[i + 1] - low[i + 1]

            if next_is_yang and next_hl > 0:
                rebound_pct = (cl[i + 1] - low[i + 1]) / next_hl  # 收盘靠近高点的程度
            else:
                rebound_pct = 0.3

            quality = depth_pct * 0.5 + rebound_pct * 0.5
            if quality > best_quality:
                best_quality = quality
                q_str = "good" if quality > 1.0 else ("weak" if quality > 0.5 else "none")
                best_result = {"has_trace": quality > 0.5, "depth_pct": round(depth_pct, 2),
                               "rebound_pct": round(rebound_pct, 2), "quality": q_str}

    return best_result


def conflict_zscore(df: pd.DataFrame, dn_start_idx: int | None = None,
                    consolidation_window: int = 20) -> float:
    """启动K线冲突感 z-score（量化"露头"判断）

    老师方法："把启动K线实体copy到调整结构末尾，看是否露头"

    量化：启动K实体 vs 调整结构K线实体的z-score
    z > 2.0 = 明显露头(S级冲突感)
    z > 1.5 = 露头(A级)
    z > 1.0 = 勉强露头(B级)
    z ≤ 1.0 = 不露头(C级)

    Returns:
        z-score 值
    """
    n = len(df)
    if n < consolidation_window + 3:
        return 0.0

    if dn_start_idx is None:
        dn_start_idx = n - 1  # 默认最后一根

    # 调整结构区域：DN启动前的K线（T-028 numpy 化：列一次提取，切片等价 df.iloc，逐位一致）
    cons_start = max(0, dn_start_idx - consolidation_window)
    cons_end = max(cons_start + 1, dn_start_idx)

    high = df["最高"].to_numpy()
    low = df["最低"].to_numpy()
    close = df["收盘"].to_numpy()
    op = df["开盘"].to_numpy()

    cons_hl = high[cons_start:cons_end] - low[cons_start:cons_end]
    cons_body = np.abs(close[cons_start:cons_end] - op[cons_start:cons_end])
    cons_ratio = np.divide(cons_body, cons_hl, out=np.ones_like(cons_body), where=cons_hl > 0)

    if len(cons_ratio) < 5 or np.std(cons_ratio) == 0:
        return 0.0

    cons_mean = np.mean(cons_ratio)
    cons_std = np.std(cons_ratio)

    # 启动K线（DN起始+后续最多3根合并）
    dn_end = min(dn_start_idx + 3, n)
    dn_hl = high[dn_start_idx:dn_end] - low[dn_start_idx:dn_end]
    dn_body = np.abs(close[dn_start_idx:dn_end] - op[dn_start_idx:dn_end])
    dn_ratio = np.divide(dn_body, dn_hl, out=np.ones_like(dn_body), where=dn_hl > 0)
    dn_max_ratio = np.max(dn_ratio) if len(dn_ratio) > 0 else 0

    z = (dn_max_ratio - cons_mean) / cons_std
    return round(float(z), 2)


def flatness_score(df: pd.DataFrame, window: int = 20) -> float:
    """横盘感评分（0~1，越高越横盘）

    老师："要有横盘感，斜率过高/凌厉直接pass"

    综合：
    1) 斜率 — 线性回归斜率(‰)，越小越好
    2) 离散度 — MAD/均价，越小越密集

    Returns:
        0.0(凌厉斜面) ~ 1.0(完美横盘)
    """
    if len(df) < window:
        return 0.5

    recent = df.tail(window)
    cl = recent["收盘"].values
    mid = (recent["最高"].values + recent["最低"].values) / 2

    # 1) 斜率评分
    x = np.arange(len(cl))
    try:
        slope = np.polyfit(x, mid, 1)[0]
        slope_abs = abs(slope)
    except Exception:
        slope_abs = 0.01

    avg_price = np.mean(mid)
    slope_permille = (slope_abs / avg_price * 1000) if avg_price > 0 else 0
    # <5‰=S, <10‰=A, <15‰=B
    if slope_permille < 5:
        slope_score = 1.0
    elif slope_permille < 10:
        slope_score = 0.7
    elif slope_permille < 15:
        slope_score = 0.4
    else:
        slope_score = 0.1

    # 2) 离散度评分
    mad_val = np.mean(np.abs(mid - np.mean(mid)))
    dispersion = mad_val / avg_price if avg_price > 0 else 1.0
    # <0.3%=S, <0.5%=A, <0.8%=B
    if dispersion < 0.003:
        dispersion_score = 1.0
    elif dispersion < 0.005:
        dispersion_score = 0.7
    elif dispersion < 0.008:
        dispersion_score = 0.4
    else:
        dispersion_score = 0.1

    return float(slope_score * 0.5 + dispersion_score * 0.5)


def reaction_quality(df: pd.DataFrame, lookback: int = 15) -> dict:
    """明显反应质量检测

    老师："真正的强反应：向下踩到平台位后，下一根K线立即往上冲，速度快"
    "碰一下慢慢蹭上去不算强特征"

    Returns:
        {"has_reaction": bool, "speed": float, "coverage": float, "quality": str}
    """
    if len(df) < 8:
        return {"has_reaction": False, "speed": 0, "coverage": 0, "quality": "none"}

    recent = df.tail(lookback)
    cl = recent["收盘"].values
    op = recent["开盘"].values
    high = recent["最高"].values
    low = recent["最低"].values

    for i in range(len(recent) - 3):
        # 找到回踩低点：下影线 + 收盘低于前一根
        body_i = abs(cl[i] - op[i])
        lower_shadow = min(op[i], cl[i]) - low[i]
        hl_i = high[i] - low[i]

        is_dip = (lower_shadow > body_i * 0.5 and cl[i] < cl[i - 1]
                  if i > 0 and hl_i > 0 else False)

        if not is_dip:
            continue

        # 看后续反弹：最多3根K线内的反应
        for j in range(i + 1, min(i + 4, len(recent))):
            rebound = cl[j] - cl[i]  # 从低点收盘的反弹
            hl_j = high[j] - low[j]
            body_j = abs(cl[j] - op[j])

            if rebound <= 0:
                continue

            # 反弹速度 = 反弹幅度 / 回调幅度
            dip_depth = high[max(0, i - 3):i + 1].max() - low[i]
            speed = rebound / dip_depth if dip_depth > 0 else 0

            # 反弹覆盖率 = 阳线实体 / 波幅
            coverage = body_j / hl_j if hl_j > 0 else 0

            # 综合质量
            quality_val = speed * 0.4 + coverage * 0.6

            if quality_val > 0.3:
                q_str = "good" if quality_val > 0.6 else ("weak" if quality_val > 0.4 else "none")
                return {"has_reaction": True, "speed": round(speed, 2),
                        "coverage": round(coverage, 2), "quality": q_str}

    return {"has_reaction": False, "speed": 0, "coverage": 0, "quality": "none"}


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


@njit(cache=True)
def _overshoot_detect_nb(close, high, n) -> tuple:
    """过高点检测核心（njit，T-028 2026-08-06）：与 Python 版逐位一致

    返回 (has_overshoot, position)；close 为全数组（close[i]/close[-1] 与原实现一致），
    recent_high 为 high[-n:] 视图；pre_low/post_low 用切片 min 扫描。
    """
    recent_close = close[-n:]
    recent_high = high[-n:]

    local_high_idx = -1
    local_high_val = 0.0
    for i in range(10, n - 5):
        if (recent_high[i] > recent_high[i - 1]
                and recent_high[i] >= recent_high[i - 2]
                and recent_high[i] > recent_high[i + 1]
                and recent_high[i] > recent_high[i + 2]
                and (close[i] - close[-1]) / close[i] > 0.02
                and recent_high[i] > local_high_val):
            local_high_val = recent_high[i]
            local_high_idx = i

    if local_high_idx >= 0:
        pre_low = recent_close[0]
        for k in range(1, local_high_idx):
            pre_low = min(pre_low, recent_close[k])
        post_low = recent_close[local_high_idx]
        for k in range(local_high_idx + 1, n):
            post_low = min(post_low, recent_close[k])
        if pre_low < post_low < local_high_val:
            return True, local_high_idx

    return False, -1


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
    recent_high = df["最高"].values[-n:]

    # 找局部最高点（T-028：njit 化，循环顺序/运算逐位一致）
    has, pos = _overshoot_detect_nb(close, recent_high, n)
    return {"has_overshoot": has, "position": pos}


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
    对应: 通道上涨检测。一元线性回归 R² = [corr(x,y)]²，避免逐窗口 polyfit。
    """
    x = np.arange(n)

    def calc_rsq(y):
        if len(y) < n or np.std(y) == 0:
            return np.nan
        return np.corrcoef(x, y)[0, 1] ** 2

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


# ══════════════════════════════════════════════════════════
# 意图模式检测（内训第14节两脉流程，2026-08-04 补课代码化）
# 一脉：独立结构 → 筹码集中区 → 成交量分布验证 POC → 有利突破或依托
# 二脉：关键位置三次测试 → 有利突破或明显反应回踩
# 硬约束：被回踩/被破位的必须是独立结构
# ══════════════════════════════════════════════════════════

def poc_level(df: pd.DataFrame, n_bins: int = 30, window: int = 120) -> dict:
    """POC（成交量分布控制点）检测

    老师（内训第8节）：价格停留时间长的价位 = 共识区；筹码集中度高 = 未来走势有规律
    POC = 成交量加权价格分布的最大点（量价分箱，每箱累加箱内成交量）

    Args:
        df: K线DataFrame
        n_bins: 价格分箱数
        window: 参与计算的最近K线数

    Returns:
        {"poc": float, "concentration": float, "top3": [float,...]}
        concentration = POC箱成交量占比（越高越集中）
    """
    w = min(window, len(df))
    if w < 30:
        return {"poc": 0.0, "concentration": 0.0, "top3": []}
    d = df.tail(w)
    # 典型价（含最高/最低的加权代表价）——价格停留时间近似
    prices = (d["最高"].values + d["最低"].values + d["收盘"].values) / 3.0
    vols = d["成交量"].values.astype(float)
    hist, edges = np.histogram(prices, bins=n_bins, weights=vols)
    total = hist.sum()
    if total <= 0:
        return {"poc": 0.0, "concentration": 0.0, "top3": []}
    # POC = 最大量箱中点
    peak = int(np.argmax(hist))
    poc = (edges[peak] + edges[peak + 1]) / 2.0
    concentration = float(hist[peak] / total)
    # 前3大量箱（多筹码集中区识别）
    top3_idx = np.argsort(hist)[-3:][::-1]
    top3 = [float((edges[i] + edges[i + 1]) / 2.0) for i in sorted(top3_idx)]
    return {"poc": float(poc), "concentration": concentration, "top3": top3}


def accumulation_zone(df: pd.DataFrame, n_bins: int = 30, top_ratio: float = 0.6,
                      window: int = 120) -> dict:
    """筹码集中区检测：覆盖 top_ratio 成交量的最窄价格带占总价格带比例

    老师（内训第8节）：筹码集中度高 = 趋势延展更优；"停留时间长 = 共识"
    zone_ratio 越小 = 筹码越集中

    Args:
        df: K线DataFrame
        n_bins: 价格分箱数
        top_ratio: 覆盖的成交量比例（0.6 = 60% 成交量）
        window: 最近K线数

    Returns:
        {"concentrated": bool, "zone_ratio": float, "poc_near_current": bool}
        concentrated = zone_ratio < 0.4（60%量在 40% 价格带内 = 集中）
    """
    w = min(window, len(df))
    if w < 30:
        return {"concentrated": False, "zone_ratio": 1.0, "poc_near_current": False}
    d = df.tail(w)
    prices = (d["最高"].values + d["最低"].values + d["收盘"].values) / 3.0
    vols = d["成交量"].values.astype(float)
    total = vols.sum()
    if total <= 0:
        return {"concentrated": False, "zone_ratio": 1.0, "poc_near_current": False}
    # 按价格排序，滑动窗口找覆盖 top_ratio 成交量的最窄价格带
    order = np.argsort(prices)
    ps = prices[order]
    vs = vols[order]
    cum = np.cumsum(vs) / total
    full = ps[-1] - ps[0] if ps[-1] > ps[0] else 1.0
    best_span = full
    i = 0
    for j in range(len(ps)):
        while i <= j and cum[j] - (cum[i - 1] if i > 0 else 0.0) >= top_ratio:
            span = ps[j] - ps[i]
            best_span = min(best_span, span)
            i += 1
    zone_ratio = float(best_span / full) if full > 0 else 1.0
    concentrated = zone_ratio < 0.4
    # POC 是否接近当前价（当前价在 POC 附近 = 正在关键位）
    poc = poc_level(df, n_bins=n_bins, window=window)["poc"]
    cur = float(d["收盘"].iloc[-1])
    poc_near = abs(cur - poc) / poc < 0.05 if poc > 0 else False
    return {"concentrated": concentrated, "zone_ratio": zone_ratio, "poc_near_current": poc_near}


def support_bounce(df: pd.DataFrame, levels: list[float] | None = None,
                   tol: float = 0.02, lookback: int = 15) -> dict:
    """依托/支撑反弹检测

    老师（2024-06-03拍板定义）：依托 = 关键位附近直接有力突破起稳；
    回踩 = 脱离后明显下探再确认（"向下踩的轨迹必须存在"）
    本函数检测：近期回调到关键位附近后是否有"支撑反弹"特征（下影线 + 收回）

    Args:
        df: K线DataFrame
        levels: 关键价位列表（缺省用支撑阻力检测）
        tol: 关键位容差（比例）
        lookback: 近期窗口K线数

    Returns:
        {"has_support": bool, "bounce": float, "level": float|None, "reason": str}
    """
    w = min(lookback, len(df))
    if w < 10:
        return {"has_support": False, "bounce": 0.0, "level": None, "reason": "数据不足"}
    d = df.tail(w)
    high = d["最高"].values
    low = d["最低"].values
    close = d["收盘"].values
    if levels is None or len(levels) == 0:
        levels = support_resistance_levels(df)
    if len(levels) == 0:
        return {"has_support": False, "bounce": 0.0, "level": None, "reason": "无关键位"}
    # 近期最低点
    min_idx = int(np.argmin(low))
    min_p = float(low[min_idx])
    if min_p <= 0:
        return {"has_support": False, "bounce": 0.0, "level": None, "reason": "价格异常"}
    # 低点是否贴近某个关键位
    near_level = None
    for lv in levels:
        if lv > 0 and abs(lv - min_p) / lv <= tol:
            near_level = float(lv)
            break
    if near_level is None:
        return {"has_support": False, "bounce": 0.0, "level": None, "reason": "低点不在关键位附近"}
    # 下影线确认（最低点那根的收盘在低点上方）
    rng = high[min_idx] - min_p
    has_shadow = rng > 0 and (close[min_idx] - min_p) / rng > 0.5
    # 反弹确认：低点后收盘高于低点一定幅度（向下踩后收回 = 轨迹存在）
    after_close = close[min_idx:]
    bounce = (after_close[-1] - min_p) / min_p if len(after_close) >= 2 else 0.0
    has_support = has_shadow and bounce > 0.03
    reason = f"依托{bounce:.1%}(关键位{near_level:.2f})" if has_support else f"回调无依托(bounce={bounce:.1%})"
    return {"has_support": has_support, "bounce": float(bounce), "level": near_level, "reason": reason}


# ══════════════════════════════════════════════════════════
# 环境判定与逆转检测（2026-08-04 补课代码化）
# 0.5R 环境判定：老师（2024-06-22/29）"环境不好（右下角）→ 0.5R"
# A 段逆转 3:1：内训第六节 "完全逆转 = 动能四要素至少 3:1 胜过最后一段运行"
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
# G3 分步建仓·收线确认（2026-08-06 工程接入 · 2024-06-29 周会原文定案）
# 原文（周会录屏/raw/2024-06-29周会.txt）：
#   「就收到这 我会先进个二分之一啊 然后等下一个收线 下一个收线
#     比如说收下去 我再进二分之一啊」
#   「你0.5R是百分之百 因为认为前面有问题 才做0.5R」
#   「然后回过头来仔细看 觉得优势不突出 动能无法接受 就马上平仓了」
# 定案语义（知识卡 经验型模式/知识卡.md 仓位与环境节 2026-08-06 标注）：
#   0.5R = 分步建仓的第一步（非终局减半）——先进 0.5R → 下一根 K 线收线确认
#   （收下去/动能接受）→ 再补 0.5R（总 1R）；收线不确认 → 马上平仓。
# 确认规则（可解释，纯基础K线列；判定风格对齐 exit_manager）：
#   C1 收下去：确认日收盘 ≥ 进场价（未跌回进场价下方）
#   C2 动能延续：确认日收盘 ≥ 开仓日收盘（未转弱）
#   C3 非动能拒绝形态：非"放量阴线"（量比>1.5 且 收阴 = 放量抛压，动能被拒绝）
#   三条件全满足 = 确认；任一不满足 → 不确认（平仓 0.5R）。
#   止损优先：确认日最低 ≤ 止损价 → 层面1 止损出场（先于确认判定）。
# ══════════════════════════════════════════════════════════

def confirm_conditions(df: pd.DataFrame, entry_price: float,
                        stop_loss: float) -> dict:
    """三条件独立评估（单一来源 · 2026-08-06 老板拍板 1B 对照实验抽出）

    现状严格版 half_position_confirm（C1&C2&C3）与放宽版
    half_position_confirm_relaxed（any2/no_c2）共用本评估，规则不复制。
    行为与重构前完全一致（仅抽出公共评估，组合逻辑不变）。

    Args:
        df: K线DataFrame，最后两根 = 开仓日 K 线 + 确认收线（不足两根 → wait）
        entry_price: 0.5R 起步进场价
        stop_loss: 结构止损价（层面1，先于确认判定）

    Returns:
        {"wait": bool, "stopped": bool, "close": float,
         "c1": bool, "c2": bool, "c3": bool, "reject_vol": bool}
        wait=True → 收线未出现（不足两根），继续持有等待
        stopped=True → 确认日已触止损（层面1 优先）
        c1 收下去：确认日收盘 ≥ 进场价
        c2 动能延续：确认日收盘 ≥ 开仓日收盘
        c3 非放量阴线：非（量比>1.5 且收阴）
    """
    if len(df) < 2:
        return {"wait": True, "stopped": False, "close": 0.0,
                "c1": False, "c2": False, "c3": True, "reject_vol": False}
    conf = df.iloc[-1]
    open_day = df.iloc[-2]
    conf_close = float(conf["收盘"])
    conf_low = float(conf["最低"])
    # 止损优先（层面1：结构止损位先于确认判定——触止损即平，无论动能）
    if conf_low <= stop_loss:
        return {"wait": False, "stopped": True, "close": conf_close,
                "c1": False, "c2": False, "c3": True, "reject_vol": False}
    conf_open = float(conf["开盘"])
    open_close = float(open_day["收盘"])
    # C3 动能拒绝形态：放量阴线（量比>1.5 且收阴 = 放量抛压吞没，动能被拒绝）
    reject_vol = False
    if "成交量" in df.columns and len(df) >= 6:
        vol = float(conf["成交量"])
        vol_ma5 = float(df["成交量"].iloc[-6:-1].mean())
        reject_vol = vol_ma5 > 0 and vol > vol_ma5 * 1.5 and conf_close < conf_open
    return {"wait": False, "stopped": False, "close": conf_close,
            "c1": conf_close >= entry_price,
            "c2": conf_close >= open_close,
            "c3": not reject_vol,
            "reject_vol": reject_vol}


def half_position_confirm(df: pd.DataFrame, entry_price: float, stop_loss: float) -> dict:
    """0.5R 分步建仓·下一根收线确认（G3 · 2024-06-29 周会原文）

    语义：0.5R = 分步建仓第一步——先进 0.5R，下一根收线确认（收下去/动能接受）
    则补 0.5R（总 1R）；收线不确认（优势不突出/动能无法接受）→ 马上平仓。
    出处见模块上方 G3 定案注释。本函数 = 现状严格版（C1&C2&C3 全满足才确认）；
    放宽版见 half_position_confirm_relaxed（2026-08-06 老板拍板 1B 对照实验）。

    Args:
        df: K线DataFrame，最后两根 = 开仓日 K 线 + 确认收线（调用方负责切片；
            模拟层 sim_trading / 回测层 tracking 同源调用）
        entry_price: 0.5R 起步进场价
        stop_loss: 结构止损价（层面1，先于确认判定）

    Returns:
        {"confirmed": bool, "stopped": bool, "reject": bool, "wait": bool,
         "close": float, "reason": str}
        wait=True → 收线未出现（不足两根），继续持有等待
        stopped=True → 确认日已触止损（层面1 优先，按止损价平仓）
        confirmed=True → 收线确认（补 0.5R，总 1R）
        reject=True → 收线不确认 → 马上平仓（"觉得优势不突出，动能无法接受"）
    """
    cond = confirm_conditions(df, entry_price, stop_loss)
    if cond["wait"]:
        return {"confirmed": False, "stopped": False, "reject": False, "wait": True,
                "close": 0.0, "reason": "收线未出现（等待下一根）"}
    if cond["stopped"]:
        return {"confirmed": False, "stopped": True, "reject": False, "wait": False,
                "close": cond["close"], "reason": "止损触发(层面1)"}
    if cond["c1"] and cond["c2"] and cond["c3"]:
        return {"confirmed": True, "stopped": False, "reject": False, "wait": False,
                "close": cond["close"], "reason": "收线确认（收下去/动能接受）"}
    parts = []
    if not cond["c1"]:
        parts.append("收盘跌破进场价")
    if not cond["c2"]:
        parts.append("收盘较开仓日转弱")
    if cond["reject_vol"]:
        parts.append("放量阴线(动能拒绝)")
    return {"confirmed": False, "stopped": False, "reject": True, "wait": False,
            "close": cond["close"], "reason": "收线未确认：" + "/".join(parts)}


def half_position_confirm_relaxed(df: pd.DataFrame, entry_price: float,
                                  stop_loss: float, mode: str = "any2") -> dict:
    """0.5R 确认规则放宽版（2026-08-06 老板拍板 1B 对照实验 · 数据驱动定案）

    与现状版关系：与 half_position_confirm 共用 confirm_conditions 条件评估
    单一来源，仅组合逻辑放宽——现状严格版 = C1 且 C2 且 C3（全满足才确认）；
    放宽版任一不满足不再一票否决：
      any2  三取二：任意两条件满足即确认（C1&C2 | C1&C3 | C2&C3）
      no_c2 去动能延续：保留 C1 收下去 + C3 非放量阴线（C2 单独不再拦截）
    止损层面1 优先逻辑与现状版完全一致（放宽不改变止损行为）。

    Args / Returns: 同 half_position_confirm（wait/stopped/confirmed/reject/
        close/reason 结构一致；reason 标注放宽模式与放行条件）
    """
    cond = confirm_conditions(df, entry_price, stop_loss)
    if cond["wait"]:
        return {"confirmed": False, "stopped": False, "reject": False, "wait": True,
                "close": 0.0, "reason": "收线未出现（等待下一根）"}
    if cond["stopped"]:
        return {"confirmed": False, "stopped": True, "reject": False, "wait": False,
                "close": cond["close"], "reason": "止损触发(层面1)"}
    if mode == "any2":
        n_ok = int(cond["c1"]) + int(cond["c2"]) + int(cond["c3"])
        ok = n_ok >= 2
    elif mode == "no_c2":
        ok = cond["c1"] and cond["c3"]
    else:
        raise ValueError(f"未知放宽模式: {mode!r}（支持 any2 / no_c2）")
    if ok:
        on = [n for n, v in (("C1", cond["c1"]), ("C2", cond["c2"]), ("C3", cond["c3"])) if v]
        return {"confirmed": True, "stopped": False, "reject": False, "wait": False,
                "close": cond["close"], "reason": f"放宽确认({mode}:" + "&".join(on) + ")"}
    parts = []
    if not cond["c1"]:
        parts.append("收盘跌破进场价")
    if not cond["c2"]:
        parts.append("收盘较开仓日转弱")
    if cond["reject_vol"]:
        parts.append("放量阴线(动能拒绝)")
    return {"confirmed": False, "stopped": False, "reject": True, "wait": False,
            "close": cond["close"], "reason": f"收线未确认(放宽{mode})：" + "/".join(parts)}


def half_position_confirm_delay2(df: pd.DataFrame, entry_price: float,
                                 stop_loss: float, conf_idx: int,
                                 max_idx: int | None = None) -> dict:
    """0.5R 确认·delay2 延迟二次确认判定（2026-08-06 老板拍板替换 strict 为生产规则）

    与 phase_confirm_from_kline(confirm_mode="delay2") 同一语义同一来源
    （首根判定 + reject 时二次判定；T+1 触止损 → 仍按止损出场不等待），
    供 引擎回测 tracking._phase_in_track / 模拟层 sim_trading._check_half_position
    共用（回放层走 phase_confirm_from_kline，规则不复制）。

    Args:
        df: K线DataFrame（升序）
        entry_price: 0.5R 起步进场价
        stop_loss: 结构止损价（层面1 优先）
        conf_idx: 首根确认收线索引（开仓日 = conf_idx-1）
        max_idx: 二次判定可用收线索引上限（None = 数据边界 len(df)-1；
            引擎回测传 hold 窗口末 end——窗口外无 T+2 → 按首根判定）

    Returns:
        同 half_position_confirm（wait/stopped/confirmed/reject/close/reason），
        另附：
          conf_idx_used: 实际判定收线索引（首根=conf_idx / 二次=conf_idx+1）
          second_checked: 是否执行了二次判定（False = 首根 reject 但无 T+2——
             调用方按场景处理：回测=按首根平仓 / 模拟层实时=等待 T+2）
    """
    v1 = half_position_confirm(df.iloc[:conf_idx + 1], entry_price, stop_loss)
    v1["conf_idx_used"] = conf_idx
    if not v1["reject"]:
        v1["second_checked"] = False
        return v1
    limit = len(df) - 1 if max_idx is None else max_idx
    if conf_idx + 1 <= limit:
        v2 = half_position_confirm(df.iloc[:conf_idx + 2], entry_price, stop_loss)
        v2["conf_idx_used"] = conf_idx + 1
        v2["second_checked"] = True
        return v2
    v1["second_checked"] = False
    return v1


def environment_quality(df: pd.DataFrame, window: int = 60) -> dict:
    """市场环境质量判定（0.5R 机制的环境部分）

    老师（2024-06-22/29）：环境好（非右下角）→ 正常 1R；环境不好（右下角）→ 0.5R
    右下角特征量化：结构质量低（近期弱势）+ 反弹无力（低点不断下移/横盘无动能）

    Args:
        df: K线DataFrame
        window: 环境评估窗口

    Returns:
        {"quality": "good"/"weak"/"bad", "signal": float, "reason": str}
        good → 1R；weak/bad → 0.5R（环境差降仓）
    """
    w = min(window, len(df))
    if w < 30:
        return {"quality": "good", "signal": 0.0, "reason": "数据不足默认正常"}
    d = df.tail(w)
    close = d["收盘"].values
    high = d["最高"].values
    low = d["最低"].values
    # 1) 近期趋势：窗口内低点是否持续下移（右下角 = 弱势下行/横盘无动能）
    half = w // 2
    low_front = low[:half].min()
    low_back = low[half:].min()
    down_trend = low_back <= low_front * 0.97  # 后半段创新低 ≥3%
    # 2) 反弹力度：窗口内最大反弹 vs 最大回撤
    cur = close[-1]
    peak = high.max()
    trough = low.min()
    bounce = (cur - trough) / trough if trough > 0 else 0
    weak_bounce = bounce < 0.08
    # 3) 结构质量：横盘波幅（过低 = 死水无动能）
    rng = (peak - trough) / cur if cur > 0 else 0
    stagnant = rng < 0.10
    # 综合判定（右下角 = 弱势 + 反弹无力）
    bad_flags = [down_trend, weak_bounce, stagnant]
    n_bad = sum(bad_flags)
    if n_bad >= 2:
        quality = "bad"
        reason = f"环境差(右下角): 创新低{down_trend}/反弹弱{weak_bounce}/横盘{stagnant}"
    elif n_bad == 1:
        quality = "weak"
        reason = f"环境偏弱: {[f for f,b in zip(['下行','弱反弹','横盘'],bad_flags) if b]}"
    else:
        quality = "good"
        reason = "环境正常"
    return {"quality": quality, "signal": float(n_bad), "reason": reason}


def phase_confirm_from_kline(df: pd.DataFrame, signal_date: str,
                             entry_price: float, stop_loss: float,
                             confirm_mode: str = "strict") -> dict:
    """0.5R 分步建仓·信号日→触发日→次日收线确认 完整回放（G3 2026-08-06）

    供 ② sim_capital.half_phase 资金占用模拟 与 ③ 确认规则质量验证回放 共用——
    触发日定位与回测层 tracking._track_prebreak 同规则（信号日 T 之后首根
    最高 ≥ 触发价的 K 线为触发日/入场日），确认判定单一来源 half_position_confirm
    （strict：C1 收下去 / C2 动能延续 / C3 非放量阴线 / 止损层面1 优先），
    confirm_mode 参数化放宽（2026-08-06 老板拍板 1B 对照实验），不复制规则：
      strict 现状版（默认，行为零变化）= C1&C2&C3 全满足才确认
      any2   三取二 = 任意两条件满足即确认
      no_c2  去动能延续 = C1 收下去 + C3 非放量阴线
      delay2 延迟二次确认 = 首根（T+1）未确认时允许下一根（T+2）再确认一次
             （以 T+1 为开仓日重新判定；T+1 触止损 → 仍按止损出场不等待；
              T+2 仍未确认 → 以 T+2 收盘平仓；无 T+2 → 按首根判定）

    Args:
        df: 个股日K线（日期/开盘/收盘/最高/最低/成交量，升序）
        signal_date: 信号日（YYYY-MM-DD；prebreak 模式信号日后才可能触发）
        entry_price: 0.5R 起步进场价（prebreak = 触发价）
        stop_loss: 结构止损价
        confirm_mode: strict / any2 / no_c2 / delay2（默认 strict = 现状行为）

    Returns:
        {"confirmed"/"stopped"/"reject": bool, "close": float,
         "trigger_date"/"confirm_date": str, "reason": str, "wait": bool}
        wait=True → 信号日后未触发（数据窗口不足/无触发 K 线），调用方放行处理
        trigger_date = 入场日；confirm_date = 入场日次日（收线确认日；
        delay2 模式为实际确认/平仓日 T+1 或 T+2）
    """
    dates = df["日期"].astype(str).str[:10].values
    highs = df["最高"].values
    trig_idx = None
    for i, d in enumerate(dates):
        if d > signal_date and highs[i] >= entry_price:
            trig_idx = i
            break
    if trig_idx is None:
        return {"confirmed": False, "stopped": False, "reject": False, "wait": True,
                "close": 0.0, "trigger_date": "", "confirm_date": "", "reason": "信号日后未触发"}
    conf_idx = trig_idx + 1
    if conf_idx >= len(df):
        return {"confirmed": False, "stopped": False, "reject": False, "wait": True,
                "close": 0.0, "trigger_date": str(dates[trig_idx])[:10],
                "confirm_date": "", "reason": "确认收线未出现（数据窗口不足）"}

    def _judge(idx: int) -> dict:
        """idx 收线（开仓日 = idx-1）按 confirm_mode 判定"""
        if confirm_mode in ("any2", "no_c2"):
            return half_position_confirm_relaxed(df.iloc[:idx + 1], entry_price,
                                                 stop_loss, mode=confirm_mode)
        return half_position_confirm(df.iloc[:idx + 1], entry_price, stop_loss)

    verdict = _judge(conf_idx)
    if confirm_mode == "delay2":
        # delay2 延迟二次确认（规则单一来源 half_position_confirm_delay2，
        # 与引擎 tracking / 模拟层 sim_trading 同式不复制）
        v = half_position_confirm_delay2(df, entry_price, stop_loss, conf_idx)
        return {
            "confirmed": v["confirmed"], "stopped": v["stopped"],
            "reject": v["reject"], "close": v["close"],
            "trigger_date": str(dates[trig_idx])[:10],
            "confirm_date": str(dates[v["conf_idx_used"]])[:10],
            "reason": v["reason"], "wait": False,
        }
    return {
        "confirmed": verdict["confirmed"], "stopped": verdict["stopped"],
        "reject": verdict["reject"], "close": verdict["close"],
        "trigger_date": str(dates[trig_idx])[:10],
        "confirm_date": str(dates[conf_idx])[:10],
        "reason": verdict["reason"], "wait": False,
    }


def reversal_3to1(df: pd.DataFrame, run_start: int | None = None) -> dict:
    """A 段逆转判定（内训第六节：完全逆转 = 动能四要素至少 3:1 胜过最后一段运行）

    四要素：大小（幅度）/ 连续性（阳线占比）/ 斜率 / 量能
    至少 3 个要素强于最后一段运行（4:0 或 3:1，允许一项不如或打平）= 完全逆转

    Args:
        df: K线DataFrame
        run_start: 最后一段运行的起始索引（缺省取最近 20 根为运行段）

    Returns:
        {"reversed": bool, "win": int, "elements": dict}
    """
    n = len(df)
    if n < 40:
        return {"reversed": False, "win": 0, "elements": {}}
    high = df["最高"].values
    low = df["最低"].values
    close = df["收盘"].values
    op = df["开盘"].values
    vol = df["成交量"].values.astype(float) if "成交量" in df.columns else None

    # 最后一段运行 = 最近 15 根（可传入 run_start 精确指定）
    r_end = n
    r_start = run_start if run_start is not None else max(0, n - 15)
    if r_start >= r_end - 5:
        r_start = max(0, n - 15)

    # 逆转段 = 最后 5 根（当前强势运行）
    c_start = max(0, n - 5)
    if c_start <= r_start:
        return {"reversed": False, "win": 0, "elements": {}}

    def _elements(s: int, e: int) -> dict:
        seg_h, seg_l = high[s:e], low[s:e]
        seg_c, seg_o = close[s:e], op[s:e]
        seg_v = vol[s:e] if vol is not None else None
        span = (seg_h.max() - seg_l.min()) / seg_c.mean() if seg_c.mean() > 0 else 0
        yang = np.mean(seg_c > seg_o) if len(seg_c) else 0
        slope = (seg_c[-1] - seg_c[0]) / seg_c[0] if seg_c[0] > 0 else 0
        vratio = (seg_v.mean() / np.mean(vol[:s]) if vol is not None and s > 5 and np.mean(vol[:s]) > 0 else 0)
        return {"span": span, "yang": yang, "slope": abs(slope), "vol": vratio}

    ref = _elements(r_start, r_end)
    cur = _elements(c_start, r_end)
    wins = 0
    details = {}
    for k in ("span", "yang", "slope", "vol"):
        better = cur[k] > ref[k] * 1.1 if k != "vol" else cur[k] > max(ref[k] * 1.1, 0.1)
        # vol 特殊：参考段有量才比
        details[k] = {"ref": round(ref[k], 3), "cur": round(cur[k], 3), "better": better}
        if better:
            wins += 1
    return {"reversed": wins >= 3, "win": wins, "elements": details}


# ══════════════════════════════════════════════════════════
# 品种筛选一票否决（补完计划 G1/G2 · 2026-08-06 工程接入）
# G1 经常跳空/涨跌停：品种筛选/知识卡.md 一票否决#4「经常跳空/涨跌停品种——连续性不好」
#   业务影响：跳空跳过止损——本应"亏 1R 止损"，实际跳空/一字封板成交在更差价位
# G2 一字形排列：品种筛选/知识卡.md 一票否决#5「一字形排列（调整全是一字形）」
#   业务影响：连续性差的极端形态，调整无意义，进场后无法按规则管理
# ══════════════════════════════════════════════════════════
# G1 阈值定案（2026-08-06 · 第二批校准，全市场 5067 只 qfq 扫描，见
# 产出/输出/数据/g1_gap_scan/scan_gap_thresholds.py 与 threshold_table.md）：
#   现状基线（9.5% 统一线 + 3% 跳空 + 3 次）排除率 主板 43.0% / 全市场 48.3%
#   ——远超"少数派"定位（知识卡语义：经常跳空/涨跌停应是少数品种，参考
#   ST 一票否决定位），且 9.5% 线对 20cm 票把常态波动算成涨跌停（创业板
#   62.7% / 科创 76.5% 的票 60 根内出现过 ≥9.5% 波动，而 ≥19.5% 真封板线
#   仅 23.5% / 24.5%）。
#   定案组合：跳空 4% + 涨跌停分板块（主板 9.5% / 20cm 19.5%）+ 60 根内
#   合计 ≥5 次 → 排除率：沪主板 23.3% / 深主板 26.0% / 创业板 13.2% /
#   科创 22.8% / 20cm 16.1% / 全市场 21.3%（剔除 ST 后口径），全部落在
#   目标区间 10-25%（知识卡"经常"= 60 根一个季度内 5 次剧烈事件，
#   平均 12 根一次；20cm 真封板线 19.5% 有数据支撑：≥3 次仅占 1.8%/2.7%）。
# ══════════════════════════════════════════════════════════

GAP_LIMIT_WINDOW = 60        # 检测窗口（与 Tier0 结构检测 60 根一致）
LIMIT_TOUCH_PCT_MAIN = 0.095  # 主板涨跌停判定线：封板线 10% 含 0.5% 容差
LIMIT_TOUCH_PCT_20CM = 0.195  # 创业/科创 20cm 判定线：封板线 20% 含 0.5% 容差
                             # （2026-08-06 定案：20cm 分线，9.5% 会把常态波动算成涨跌停）
GAP_PCT = 0.04               # 跳空判定线：|开盘/前收盘-1| ≥ 4%（2026-08-06 定案：3%→4%）
GAP_LIMIT_FREQ = 5           # 窗口内 跳空+涨跌停 合计 ≥ 5 次 = "经常"（2026-08-06 定案：3→5，
                             # 60 根一个季度内平均 12 根一次剧烈事件）
ONE_LINE_WINDOW = 60       # 一字形检测窗口（与 G1 一致）
ONE_LINE_AMP = 0.001       # 一字形振幅上限：|最高-最低|/前收盘 < 0.1%（几乎零波动；
                           # 2026-08-06 实测校准：0.5% 会把 20 元级窄幅横盘股日振幅误判
                           # 为一字形（真实一字板振幅=0，0.1% 容差足够））
ONE_LINE_FREQ = 3          # 窗口内一字形 ≥ 3 根 = "排列"（平均 20 根一根）


def board_limit_pct(code: str | None) -> float:
    """按股票代码返回涨跌停判定线（G1 分板块口径 · 2026-08-06 定案）

    创业板（300/301）/ 科创板（688/689）= 20cm 票 → 19.5%；
    主板（60x/00x）及其他 → 9.5%。判定依据见模块头部 G1 定案注释。

    Args:
        code: 股票代码（无代码上下文 → 主板线 9.5%，保守侧）

    Returns:
        涨跌停判定线（0.095 或 0.195）
    """
    if code and code.startswith(("300", "301", "688", "689")):
        return LIMIT_TOUCH_PCT_20CM
    return LIMIT_TOUCH_PCT_MAIN


def gap_limit_detect(df: pd.DataFrame, limit_pct: float | None = None) -> dict:
    """经常跳空/涨跌停检测（品种筛选一票否决#4，G1）

    知识卡原文：「经常跳空/涨跌停品种——连续性不好」（2024-07-16 扫盘）。
    连续性差 = 止损可能被跳空跳过（本应亏 1R 止损，实际跳空/封板成交更差价位）。
    另含当日事件检查：最新一根一字涨停（无法买入）或一字跌停（止损无法卖出）→ 排除。

    板块口径（2026-08-06 定案）：涨跌停线按板块分——主板 9.5%、20cm 19.5%
    （9.5% 对 20cm 票会把常态波动算成涨跌停）。板块来源优先级：
    limit_pct 显式传入 > df.attrs["code"]（scanner/回测引擎已设置）> 主板线默认。

    Args:
        df: K线DataFrame（需 开盘/最高/最低/收盘 列）
        limit_pct: 涨跌停判定线（None=自动按代码板块判定）

    Returns:
        {"excluded": bool, "limit_days": int, "gap_days": int, "latest_block": bool,
         "reason": str}
        excluded=True → 触发一票否决
    """
    if limit_pct is None:
        limit_pct = board_limit_pct(df.attrs.get("code"))
    w = min(GAP_LIMIT_WINDOW, len(df))
    if w < 30:
        return {"excluded": False, "limit_days": 0, "gap_days": 0,
                "latest_block": False, "reason": "数据不足"}
    d = df.tail(w).reset_index(drop=True)
    close = d["收盘"].values.astype(float)
    op = d["开盘"].values.astype(float)
    hi = d["最高"].values.astype(float)
    lo = d["最低"].values.astype(float)

    limit_days = 0
    gap_days = 0
    for i in range(1, len(d)):
        prev_close = close[i - 1]
        if prev_close <= 0:
            continue
        chg = close[i] / prev_close - 1.0
        if abs(chg) >= limit_pct:
            limit_days += 1
        if abs(op[i] / prev_close - 1.0) >= GAP_PCT:
            gap_days += 1

    # 当日事件：最新一根一字封板（开=高=低=收 且 触涨跌停线）
    latest_block = False
    prev_close = close[-2] if len(close) >= 2 else op[-1]
    if prev_close > 0:
        chg = close[-1] / prev_close - 1.0
        one_price = hi[-1] == lo[-1] and lo[-1] == op[-1] and op[-1] == close[-1]
        latest_block = one_price and abs(chg) >= limit_pct

    total = limit_days + gap_days
    excluded = total >= GAP_LIMIT_FREQ or latest_block
    if excluded:
        parts = []
        if total >= GAP_LIMIT_FREQ:
            parts.append(f"60根内涨跌停{limit_days}次+跳空{gap_days}次")
        if latest_block:
            parts.append("最新日一字封板" + ("涨停" if chg > 0 else "跌停"))
        reason = "/".join(parts)
    else:
        reason = ""
    return {"excluded": excluded, "limit_days": limit_days, "gap_days": gap_days,
            "latest_block": latest_block, "reason": reason}


def one_line_detect(df: pd.DataFrame) -> dict:
    """一字形排列检测（品种筛选一票否决#5，G2）

    知识卡原文：「一字形排列（调整全是一字形）」（2024-07-25 扫盘）。
    一字形 = 开盘即封板无波动（开≈高≈低≈收），连续性差且无法按规则管理。

    Args:
        df: K线DataFrame（需 开盘/最高/最低/收盘 列）

    Returns:
        {"excluded": bool, "one_line_count": int, "reason": str}
        excluded=True → 触发一票否决
    """
    w = min(ONE_LINE_WINDOW, len(df))
    if w < 30:
        return {"excluded": False, "one_line_count": 0, "reason": "数据不足"}
    d = df.tail(w).reset_index(drop=True)
    hi = d["最高"].values.astype(float)
    lo = d["最低"].values.astype(float)
    cl = d["收盘"].values.astype(float)
    op = d["开盘"].values.astype(float)

    n_one = 0
    for i in range(len(d)):
        prev_close = cl[i - 1] if i > 0 else op[i]
        if prev_close <= 0:
            continue
        amp = (hi[i] - lo[i]) / prev_close
        body = abs(cl[i] - op[i]) / prev_close
        if amp < ONE_LINE_AMP and body < ONE_LINE_AMP:
            n_one += 1
    excluded = n_one >= ONE_LINE_FREQ
    return {"excluded": excluded, "one_line_count": n_one,
            "reason": f"60根内一字形{n_one}根" if excluded else ""}
