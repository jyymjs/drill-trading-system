"""因子评估模块 — 基于 Alphalens

验证钻潜6条件策略各维度是否具有真实的预测力。
使用方式：
    from 分析决策.分析.factor_eval import evaluate_factor
    evaluate_factor("600419", factor_col="MA20", periods=[1, 5, 10])

注意：需要 pip install alphalens-reloaded（可选，安装失败不影响其他模块）
"""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import List

HAS_ALPHALENS = False
try:
    import alphalens as al
    HAS_ALPHALENS = True
except ImportError:
    pass


def evaluate_factor(
    codes: List[str],
    factor_name: str,
    factor_fn,
    periods: List[int] = [1, 5, 10, 20],
) -> dict | None:
    """对因子进行 IC 分析 + 分层回测

    Args:
        codes: 股票代码列表
        factor_name: 因子名称（用于标签）
        factor_fn: 因子计算函数 fn(df) -> float
        periods: 前向收益周期

    Returns:
        {"ic_mean": float, "ic_ir": float, ...} 或 None（Alphalens未安装时）
    """
    if not HAS_ALPHALENS:
        print("[factor_eval] Alphalens 未安装，跳过因子评估")
        print("  安装: pip install alphalens-reloaded")
        return None

    from 数据基础.数据.fetcher import get_daily_kline

    factor_values = {}
    prices = {}

    for code in codes:
        df = get_daily_kline(code, use_cache=True)
        if df.empty or len(df) < 60:
            continue
        try:
            val = factor_fn(df)
            factor_values[code] = val
            prices[code] = df.set_index("日期")["收盘"]
        except Exception:
            continue

    if len(factor_values) < 10:
        print(f"[factor_eval] 有效样本不足 ({len(factor_values)}只)")
        return None

    factor_series = pd.Series(factor_values, name=factor_name)
    price_df = pd.DataFrame(prices).T

    try:
        factor_data = al.utils.get_clean_factor_and_forward_returns(
            factor_series,
            price_df,
            periods=periods,
            quantiles=5,
        )

        ic = al.performance.factor_information_coefficient(factor_data)
        mean_ic = ic.mean()

        print(f"\n=== {factor_name} 因子评估 ===\n")
        print(f"有效样本: {len(factor_values)} 只")
        print(f"\nIC 均值:")
        for p in periods:
            col = f"{p}D"
            if col in mean_ic.index:
                print(f"  {p}日: {mean_ic[col]:.4f}")
        print()

        return {
            "factor_name": factor_name,
            "sample_count": len(factor_values),
            "ic_mean": mean_ic.to_dict(),
        }
    except Exception as e:
        print(f"[factor_eval] Alphalens 计算失败: {e}")
        return None


def quick_factor_check(code: str, factor_col: str = "VOL_RATIO") -> dict:
    """单股因子快检（不依赖 Alphalens）

    检查该因子在最近的表现是否异常。
    """
    from 数据基础.数据.fetcher import get_daily_kline
    from 分析决策.分析.indicators import all_indicators

    df = get_daily_kline(code, use_cache=True)
    if df.empty:
        return {"error": "无数据"}

    # 已知的按需因子列（避免触发全量计算）
    _KNOWN_FACTORS = {"VOL_RATIO", "BODY_RATIO", "ROC10", "STD20", "VSTD10",
                       "MAXPOS20", "KLEN", "WVMA20", "CORR10", "RSQR20",
                       "BIAS5", "MAD20", "ATR", "VOLATILITY"}
    df = all_indicators(df, needed_cols=[factor_col] if factor_col in _KNOWN_FACTORS else None)

    if factor_col not in df.columns:
        return {"error": f"因子 {factor_col} 未计算"}

    vals = df[factor_col].dropna().tail(60)
    if len(vals) < 20:
        return {"error": "数据不足"}

    latest = vals.iloc[-1]
    mean = vals.mean()
    std = vals.std()
    zscore = (latest - mean) / std if std > 0 else 0
    percentile = (vals < latest).mean()

    return {
        "code": code,
        "factor": factor_col,
        "latest": round(latest, 4),
        "mean_60d": round(mean, 4),
        "zscore": round(zscore, 2),
        "percentile": f"{percentile:.0%}",
        "signal": "强烈" if abs(zscore) > 2 else ("偏强" if abs(zscore) > 1 else "正常"),
    }
