"""离场管理器 — 课程4层面 + 主动出场

课程规则参考 trading-exit-rules.md

层面1: 原始止损 — TY低点下方（多头）/ TY高点上方（空头）
层面2: 平价保护 — 1:1 RR 触发，止损移至成本位
层面3: 移动获利 — 基于拐点+优势因素
层面4: 追踪获利 — 36%回调缓冲（≥5R时）
主动: 拐点三特征 — 斜率骤涨/短时大幅/成交量放大
"""
import numpy as np
import pandas as pd
from 分析决策.风控.position import Position


def calc_initial_stop(ty_low: float, ty_high: float, direction: str = "long",
                      entry_price: float = 0) -> float:
    """层面1：计算原始止损价

    多头：TY低点下方（留余量）
    空头：TY高点上方（留余量）

    Returns:
        止损价
    """
    if direction == "long":
        return round(ty_low * 0.995, 2)
    else:
        return round(ty_high * 1.005, 2)


def check_breakeven(position: Position, current_price: float) -> float | None:
    """层面2：平价保护检测

    当 R倍数 ≥ 1.0 时，止损移至成本位。
    从此时起，这笔交易不会亏损。

    Args:
        position: 持仓对象
        current_price: 当前价格

    Returns:
        新的止损价（达到1R时返回进场价），或 None（未触发）
    """
    r = position.current_r_multiple(current_price)
    if r >= 1.0:
        return position.entry_price
    return None


def check_trailing_stop(position: Position, df: pd.DataFrame) -> float | None:
    """层面3：移动获利 — 基于拐点

    在回调后再次破前高时，将止损上移到拐点下方。

    优势因素（利润<5R时需≥2个，≥5R时需拐点+1个）：
    ① 回调有深度（≥0.5R）
    ② 拐点有明显影线（影线≥实体2倍）
    ③ 有调整结构（回调后有横盘）

    老师硬规则（2023-03-04，2026-08-04 补齐）：移动获利点必须在进场位正向——
    做多时新止损必须高于进场价，做空时低于进场价（否则不生效）。

    Args:
        position: 持仓对象
        df: 包含完整K线的DataFrame

    Returns:
        新止损价，或 None
    """
    if len(df) < 20:
        return None

    close = df["收盘"].values
    high = df["最高"].values
    low = df["最低"].values
    n = len(close)

    # 计算当前R倍数
    current_r = position.current_r_multiple(close[-1])

    # 找拐点：最近10根K线中的明显低点（做多）或高点（做空）
    lookback = min(20, n - 5)
    pivot_idx = None

    if position.direction == "long":
        # 找回调低点（拐点）
        for i in range(n - lookback + 3, n - 2):
            if (low[i] < low[i - 1] and low[i] < low[i - 2]
                    and low[i] <= low[i + 1] and low[i] <= low[i + 2]):
                # 确认回调后已突破前高
                pre_high = max(high[max(0, i - 5):i + 1])
                post_high = max(high[i:])
                if post_high > pre_high * 1.01:  # 已突破
                    pivot_idx = i
                    break
    else:
        # 做空：找反弹高点
        for i in range(n - lookback + 3, n - 2):
            if (high[i] > high[i - 1] and high[i] > high[i - 2]
                    and high[i] >= high[i + 1] and high[i] >= high[i + 2]):
                pre_low = min(low[max(0, i - 5):i + 1])
                post_low = min(low[i:])
                if post_low < pre_low * 0.99:
                    pivot_idx = i
                    break

    if pivot_idx is None:
        return None

    # 评估优势因素
    advantages = 0

    # ① 回调深度
    if position.direction == "long":
        drawdown = (position.highest_price - low[pivot_idx]) / position.risk_per_share()
    else:
        drawdown = (high[pivot_idx] - position.lowest_price) / position.risk_per_share()
    if drawdown >= 0.5:
        advantages += 1

    # ② 拐点影线
    candle_body = abs(close[pivot_idx] - open(pivot_idx) if hasattr(df, 'open') else close[pivot_idx])
    candle_shadow = (high[pivot_idx] - low[pivot_idx]) - candle_body
    if candle_shadow > candle_body * 2:
        advantages += 1

    # ③ 调整结构
    pre_slice = df.iloc[max(0, pivot_idx - 5):pivot_idx + 1]
    pre_range = pre_slice["最高"].max() - pre_slice["最低"].min()
    avg_body = (pre_slice["收盘"] - pre_slice["开盘"]).abs().mean()
    if pre_range > 0 and avg_body / pre_range < 0.5:
        advantages += 1

    # 判断是否满足条件
    if current_r < 5:
        if advantages < 2:
            return None
    else:
        if advantages < 1:
            return None

    # 计算新止损价
    if position.direction == "long":
        new_stop = low[pivot_idx] - (position.highest_price - low[pivot_idx]) * 0.1
    else:
        new_stop = high[pivot_idx] + (high[pivot_idx] - position.lowest_price) * 0.1

    # 老师硬规则：移动获利点必须在进场位正向（做多止损高于进场价，做空低于）
    if position.direction == "long":
        return round(new_stop, 2) if new_stop > position.entry_price else round(position.entry_price + 0.01, 2)
    else:
        return round(new_stop, 2) if new_stop < position.entry_price else round(position.entry_price - 0.01, 2)


