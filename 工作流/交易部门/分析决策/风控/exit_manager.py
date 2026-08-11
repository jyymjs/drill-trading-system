"""离场管理器 — 课程4层面 + 主动出场

课程规则参考知识库《出场体系/知识卡.md》+《钻潜交易内训/知识卡-18-22节.md》（19 节正式版）

层面1: 原始止损 — TY低点下方（多头）/ TY高点上方（空头）
层面2: 平价保护 — 1:1 RR 触发，止损移至成本位
层面3: 移动获利 — 夹角较小拐点（基本条件）+优势因素，5R 两档
层面4: 追踪获利 — 36%回调缓冲（>5R 且无合适移动获利点时）
主动: 拐点三特征 — 斜率骤涨/短时大幅/成交量放大（须环境：有利可图+累耗失衡）
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


def check_trailing_stop(position: Position, df: pd.DataFrame, r_boundary: float = 5.0,
                        trail_retreat: float = 0.10,
                        angle_or_shadow: bool = False) -> float | None:
    """层面3：移动获利 — 基于拐点

    在回调后再次破前高时，将止损上移到拐点下方。

    基本条件（内训 19·4）：**夹角较小的明显拐点**——夹角小 = 下来后快速上涨
    （夹角大 = 缓慢调整），是移动获利点选择的"大标准"；拐点用五根 K 线确认
    （比左二/右二根都低/高才是真拐点）。
    优势因素（利润 <5R 时需≥2个，≥5R 时需拐点+1个）：
    ① 回调有深度（≥0.5R）
    ② 拐点有明显影线（影线≥实体2倍）
    ③ 有调整结构（回调后有横盘）

    老师硬规则（2023-03-04，2026-08-04 补齐）：移动获利点必须在进场位正向——
    做多时新止损必须高于进场价，做空时低于进场价（否则不生效）。

    两档 R 界（2026-08-06 老板拍板统一 5R）：内训 19·4 正式版以 5R 为界
    （<5R 优势两个、>5R 一个）——原 2023 周会波段 3R 界已统一弃用，
    r_boundary 参数保留供对照实验（G7）。

    R-055（2026-08-11 老板拍板 + 核实）：
      - 删除 len<20 硬门槛（与 lookback 自适应自相矛盾——n=15 本可工作被拦截，
        345/704 笔 49% 被卡；lookback=min(20, n-5) 已覆盖短数据，pivot 窗口空则自然 None）
      - trail_retreat 参数化：新止损 = 拐点低点 − (最高−拐点低点)×trail_retreat
        （默认 0.10 = 教学 10%；A 股波动大，48 笔触发 100% 被跌破——参数实验校准）
      - angle_or_shadow：2024 周会口径"明显夹角 OR 明显影线任一即可进入三条件"
        （默认 False = 内训 19 节正式版夹角硬门槛；True = 放宽）

    Args:
        position: 持仓对象
        df: 包含完整K线的DataFrame
        r_boundary: 两档优势条件的 R 界（默认 5.0，内训 19·4 正式版）
        trail_retreat: 拐点下方回撤比例（新止损 = 拐点低点 − 回撤段×比例，默认 0.10）
        angle_or_shadow: 夹角 OR 影线两前提（2024 周会口径，默认 False）

    Returns:
        新止损价，或 None
    """
    close = df["收盘"].values
    high = df["最高"].values
    low = df["最低"].values
    n = len(close)

    # 计算当前R倍数
    current_r = position.current_r_multiple(close[-1])

    # 找拐点：最近10根K线中的明显低点（做多）或高点（做空）
    lookback = min(20, n - 5)
    pivot_idx = None
    post_idx = None

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
                    post_idx = i + int(np.argmax(high[i:]))  # 突破高点位置
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
                    post_idx = i + int(np.argmin(low[i:]))  # 突破低点位置
                    break

    if pivot_idx is None:
        return None

    # 基本条件（内训 19·4）：夹角较小 = 下来后快速上涨（右侧反弹比左侧下跌更陡 = V型尖底）
    # 夹角大 = 缓慢调整，不是好拐点 → 不设移动获利。数值口径：反弹斜率 ≥ 下跌斜率（工程定案）。
    # R-055 P3：angle_or_shadow=True 时（2024 周会口径"明显夹角 OR 明显影线任一即可"），
    # 夹角不满足但拐点影线明显（影线≥实体2倍）→ 放行进入优势判断
    if position.direction == "long":
        left_win = max(0, pivot_idx - 5)
        left_high_idx = left_win + int(np.argmax(high[left_win:pivot_idx]))
        left_slope = (high[left_high_idx] - low[pivot_idx]) / max(pivot_idx - left_high_idx, 1)
        right_slope = (post_high - low[pivot_idx]) / max(post_idx - pivot_idx, 1)
        if right_slope < left_slope and not angle_or_shadow:
            return None
        if right_slope < left_slope:
            opens_v = df["开盘"].values
            body_v = abs(close[pivot_idx] - opens_v[pivot_idx])
            shadow_v = (high[pivot_idx] - low[pivot_idx]) - body_v
            if not (shadow_v > body_v * 2):
                return None
    else:
        left_win = max(0, pivot_idx - 5)
        left_low_idx = left_win + int(np.argmin(low[left_win:pivot_idx]))
        left_slope = (low[pivot_idx] - low[left_low_idx]) / max(pivot_idx - left_low_idx, 1)
        right_slope = (low[pivot_idx] - post_low) / max(post_idx - pivot_idx, 1)
        if right_slope < left_slope and not angle_or_shadow:
            return None
        if right_slope < left_slope:
            opens_v = df["开盘"].values
            body_v = abs(close[pivot_idx] - opens_v[pivot_idx])
            shadow_v = (high[pivot_idx] - low[pivot_idx]) - body_v
            if not (shadow_v > body_v * 2):
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

    # ② 拐点影线（实体 = |收盘-开盘|；原实现误用内置 open() 恒走 else 分支
    # 导致实体恒=0 被高估影线，2026-08-06 修正为真实开盘价）
    opens = df["开盘"].values
    candle_body = abs(close[pivot_idx] - opens[pivot_idx])
    candle_shadow = (high[pivot_idx] - low[pivot_idx]) - candle_body
    if candle_shadow > candle_body * 2:
        advantages += 1

    # ③ 调整结构
    pre_slice = df.iloc[max(0, pivot_idx - 5):pivot_idx + 1]
    pre_range = pre_slice["最高"].max() - pre_slice["最低"].min()
    avg_body = (pre_slice["收盘"] - pre_slice["开盘"]).abs().mean()
    if pre_range > 0 and avg_body / pre_range < 0.5:
        advantages += 1

    # 判断是否满足条件（两档：<r_boundary 需 2 个优势，≥r_boundary 需 1 个）
    if current_r < r_boundary:
        if advantages < 2:
            return None
    else:
        if advantages < 1:
            return None

    # 计算新止损价（R-055：回撤比例参数化 trail_retreat，默认 0.10 教学值）
    if position.direction == "long":
        new_stop = low[pivot_idx] - (position.highest_price - low[pivot_idx]) * trail_retreat
    else:
        new_stop = high[pivot_idx] + (high[pivot_idx] - position.lowest_price) * trail_retreat

    # 老师硬规则：移动获利点必须在进场位正向（做多止损高于进场价，做空低于）
    if position.direction == "long":
        return round(new_stop, 2) if new_stop > position.entry_price else round(position.entry_price + 0.01, 2)
    else:
        return round(new_stop, 2) if new_stop < position.entry_price else round(position.entry_price - 0.01, 2)


def check_36pct_trail(position: Position, has_trailing_stop: bool = False) -> float | None:
    """层面4：36%追踪获利（TTP）

    内训 19·5（正式版）：触发条件 = **利润 ≥5R 且没有合适的移动获利点**（含 5R 边界，工程定案 E-044）
    （中间一直没有可设移动获利的位置、平保之外无任何保护措施时的兜底机制）。
    两者互斥：有合适移动获利点 → 由移动获利接管，TTP 不启用（"系统是不允许
    有这样的事情发生的"——有移动获利点还靠 TTP 兜底 = 平白浪费利润空间）。

    允许从最高点回调 36%（保住 64% 利润空间）。

    公式：止损 = 最高价 - (最高价 - 进场价) × 36%（多头）

    Args:
        position: 持仓对象
        has_trailing_stop: 是否已有合适移动获利点（evaluate_exit 用
            check_trailing_stop 的结果传入；True = 有 → TTP 不启用）

    Returns:
        新止损价，或 None（R<5 或已有合适移动获利点时不触发）
    """
    if has_trailing_stop:
        return None
    current_r = position.current_r_multiple(position.highest_price)
    if current_r < 5:
        return None

    if position.direction == "long":
        trail_stop = position.highest_price - (position.highest_price - position.entry_price) * 0.36
    else:
        trail_stop = position.lowest_price + (position.entry_price - position.lowest_price) * 0.36

    return round(trail_stop, 2)


def detect_active_exit(position: Position, df: pd.DataFrame, lookback: int = 5) -> dict:
    """主动出场检测 —— 环境前提 + 3个拐点特征

    内训 19·6（正式版）：拐点特征须先满足**环境前提**才能判定真拐点——
    "一定要有环境，然后再出现拐点的特征，它才是真正的拐点，不能抛开环境只看特征"。
    环境两条（知识卡 2023中/2024周会 同源）：
      ① 有利可图 = 持仓盈利（"前面已有一段运行+利润空间"，R 倍数 > 0，工程定案）
      ② 累耗失衡 = 动能加速（"积累和消耗失去平衡"/"加速运动"：
         最近 lookback 根平均绝对涨速 ≥ 前期 3×lookback 的 1.5 倍，工程定案）

    特征1：斜率骤涨/急坠（价格突然加速）
    特征2：短时大幅波动
    特征3：成交量突然放大（单独放量不行，须结合前两个特征）

    Args:
        position: 持仓对象（环境前提：有利可图判定）
        df: 包含完整K线的DataFrame
        lookback: 特征观察窗口（根）

    Returns:
        {"signal": bool, "features": list[str], "strength": str, "env": str}
    """
    if len(df) < lookback * 4 + 1:
        return {"signal": False, "features": [], "strength": "none", "env": "数据不足"}

    close = df["收盘"].values
    high = df["最高"].values
    low = df["最低"].values
    volume = df["成交量"].values if "成交量" in df.columns else None
    n = len(close)

    # 环境前提①：有利可图（持仓盈利）
    r_now = position.current_r_multiple(close[-1])
    if r_now <= 0:
        return {"signal": False, "features": [], "strength": "none", "env": "未有利可图"}

    # 环境前提②：累耗失衡（积累与消耗失衡：近期平均涨速 ≥ 前期 ×1.5）
    recent_slice = close[-lookback:]
    recent_abs = np.abs(np.diff(recent_slice)) / recent_slice[:-1]
    prior_slice = close[-(lookback * 4):-lookback]
    prior_abs = np.abs(np.diff(prior_slice)) / prior_slice[:-1]
    recent_speed = float(recent_abs.mean()) if len(recent_abs) else 0.0
    prior_speed = float(prior_abs.mean()) if len(prior_abs) else 0.0
    if prior_speed <= 0:
        # 前期零波动（横盘积累）→ 近期开始运动（释放消耗）= 失衡成立
        unbalanced = recent_speed > 0
    else:
        unbalanced = recent_speed >= prior_speed * 1.5
    if not unbalanced:
        return {"signal": False, "features": [], "strength": "none", "env": "无累耗失衡"}

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
    return {"signal": signal, "features": features, "strength": strength, "env": "有利可图+累耗失衡"}


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
    """持仓区间标注（移动获利两档 5R 界，2026-08-06 老板拍板统一）

    原 2023 周会波段三区间（0-3R 满足 2 个 / 3R-5R 满足 1 个 / 5R 止盈）与
    内训 19 节正式版（<5R 优势两个 / >5R 一个）R 界口径存疑（知识卡存疑点 1），
    老板拍板统一 5R 界——与 check_trailing_stop 两档口径一致（G7）：
    0-5R：移动获利 3 条件满足 2 个（收紧，给调整空间，不敏感）
    5R+：满足 1 个（跟紧，向止盈靠拢）

    Returns:
        "0-5R" / "5R+" / "亏损区"
    """
    r = position.current_r_multiple(position.highest_price)
    if r >= 5:
        return "5R+"
    if r >= 0:
        return "0-5R"
    return "亏损区"


def evaluate_exit(position: Position, df: pd.DataFrame,
                  enable_breakeven: bool = True, enable_trailing: bool = True,
                  enable_active: bool = True, enable_ttp: bool = True) -> dict:
    """综合离场评估

    按优先级检查所有离场条件。2026-08-04 增强：
    - 止盈价（方向/市场类别区分）
    - 分批平仓建议（>5R 全出 / <5R 平一半）
    - 持仓区间标注（波段三区间）

    R-057（2026-08-11 老板拍板）：4 开关参数化分离各规则边际贡献——
    默认全 True = 现状行为零变化（生产调用 sim_check/protect_card 不传参）；
    实验组显式传 False 关闭对应规则（A 基线 = 全 False）。

    Args:
        position: 持仓对象
        df: K线DataFrame
        enable_breakeven: 层面2 1R 平保（R≥1 止损移成本价）
        enable_trailing: 层面3 移动获利（拐点三要素）
        enable_active: 主动出场（斜率骤变/波幅/放量）
        enable_ttp: 层面4 36% 追踪获利（≥5R 且无移动获利点）

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

    # 检查层面2：平价保护（R-054 修复 2026-08-11：须 bv > current_stop 只升不降——
    # 原 `!=` 在移动获利上移后会把止损降回成本位，A1 落库后即成真实回退）
    ts = None
    if enable_breakeven:
        bv = check_breakeven(position, close)
        if bv is not None and bv > position.current_stop:
            result["stop_update"] = bv
            result["reason"] = f"平价保护触发(层面2), 止损移至{bv}"

    # 检查层面3：移动获利
    if enable_trailing:
        ts = check_trailing_stop(position, df)
        if ts is not None and ts > position.current_stop:
            result["stop_update"] = ts
            result["reason"] = f"移动获利触发(层面3), 止损移至{ts}"

    # 检查层面4：追踪获利（内训 19·5：>5R 且无合适移动获利点才启用——互斥）
    if enable_ttp:
        tr = check_36pct_trail(position, has_trailing_stop=ts is not None)
        if tr is not None and tr > position.current_stop:
            result["stop_update"] = tr
            result["reason"] = f"追踪获利触发(层面4), 止损移至{tr}"

    # 检查主动出场（内训 19·6：环境前提有利可图+累耗失衡 + 拐点三特征）
    if enable_active:
        active = detect_active_exit(position, df)
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
