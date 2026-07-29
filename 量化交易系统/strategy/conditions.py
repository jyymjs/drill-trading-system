"""常用筛选条件函数库

每个条件是一个函数，输入带指标的DataFrame，输出 bool。
可在策略中组合使用。
"""
import pandas as pd


def has_ma_cross(df: pd.DataFrame, short: str = "MA5", long: str = "MA20") -> bool:
    """最近一天是否出现均线金叉"""
    if len(df) < 2:
        return False
    cross = df["MA_CROSS"]
    return cross.iloc[-1] == 1


def has_ma_dead_cross(df: pd.DataFrame, short: str = "MA5", long: str = "MA20") -> bool:
    """最近一天是否出现均线死叉"""
    if len(df) < 2:
        return False
    cross = df["MA_CROSS"]
    return cross.iloc[-1] == -1


def price_above_ma(df: pd.DataFrame, ma_col: str = "MA20") -> bool:
    """收盘价在均线上方"""
    if df.empty or ma_col not in df.columns:
        return False
    return df["收盘"].iloc[-1] > df[ma_col].iloc[-1]


def price_above_ma_all(df: pd.DataFrame, ma_cols: list[str] = None) -> bool:
    """收盘价在所有指定均线上方（多头排列）"""
    if ma_cols is None:
        ma_cols = ["MA5", "MA10", "MA20", "MA60"]
    close = df["收盘"].iloc[-1]
    return all(
        close > df[col].iloc[-1]
        for col in ma_cols
        if col in df.columns and pd.notna(df[col].iloc[-1])
    )


def ma_aligned_bullish(df: pd.DataFrame) -> bool:
    """均线多头排列: MA5 > MA10 > MA20 > MA60"""
    cols = ["MA5", "MA10", "MA20", "MA60"]
    vals = []
    for c in cols:
        if c in df.columns and pd.notna(df[c].iloc[-1]):
            vals.append(df[c].iloc[-1])
    if len(vals) < 4:
        return False
    return all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))


def rsi_in_range(df: pd.DataFrame, low: float = 30, high: float = 70) -> bool:
    """RSI在指定范围内"""
    if "RSI" not in df.columns or pd.isna(df["RSI"].iloc[-1]):
        return False
    rsi_val = df["RSI"].iloc[-1]
    return low <= rsi_val <= high


def rsi_below(df: pd.DataFrame, threshold: float = 30) -> bool:
    """RSI低于阈值（超卖）"""
    if "RSI" not in df.columns or pd.isna(df["RSI"].iloc[-1]):
        return False
    return df["RSI"].iloc[-1] < threshold


def rsi_above(df: pd.DataFrame, threshold: float = 70) -> bool:
    """RSI高于阈值（超买）"""
    if "RSI" not in df.columns or pd.isna(df["RSI"].iloc[-1]):
        return False
    return df["RSI"].iloc[-1] > threshold


def macd_golden_cross(df: pd.DataFrame) -> bool:
    """MACD金叉: DIF上穿DEA"""
    if "DIF" not in df.columns or len(df) < 2:
        return False
    return (df["DIF"].iloc[-1] > df["DEA"].iloc[-1]
            and df["DIF"].iloc[-2] <= df["DEA"].iloc[-2])


def macd_above_zero(df: pd.DataFrame) -> bool:
    """DIF在零轴上方"""
    if "DIF" not in df.columns or pd.isna(df["DIF"].iloc[-1]):
        return False
    return df["DIF"].iloc[-1] > 0


def volume_increase(df: pd.DataFrame, threshold: float = 1.5) -> bool:
    """当日成交量大于N日均量 threshold 倍"""
    if "VOL_RATIO" not in df.columns or pd.isna(df["VOL_RATIO"].iloc[-1]):
        return False
    return df["VOL_RATIO"].iloc[-1] > threshold


def boll_position(df: pd.DataFrame) -> str:
    """布林带位置: "upper"/"mid"/"lower" """
    if not all(c in df.columns for c in ["BOLL_UPPER", "BOLL_MID", "BOLL_LOWER"]):
        return "unknown"
    close = df["收盘"].iloc[-1]
    upper, mid, lower = df["BOLL_UPPER"].iloc[-1], df["BOLL_MID"].iloc[-1], df["BOLL_LOWER"].iloc[-1]
    if pd.isna(upper):
        return "unknown"
    if close >= upper:
        return "upper"
    if close <= lower:
        return "lower"
    return "mid"


def kdj_golden_cross(df: pd.DataFrame) -> bool:
    """KDJ金叉: K上穿D"""
    if "K" not in df.columns or len(df) < 2:
        return False
    return (df["K"].iloc[-1] > df["D"].iloc[-1]
            and df["K"].iloc[-2] <= df["D"].iloc[-2])


def consecutive_up_days(df: pd.DataFrame, n: int = 3) -> bool:
    """连续N天上涨"""
    if len(df) < n:
        return False
    recent = df["涨跌幅"].iloc[-n:]
    return (recent > 0).all()


def consecutive_down_days(df: pd.DataFrame, n: int = 3) -> bool:
    """连续N天下跌"""
    if len(df) < n:
        return False
    recent = df["涨跌幅"].iloc[-n:]
    return (recent < 0).all()