def check_36pct_trail(position: Position) -> float | None:
    """层面4：36%追踪获利

    当 R倍数 ≥ 5 且无合适移动获利点时触发。
    允许从最高点回调 36%。

    公式：止损 = 最高价 - (最高价 - 进场价) × 36%（多头）

    Args:
        position: 持仓对象

    Returns:
        新止损价，或 None（R倍数<5时不触发）
    """
    current_r = position.current_r_multiple(position.highest_price)
    if current_r < 5:
        return None

    if position.direction == "long":
        trail_stop = position.highest_price - (position.highest_price - position.entry_price) * 0.36
    else:
        trail_stop = position.lowest_price + (position.entry_price - position.lowest_price) * 0.36

    return round(trail_stop, 2)


def detect_active_exit(df: pd.DataFrame, lookback: int = 5) -> dict:
    """主动出场检测 —— 3个拐点特征

    特征1：斜率骤涨/急坠（价格突然加速）
    特征2：短时大幅波动
    特征3：成交量突然放大（需结合前两个特征）

    Returns:
        {"signal": bool, "features": list[str], "strength": str}
    """
    if len(df) < lookback + 1:
        return {"signal": False, "features": [], "strength": "none"}

    close = df["收盘"].values
    high = df["最高"].values
    low = df["最低"].values
    volume = df["成交量"].values if "成交量" in df.columns else None
    n = len(close)

    features = []
    strength = "none"

    # 特征1：斜率骤涨/急坠
    recent = close[-lookback:]
    pct_changes = np.abs(np.diff(recent) / recent[:-1])
    if len(pct_changes) > 0 and max(pct_changes) > 0.04:
        features.append("斜率骤变")
        strength = "strong" if max(pct_changes) > 0.06 else "moderate"

    # 特征2：短时大幅波动
    recent_range = (high[-lookback:].max() - low[-lookback:].min()) / close[-1]
    if recent_range > 0.05:
        features.append(f"波幅{recent_range:.1%}")
        if recent_range > 0.08:
            strength = "strong"

    # 特征3：成交量突然放大
    if volume is not None:
        vol_ratio = volume[-1] / np.mean(volume[-lookback * 3:-1]) if lookback * 3 < n else 1
        if vol_ratio > 2.0:
            features.append(f"放量{vol_ratio:.1f}x")
            if strength != "none":
                strength = "strong"

    signal = len(features) >= 2 and strength == "strong"
    return {"signal": signal, "features": features, "strength": strength}


def calc_take_profit(position: Position) -> float | None:
    """止盈价计算（老师口径，2026-08-04 补齐）

    所有市场空头 + 外汇无论多空 = 波段 5R 止盈
    其他市场多头（A股/美股/加密/期货多头）= 趋势跟踪，无止盈

    Returns:
        止盈价（多头=进场+5R，空头=进场-5R），或 None（趋势跟踪无止盈）
    """
    if position.direction == "short" or position.market == "forex":
        risk = position.risk_per_share()
        if position.direction == "long":
            return round(position.entry_price + risk * 5, 2)
        else:
            return round(position.entry_price - risk * 5, 2)
    return None


