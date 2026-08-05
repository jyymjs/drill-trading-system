"""市场状态分段（T-021 · 防"牛市滤镜"）

背景：老板拷问"回测时间越长越好吗？选的时间段都是牛市，回测不是没意义？"
结论：回测不是越长越好，而是越有代表性越好——按市场状态分段统计，
一眼看清策略在牛市/熊市/震荡各段的表现，防止整段牛市带来的"牛市滤镜"。

市场状态规则（简单可解释，用上证指数日线判定，代码注释即规则）：
  用上证指数（000001.SH）收盘价的 20/60/120 日均线（rolling mean）：
    牛 = 收盘 > 120日均线 且 20日均线 > 60日均线（多头排列 + 站上半年线）
    熊 = 收盘 < 120日均线（跌破半年线）
    震荡 = 其余（半年线附近纠缠 / 均线走平 / 数据不足）
  选择 120 日均线作牛熊分界：A股习惯用 120 日（半年线）区分中期趋势，
  叠加 20>60 的多头排列确认——比单用一条均线更稳健，且保持可解释。

无前视纪律：信号日 T 的市场状态只用 ≤ T 日的指数数据判定（T 日收盘后
可知，符合 T+1 决策时点）；信号归属按"信号日"划分。

数据源：分析决策/市场环境/index_data.py 的指数缓存（pytdx 拉取兜底）；
缓存路径可注入（cache_dir），离线验证用本地缓存，避免联网。

分段统计口径与 stats 一致：1R 等权累计（跨 hold 合并参与序列），
排序键 (信号日, code, hold) 保证并发下可复现；胜 = R>0。
本模块只做纯函数分析，不写文件；渲染在 report.py。
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from 回测系统.stats import _max_drawdown_from_r
from 回测系统.tracking import TrackedRecord

# 市场状态（报告顺序固定）
REGIMES = ("牛", "熊", "震荡")

# 均线窗口（交易日）
MA_SHORT, MA_MID, MA_LONG = 20, 60, 120


def load_index_df(name: str = "上证指数", cache_dir: str | None = None) -> pd.DataFrame | None:
    """加载市场状态判定用指数日线（复用 index_data 缓存层）

    Args:
        name: 指数名（默认上证指数，与 B1 环境闸门主闸门一致）
        cache_dir: 指数缓存目录（默认 index_data 默认路径；测试/离线可注入）

    Returns:
        中文列 DataFrame（日期/收盘 等，升序）；无缓存且无法联网 → None
    """
    try:
        from 分析决策.市场环境.index_data import load_index_daily
        df = load_index_daily(name, cache_dir=cache_dir)
    except Exception:  # noqa: BLE001 - 数据层异常不阻断报告生成
        return None
    if df is None or df.empty:
        return None
    return df


def _series_ma(df: pd.DataFrame, window: int) -> pd.Series:
    """收盘价 rolling 均线（含当日；窗口不足 → NaN，下游归震荡）"""
    return df["收盘"].rolling(window).mean()


def regime_series(df: pd.DataFrame) -> pd.Series:
    """逐日市场状态序列（索引=日期，值=牛/熊/震荡；无前视，只用 ≤T 数据）

    规则（可解释优先）：
      牛 = 收盘 > MA120 且 MA20 > MA60（多头排列 + 站上半年线）
      熊 = 收盘 < MA120（跌破半年线）
      其余（含均线 NaN、半年线附近纠缠、20≤60 的弱多头）→ 震荡
    """
    close = df["收盘"]
    ma20 = _series_ma(df, MA_SHORT)
    ma60 = _series_ma(df, MA_MID)
    ma120 = _series_ma(df, MA_LONG)
    bull = (close > ma120) & (ma20 > ma60)
    bear = close < ma120
    out = pd.Series("震荡", index=df["日期"], dtype=object)
    out[bull.values] = "牛"
    out[bear.values] = "熊"
    return out


def _regime_on(series: pd.Series, date: pd.Timestamp) -> str | None:
    """信号日状态查询（日期不在指数日历 → None，归'未知'段）"""
    try:
        return series.loc[date]
    except KeyError:
        return None


def regime_stats(records: list[TrackedRecord], index_df: pd.DataFrame,
                 holds: list[int], mode: str | None = None) -> dict:
    """按市场状态分段统计（口径与 stats.mode_stats 一致）

    Args:
        records: 引擎产出记录
        index_df: 指数日线（load_index_df 产出）
        holds: 观察窗（参与序列跨 hold 合并，与 stats 口径一致）
        mode: 过滤模式（None=全部；normal/prebreak 可单独看）

    Returns:
        OrderedDict: {"牛": {...}, "熊": {...}, "震荡": {...}, "未知": {...}}
        每段含：
          n_signals    信号组合数（信号×hold，全样本）
          n_participate 参与统计笔数（normal 全参与；prebreak 仅触发者）
          n_win        胜笔数（R>0）
          win_rate     胜率
          avg_r        平均R
          total_r      累计R
          profit_factor 盈亏比（ΣR+ / |ΣR-|；无亏损笔 → None=无穷大）
          max_drawdown 累计R曲线最大回撤
          start/end    段内首末信号日（None=无信号）
        "未知" = 信号日不在指数日历（指数数据缺失，不计入牛熊震荡占比）
    """
    if index_df is None or index_df.empty:
        return {}
    series = regime_series(index_df)

    # 每段收集参与 R 序列（排序键 (信号日, code, hold) 全序，回撤可复现）
    r_by_regime: dict[str, list[tuple[pd.Timestamp, str, int, float]]] = {r: [] for r in REGIMES}
    n_sig_by_regime: dict[str, int] = {r: 0 for r in REGIMES}
    for rec in records:
        sig = rec.signal
        if mode is not None and sig.mode != mode:
            continue
        regime = _regime_on(series, sig.date)
        if regime is None:
            regime = "未知"
        if regime not in r_by_regime:
            r_by_regime[regime] = []
            n_sig_by_regime[regime] = 0
        for hold, oc in rec.outcomes.items():
            n_sig_by_regime[regime] += 1
            if oc.participate():
                r_by_regime[regime].append((sig.date, sig.code, hold, oc.r))

    out: OrderedDict = OrderedDict()
    for regime in list(REGIMES) + ["未知"]:
        pairs = r_by_regime.get(regime, [])
        n_sig = n_sig_by_regime.get(regime, 0)
        r_list = [r for _, _, _, r in sorted(pairs, key=lambda p: (p[0], p[1], p[2]))]
        n_part = len(r_list)
        n_win = sum(1 for r in r_list if r > 0)
        total_r = round(sum(r_list), 4)
        gains = sum(r for r in r_list if r > 0)
        losses = sum(abs(r) for r in r_list if r < 0)
        block = {
            "n_signals": n_sig,
            "n_participate": n_part,
            "n_win": n_win,
            "win_rate": round(n_win / n_part, 4) if n_part else 0.0,
            "avg_r": round(total_r / n_part, 4) if n_part else 0.0,
            "total_r": total_r,
            "profit_factor": round(gains / losses, 4) if losses > 0 else None,
            "max_drawdown": _max_drawdown_from_r(r_list),
        }
        if pairs:
            dates = sorted(p[0] for p in pairs)
            block["start"] = dates[0]
            block["end"] = dates[-1]
        else:
            block["start"] = block["end"] = None
        out[regime] = block
    return out
