"""exit_manager 补完计划中优先批次 B 单元测试（G4/G5/G6/G7，2026-08-06）

验收口径（出处 = 知识库《钻潜交易内训/知识卡-18-22节.md》第 19 节正式版）：
  - G4 夹角较小基本条件：内训 19·4"夹角小 = 下来后快速上涨（夹角大 = 缓慢）"——
    pivot 右侧反弹斜率 ≥ 左侧下跌斜率才视为夹角小（V 型尖底），否则不设移动获利
  - G5 TTP 启用前提：内训 19·5"利润 >5R 且没有合适的移动获利点"——有合适移动
    获利点（check_trailing_stop 非 None）→ TTP 互斥不启用
  - G6 环境前提：内训 19·6"一定要有环境（有利可图 + 累耗失衡），然后再出现拐点
    的特征，它才是真正的拐点"——未盈利或动能未加速 → 特征再明显也不判拐点
  - G7 5R 界统一：老板拍板（2026-08-06）position_zone 3R→5R；check_trailing_stop
    两档界参数化 r_boundary=5.0（内训 19·4 正式版口径）
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 分析决策.风控 import exit_manager as em
from 分析决策.风控.position import Position


def make_df(closes, highs=None, lows=None, opens=None, volumes=None) -> pd.DataFrame:
    """手工构造 K 线（价格数组显式可控；开盘缺省=收盘）"""
    n = len(closes)
    highs = highs or [max(c * 1.01, c + 0.01) for c in closes]
    lows = lows or [min(c * 0.99, c - 0.01) for c in closes]
    opens = opens or closes
    volumes = volumes or [100000] * n
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="B"),
        "开盘": opens, "收盘": closes, "最高": highs, "最低": lows,
        "成交量": volumes,
    })


def make_pos(entry: float = 10.0, stop: float = 9.0, highest: float = 10.0,
             direction: str = "long") -> Position:
    """构造持仓（每股风险 = entry - stop = 1.0 为默认）"""
    pos = Position(symbol="600000", direction=direction, market="stock",
                   entry_price=entry, initial_stop=stop, current_stop=stop,
                   volume=100)
    pos.highest_price = highest
    pos.lowest_price = min(stop, entry)
    return pos


# ============================================================
# G4：夹角较小基本条件（内训 19·4）
# ============================================================

def _trailing_df(closes, highs, lows, opens=None):
    """组 K 线 + 构造已盈利持仓（R≈2，需≥2 优势档）"""
    df = make_df(closes, highs=highs, lows=lows, opens=opens)
    pos = make_pos(entry=10.0, stop=9.0, highest=max(highs))
    return pos, df


def test_g4_sharp_v_pivot_triggers_trailing():
    """夹角小（V 型尖底：急跌后快速反弹破前高）→ 移动获利触发

    pivot（索引 20，low=10.0）左侧下跌 11.5→10.0 用 3 根
    （left_slope=(11.5-10)/3=0.5），反弹 3 根到 12.1（right_slope=0.7 > 0.5）
    → 夹角小通过；回调深度 1.5R + pivot 长下影线 → 2 优势（<5R 档）→ 触发。
    """
    closes = [10.0] * 14 + [10.3, 10.8, 11.2, 11.5, 11.3, 10.6, 10.0, 11.2, 11.8, 12.0]
    highs = [10.1] * 14 + [10.5, 11.0, 11.3, 11.5, 11.4, 10.8, 10.7, 11.4, 11.9, 12.1]
    lows = [9.9] * 14 + [10.2, 10.6, 11.0, 11.3, 11.1, 10.2, 10.0, 11.0, 11.6, 11.8]
    opens = [10.0] * 14 + [10.3, 10.7, 11.1, 11.4, 11.3, 10.6, 10.3, 11.1, 11.7, 11.9]
    pos, df = _trailing_df(closes, highs, lows, opens)
    # pivot 根（索引 20，open=close=10.3，high=10.7/low=10.0）长下影线：
    # 实体 0、影线 0.7 → 影线>实体×2 成立（优势②）；回调深度 1.5R（优势①）→ 2 优势
    ts = em.check_trailing_stop(pos, df)
    assert ts is not None, "夹角小 + 2 优势应触发移动获利"
    assert ts > pos.entry_price, "移动获利点必须在进场位正向"


def test_g4_wide_angle_pivot_not_triggered():
    """夹角大（U 型缓底：急跌后缓慢爬升破前高）→ 基本条件不满足 → 不触发

    左侧下跌 11.5→10.0 用 4 根（slope=0.375）；右侧反弹 10.0→11.7 用 8 根
    （slope=0.21 < 0.375）→ 夹角大 = 缓慢调整，不是好拐点 → 即使回调深度达标也不触发。
    """
    closes = [10.0] * 14 + [10.3, 10.8, 11.2, 11.5, 10.9, 10.4, 10.0,
                            10.2, 10.4, 10.6, 10.8, 11.0, 11.3, 11.7]
    highs = [10.1] * 14 + [10.5, 11.0, 11.3, 11.5, 11.0, 10.6, 10.1,
                           10.3, 10.5, 10.7, 10.9, 11.1, 11.4, 11.8]
    lows = [9.9] * 14 + [10.2, 10.6, 11.0, 11.3, 10.7, 10.2, 9.95,
                         10.1, 10.3, 10.5, 10.7, 10.9, 11.2, 11.5]
    pos, df = _trailing_df(closes, highs, lows)
    # pivot 根（索引 20，low=9.95）后 8 根缓慢反弹 → right_slope < left_slope
    ts = em.check_trailing_stop(pos, df)
    assert ts is None, "夹角大（缓慢调整）→ 非基本条件 → 不设移动获利"


# ============================================================
# G5：TTP 启用前提 = >5R 且无合适移动获利点（内训 19·5）
# ============================================================

def test_g5_ttp_blocked_when_trailing_point_exists():
    """有合适移动获利点 → TTP 不启用（互斥）"""
    pos = make_pos(entry=10.0, stop=9.0, highest=16.0)  # R=6 ≥ 5
    assert em.check_36pct_trail(pos, has_trailing_stop=True) is None


def test_g5_ttp_fires_without_trailing_point():
    """无合适移动获利点 + R≥5 → TTP 触发：止损 = 16-(16-10)×36% = 13.84"""
    pos = make_pos(entry=10.0, stop=9.0, highest=16.0)  # R=6 ≥ 5
    tr = em.check_36pct_trail(pos, has_trailing_stop=False)
    assert tr == round(16.0 - (16.0 - 10.0) * 0.36, 2) == 13.84


def test_g5_ttp_below_5r_never_fires():
    """R<5 → TTP 永不触发（无论有无移动获利点）"""
    pos = make_pos(entry=10.0, stop=9.0, highest=14.0)  # R=4
    assert em.check_36pct_trail(pos, has_trailing_stop=False) is None


def test_g5_evaluate_mutual_exclusion_integration():
    """evaluate_exit 集成：R≥5 且无拐点 → TTP 接管止损；
    有移动获利点 → 移动获利优先、TTP 不叠加"""
    # 无拐点横盘上涨（R=6）：TTP 应触发
    closes = [10.0] * 10 + [10.5, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 16.2]
    df = make_df(closes)
    pos = make_pos(entry=10.0, stop=9.0, highest=16.2)
    res = em.evaluate_exit(pos, df)
    assert "追踪获利" in res["reason"], f"无移动获利点时 R≥5 应由 TTP 接管，实际: {res['reason']}"
    # 有移动获利点（V 型尖底场景，R=6 需 ≥1 优势即触发）
    closes = [10.0] * 14 + [10.3, 10.8, 11.2, 11.5, 11.3, 10.6, 10.3, 10.6, 10.9, 11.6, 11.8,
                            12.5, 13.5, 14.5, 15.5, 16.0]
    highs = [10.1] * 14 + [10.5, 11.0, 11.3, 11.5, 11.4, 10.8, 10.7, 11.0, 11.3, 11.7, 11.9,
                           12.7, 13.7, 14.7, 15.7, 16.1]
    lows = [9.9] * 14 + [10.2, 10.6, 11.0, 11.3, 11.1, 10.2, 10.0, 10.4, 10.7, 11.4, 11.6,
                         12.3, 13.3, 14.3, 15.3, 15.8]
    df2 = make_df(closes, highs=highs, lows=lows)
    pos2 = make_pos(entry=10.0, stop=9.0, highest=16.1)
    res2 = em.evaluate_exit(pos2, df2)
    assert "移动获利" in res2["reason"], f"有移动获利点时 TTP 不应接管，实际: {res2['reason']}"
    assert "追踪获利" not in res2["reason"]


# ============================================================
# G6：主动离场环境前提（内训 19·6：有利可图 + 累耗失衡）
# ============================================================

def _active_df():
    """强特征 K 线：前期横盘 → 近 5 根急涨急坠（斜率骤变 + 大幅波动 + 放量）"""
    closes = [10.0] * 14 + [10.1, 10.2, 10.4, 10.3, 9.9, 9.6, 10.2, 10.9, 11.5, 10.8]
    highs = [10.1] * 14 + [10.2, 10.3, 10.5, 10.4, 10.1, 9.8, 10.6, 11.3, 11.9, 11.0]
    lows = [9.9] * 14 + [10.0, 10.1, 10.3, 10.2, 9.8, 9.5, 9.9, 10.5, 11.0, 10.5]
    volumes = [100000] * 22 + [260000, 120000]
    df = make_df(closes, highs=highs, lows=lows, volumes=volumes)
    return df


def test_g6_loss_position_never_active_exit():
    """未有利可图（持仓亏损）→ 特征再强也不判主动离场"""
    df = _active_df()
    pos = make_pos(entry=11.0, stop=10.0, highest=11.5)  # 现价 10.8 < 进场 → 亏损
    r = em.detect_active_exit(pos, df)
    assert r["signal"] is False
    assert "有利可图" in r["env"]


def test_g6_no_acceleration_no_active_exit():
    """盈利但动能未加速（无累耗失衡）→ 不判主动离场"""
    closes = [10.0] * 24 + [10.2, 10.4, 10.6, 10.8, 11.0]  # 匀速上涨 0.2/根
    highs = [c + 0.15 for c in closes]
    lows = [c - 0.15 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)
    pos = make_pos(entry=10.0, stop=9.0, highest=11.0)    # 盈利 R≈1
    r = em.detect_active_exit(pos, df)
    assert r["signal"] is False
    assert "累耗失衡" in r["env"]


def test_g6_profit_with_acceleration_and_features_fires():
    """有利可图 + 累耗失衡 + 双特征 → 主动离场信号"""
    df = _active_df()
    pos = make_pos(entry=10.0, stop=9.0, highest=11.5)    # 盈利 R≈0.8
    r = em.detect_active_exit(pos, df)
    assert r["signal"] is True, f"env={r['env']} features={r['features']}"
    assert len(r["features"]) >= 2
    assert r["env"] == "有利可图+累耗失衡"


def test_g6_active_exit_through_evaluate():
    """evaluate_exit 集成：环境+特征满足 → should_exit；环境不满足 → hold"""
    df = _active_df()
    pos = make_pos(entry=10.0, stop=9.0, highest=11.5)
    res = em.evaluate_exit(pos, df)
    assert res["should_exit"] is True
    assert "主动出场" in res["reason"]
    # 亏损持仓同数据 → 不主动离场（可被原始止损带走）
    pos2 = make_pos(entry=11.5, stop=10.5, highest=11.5)
    res2 = em.evaluate_exit(pos2, df)
    assert res2["reason"].count("主动出场") == 0


# ============================================================
# G7：5R 界统一（老板拍板 2026-08-06）
# ============================================================

def test_g7_position_zone_5r_boundary():
    """position_zone：3R 界已弃用 → 0-5R / 5R+ / 亏损区（无 3R-5R）"""
    pos_low = make_pos(entry=10.0, stop=9.0, highest=14.0)   # R=4 → 0-5R（原 3R-5R 已并入）
    assert em.position_zone(pos_low) == "0-5R"
    pos_high = make_pos(entry=10.0, stop=9.0, highest=16.0)  # R=6 → 5R+
    assert em.position_zone(pos_high) == "5R+"
    pos_neg = make_pos(entry=11.0, stop=10.0, highest=10.5)  # R<0 → 亏损区
    assert em.position_zone(pos_neg) == "亏损区"
    pos_exact = make_pos(entry=10.0, stop=9.0, highest=15.0)  # R=5 边界 → 5R+
    assert em.position_zone(pos_exact) == "5R+"


def test_g7_r_boundary_parameterized_trailing():
    """check_trailing_stop 两档界参数化：R=4 时 r_boundary=3（1 优势即触发）
    vs r_boundary=5（需 2 优势不触发）——3R 界与 5R 界行为差异的单元级对照"""
    # V 型尖底 pivot + 恰好 1 个优势（仅回调深度 0.55R）：
    # pivot 前 5 根大实体横盘（avg_body/range=0.545 ≥ 0.5 → 调整结构不成立）；
    # pivot 根实体 0.3 / 影线 0.15 → 影线>2×实体不成立
    closes = [10.0] * 14 + [10.2] * 5 + [10.2] + [11.0, 12.0, 13.0, 14.0]
    highs = [10.1] * 14 + [10.45] * 5 + [10.35] + [11.2, 12.2, 13.2, 14.2]
    lows = [9.9] * 14 + [9.95] * 5 + [9.9] + [10.8, 11.8, 12.8, 13.8]
    opens = [10.0] * 14 + [9.9] * 5 + [9.9] + [10.9, 11.9, 12.9, 13.9]
    df = make_df(closes, highs=highs, lows=lows, opens=opens)
    pos = make_pos(entry=10.0, stop=9.0, highest=10.45)  # 现价 14.0 → R=4
    assert pos.current_r_multiple(closes[-1]) == 4.0
    ts_3r = em.check_trailing_stop(pos, df, r_boundary=3.0)
    assert ts_3r is not None, "3R 界：R=4 已过界 → 1 优势即可触发"
    ts_5r = em.check_trailing_stop(pos, df, r_boundary=5.0)
    assert ts_5r is None, "5R 界：R=4 未过界 → 需 2 优势，仅 1 个不触发"


# ============================================================
# 回归：既有行为不破坏
# ============================================================

def test_regression_initial_stop_exit():
    """层面1 回归：触及原始止损 → 以止损价全出"""
    closes = [10.0, 10.1, 10.2, 10.0, 9.8, 9.5]
    highs = [10.2, 10.3, 10.4, 10.1, 9.9, 9.6]
    lows = [9.9, 10.0, 10.1, 9.9, 9.7, 9.4]  # 最后一日 9.4 < 止损 9.5
    df = make_df(closes, highs=highs, lows=lows)
    pos = make_pos(entry=10.0, stop=9.5, highest=10.4)
    res = em.evaluate_exit(pos, df)
    assert res["should_exit"] and res["action"] == "full_exit"
    assert res["exit_price"] == 9.5
    assert "止损触发" in res["reason"]


def test_regression_breakeven_moves_stop():
    """层面2 回归：R≥1 → 平价保护止损移至进场位"""
    pos = make_pos(entry=10.0, stop=9.0, highest=11.7)
    assert em.check_breakeven(pos, 11.5) == 10.0


def test_regression_short_take_profit():
    """层面6 回归：空头 5R 止盈价 = 进场 - 5R"""
    pos = Position(symbol="EURUSD", direction="short", market="forex",
                   entry_price=1.1000, initial_stop=1.1100, current_stop=1.1100,
                   volume=10000)
    tp = em.calc_take_profit(pos)
    assert tp == round(1.1000 - (1.1100 - 1.1000) * 5, 2) == 1.0500