def position_zone(position: Position) -> str:
    """持仓区间标注（波段三区间仓位管理，2023-04-22）

    0-3R：移动获利 3 条件满足 2 个（收紧但不敏感）
    3R-5R：满足 1 个（跟紧，向止盈靠拢）
    5R：到达止盈，全部兑现

    Returns:
        "0-3R" / "3R-5R" / "5R+" / "亏损区"
    """
    r = position.current_r_multiple(position.highest_price)
    if r >= 5:
        return "5R+"
    if r >= 3:
        return "3R-5R"
    if r >= 0:
        return "0-3R"
    return "亏损区"


def evaluate_exit(position: Position, df: pd.DataFrame) -> dict:
    """综合离场评估

    按优先级检查所有离场条件。2026-08-04 增强：
    - 止盈价（方向/市场类别区分）
    - 分批平仓建议（>5R 全出 / <5R 平一半）
    - 持仓区间标注（波段三区间）

    Returns:
        {"should_exit": bool, "reason": str, "exit_price": float, "stop_update": float|None,
         "take_profit": float|None, "action": str, "zone": str}
    """
    result = {"should_exit": False, "reason": "", "exit_price": 0, "stop_update": None,
              "take_profit": None, "action": "hold", "zone": "亏损区"}
    if len(df) == 0:
        return result

    # 止盈价（方向/市场类别）与持仓区间
    result["take_profit"] = calc_take_profit(position)
    result["zone"] = position_zone(position)

    latest = df.iloc[-1]
    high = latest["最高"]
    low = latest["最低"]
    close = latest["收盘"]

    # 更新价格极值
    position.update_price(high, low, close)

    # 检查层面1：价格是否触碰止损
    if position.direction == "long" and low <= position.current_stop or position.direction == "short" and high >= position.current_stop:
        result = {"should_exit": True, "reason": "止损触发(层面1)",
                  "exit_price": position.current_stop, "stop_update": None,
                  "take_profit": result["take_profit"], "action": "full_exit", "zone": result["zone"]}
        return result

    # 检查层面2：平价保护
    bv = check_breakeven(position, close)
    if bv is not None and bv != position.current_stop:
        result["stop_update"] = bv
        result["reason"] = f"平价保护触发(层面2), 止损移至{bv}"

    # 检查层面3：移动获利
    ts = check_trailing_stop(position, df)
    if ts is not None and ts > position.current_stop:
        result["stop_update"] = ts
        result["reason"] = f"移动获利触发(层面3), 止损移至{ts}"

    # 检查层面4：追踪获利
    tr = check_36pct_trail(position)
    if tr is not None and tr > position.current_stop:
        result["stop_update"] = tr
        result["reason"] = f"追踪获利触发(层面4), 止损移至{tr}"

    # 检查主动出场（拐点三特征）
    active = detect_active_exit(df)
    if active["signal"]:
        if result["reason"]:
            result["reason"] += "; "
        result["reason"] += f"主动出场({','.join(active['features'])})"
        result["should_exit"] = True
        result["exit_price"] = close

    # 分批平仓建议（老师口径：>5R 全出 TAP / <5R 平一半 THP）
    if result["should_exit"]:
        r_now = position.current_r_multiple(close)
        result["action"] = "full_exit" if r_now >= 5 else "half_exit"
        result["reason"] += f"; {result['action']}(R={r_now:.1f})"
    elif result["take_profit"] is not None:
        # 到达止盈价（空头/外汇 5R）
        if (position.direction == "long" and high >= result["take_profit"]) or \
           (position.direction == "short" and low <= result["take_profit"]):
            result["should_exit"] = True
            result["exit_price"] = result["take_profit"]
            result["reason"] = f"止盈触发({result['take_profit']}, 波段5R)"
            result["action"] = "full_exit"

    return result
