"""1B 确认规则放宽对照实验测试（2026-08-06 老板拍板）

覆盖：
1. indicators.confirm_conditions 三条件独立评估（单一来源）
2. indicators.half_position_confirm_relaxed 放宽判定（any2 三取二 / no_c2 去动能延续）
3. indicators.phase_confirm_from_kline confirm_mode 参数化（delay2 延迟二次确认）
4. confirm_replay.replay_confirm confirm_mode 参数化（回放口径）
5. confirm_replay.rebuild_exit_for_mode（strict 原样 / 放宽新确认重算出场）
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 回测系统.confirm_replay import rebuild_exit_for_mode, replay_confirm

from 分析决策.分析.indicators import (
    confirm_conditions,
    half_position_confirm,
    half_position_confirm_relaxed,
    phase_confirm_from_kline,
)


def mk_kline(rows: list[tuple], start="2024-01-01") -> pd.DataFrame:
    """rows = [(开盘, 最高, 最低, 收盘, 成交量), ...]"""
    return pd.DataFrame({
        "日期": pd.bdate_range(start, periods=len(rows)),
        "开盘": [r[0] for r in rows],
        "最高": [r[1] for r in rows],
        "最低": [r[2] for r in rows],
        "收盘": [r[3] for r in rows],
        "成交量": [r[4] if len(r) > 4 else 1_000_000 for r in rows],
    })


def mk_signals(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("code", "grade", "entry_20d", "stop_loss", "stop",
                "risk", "r_20d", "exit_20d", "exit_date_20d", "date"):
        if col not in df.columns:
            df[col] = ""
    if "mode" not in df.columns:
        df["mode"] = "prebreak"
    if "triggered_20d" not in df.columns:
        df["triggered_20d"] = 1
    return df


# ──────────────────────────────────────────────
# 1. confirm_conditions 三条件独立评估
# ──────────────────────────────────────────────

class TestConfirmConditions:
    def test_all_true(self):
        """收下去 + 动能延续 + 非放量阴线 → 三条件全真"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),   # 开仓日收 10.1
            (10.2, 10.5, 10.1, 10.4, 1e6),   # 确认日收 10.4
        ])
        c = confirm_conditions(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert c["c1"] and c["c2"] and c["c3"] and not c["stopped"] and not c["wait"]

    def test_c2_false_when_weaker(self):
        """收盘 ≥ 进场价但 < 开仓日收盘 → 仅 C2 不满足（C2 误杀场景）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (10.05, 10.25, 10.0, 10.2, 1e6),   # 开仓日收 10.2
            (10.1, 10.4, 10.05, 10.15, 1e6),   # 确认日收 10.15
        ])
        c = confirm_conditions(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert c["c1"] and not c["c2"] and c["c3"]

    def test_c3_false_volume_down(self):
        """放量阴线（量比>1.5 且收阴）→ 仅 C3 不满足（需 ≥6 根才有量比判定）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),
            (10.0, 10.2, 9.9, 10.05, 1e6),
            (10.1, 10.3, 10.0, 10.2, 1e6),
            (10.2, 10.5, 10.1, 10.4, 1e6),     # 开仓日收 10.4（前 5 日均量 1e6）
            (10.6, 10.8, 10.2, 10.5, 2e6),     # 确认日：收 10.5 ≥ 进 10.3 且 ≥ 开仓日收 10.4
                                                #         但量 2e6>1.5e6 且收阴（开 10.6>收 10.5）
        ])
        c = confirm_conditions(df.iloc[:6], entry_price=10.3, stop_loss=10.0)
        assert c["c1"] and c["c2"] and not c["c3"] and c["reject_vol"]

    def test_stopped_priority(self):
        """确认日最低 ≤ 止损 → stopped（三条件不参与）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),
            (10.3, 10.6, 9.85, 10.5, 1e6),    # 最低 9.85 ≤ 止损 9.9
        ])
        c = confirm_conditions(df.iloc[:3], entry_price=10.1, stop_loss=9.9)
        assert c["stopped"] and not c["c1"] and not c["c2"]


# ──────────────────────────────────────────────
# 2. half_position_confirm_relaxed 放宽判定
# ──────────────────────────────────────────────

class TestHalfPositionConfirmRelaxed:
    def test_any2_rescues_c2_only_fail(self):
        """现状 reject（仅 C2 不满足）→ any2 确认（C1+C3）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (10.05, 10.25, 10.0, 10.2, 1e6),
            (10.1, 10.4, 10.05, 10.15, 1e6),   # 收 10.15 ≥ 进 10.1，< 开仓日收 10.2
        ])
        sl = df.iloc[:3]
        assert half_position_confirm(sl, entry_price=10.1, stop_loss=9.9)["reject"]
        v = half_position_confirm_relaxed(sl, entry_price=10.1, stop_loss=9.9,
                                          mode="any2")
        assert v["confirmed"] and "any2" in v["reason"]

    def test_any2_rejects_single_condition(self):
        """仅 C1 满足（收盘≥进场价，但动能弱且放量阴线）→ any2 仍 reject"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (10.05, 10.25, 10.0, 10.2, 1e6),
            (10.0, 10.2, 9.9, 10.05, 1e6),
            (10.1, 10.3, 10.0, 10.2, 1e6),
            (10.2, 10.5, 10.1, 10.4, 1e6),     # 开仓日收 10.4（前 5 日均量 1e6）
            (10.3, 10.6, 10.1, 10.2, 2e6),     # 确认日：收 10.2 ≥ 进 10.15（C1✓）
                                                #          < 开仓日收 10.4（C2✗）
                                                #          放量阴线 2e6>1.5e6（C3✗）
        ])
        v = half_position_confirm_relaxed(df.iloc[:6], entry_price=10.15,
                                          stop_loss=10.0, mode="any2")
        assert v["reject"]

    def test_no_c2_rescues_weaker_close(self):
        """现状 reject（仅 C2 不满足）→ no_c2 确认（C1+C3）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (10.05, 10.25, 10.0, 10.2, 1e6),
            (10.1, 10.4, 10.05, 10.15, 1e6),
        ])
        v = half_position_confirm_relaxed(df.iloc[:3], entry_price=10.1,
                                          stop_loss=9.9, mode="no_c2")
        assert v["confirmed"] and "no_c2" in v["reason"]

    def test_no_c2_rejects_below_entry(self):
        """收盘 < 进场价（C1 不满足）→ no_c2 仍 reject"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (10.05, 10.25, 10.0, 10.2, 1e6),
            (10.0, 10.2, 9.92, 10.02, 1e6),   # 收 10.02 < 进 10.1
        ])
        v = half_position_confirm_relaxed(df.iloc[:3], entry_price=10.1,
                                          stop_loss=9.9, mode="no_c2")
        assert v["reject"] and "跌破进场价" in v["reason"]

    def test_relaxed_stop_priority(self):
        """放宽版止损层面1 优先（不因放宽改变）"""
        df = mk_kline([
            (9.9, 10.1, 9.8, 10.0, 1e6),
            (9.95, 10.15, 9.9, 10.1, 1e6),
            (10.3, 10.6, 9.85, 10.5, 1e6),    # 最低 9.85 ≤ 止损 9.9
        ])
        v = half_position_confirm_relaxed(df.iloc[:3], entry_price=10.1,
                                          stop_loss=9.9, mode="any2")
        assert v["stopped"] and not v["confirmed"]

    def test_relaxed_wait(self):
        """不足两根 → wait"""
        df = mk_kline([(9.95, 10.15, 9.9, 10.1, 1e6)])
        v = half_position_confirm_relaxed(df.iloc[:1], entry_price=10.1,
                                          stop_loss=9.9, mode="any2")
        assert v["wait"]

    def test_unknown_mode_raises(self):
        df = mk_kline([(9.95, 10.15, 9.9, 10.1, 1e6),
                       (10.2, 10.5, 10.1, 10.4, 1e6)])
        with pytest.raises(ValueError):
            half_position_confirm_relaxed(df, entry_price=10.1, stop_loss=9.9,
                                          mode="bogus")


# ──────────────────────────────────────────────
# 3. phase_confirm_from_kline confirm_mode（delay2）
# ──────────────────────────────────────────────

def _delay_kline():
    """信号日 01-02（触发价 10.5）→ 触发日 01-03 → 确认日 01-04 弱（reject）→ 01-05 收回"""
    return mk_kline([
        (10.0, 10.3, 9.9, 10.1, 1e6),     # 01-01
        (10.1, 10.4, 10.0, 10.2, 1e6),    # 01-02 信号日
        (10.2, 10.9, 10.1, 10.3, 1e6),    # 01-03 触发日（最高 10.9 ≥ 10.5）
        (10.25, 10.35, 10.05, 10.2, 1e6),  # 01-04 首确认日：收 10.2 < 触发日收 10.3 → reject
        (10.4, 10.7, 10.3, 10.55, 1e6),   # 01-05 二次确认日：收 10.55 ≥ 进 10.5 且 ≥ 01-04 收
    ], start="2024-01-01")


class TestPhaseConfirmDelay2:
    def test_delay2_confirms_on_second_bar(self):
        """首根 reject → 第二根收回 → 确认（confirm_date = 第二根）"""
        k = _delay_kline()
        v_strict = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5)
        assert v_strict["reject"] and v_strict["confirm_date"] == "2024-01-04"
        v = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5,
                                     confirm_mode="delay2")
        assert v["confirmed"] and not v["reject"]
        assert v["confirm_date"] == "2024-01-05"

    def test_delay2_still_rejects_when_second_bar_weak(self):
        """两轮都不确认 → reject（以第二根收盘平仓）"""
        k = _delay_kline()
        # 第二根改弱：收 10.3 < 触发日收 10.3？构造：收 10.25 < 进 10.5
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),
            (10.1, 10.4, 10.0, 10.2, 1e6),
            (10.2, 10.9, 10.1, 10.3, 1e6),
            (10.25, 10.35, 10.05, 10.2, 1e6),
            (10.0, 10.3, 9.9, 10.1, 1e6),   # 第二根仍弱
        ], start="2024-01-01")
        v = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5,
                                     confirm_mode="delay2")
        assert v["reject"] and v["confirm_date"] == "2024-01-05"

    def test_delay2_second_bar_stop(self):
        """第二根触止损 → stopped（层面1 优先，不算 reject）"""
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),
            (10.1, 10.4, 10.0, 10.2, 1e6),
            (10.2, 10.9, 10.1, 10.3, 1e6),
            (10.25, 10.35, 10.05, 10.2, 1e6),
            (10.0, 10.3, 9.3, 10.1, 1e6),   # 第二根最低 9.3 ≤ 止损 9.5
        ], start="2024-01-01")
        v = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5,
                                     confirm_mode="delay2")
        assert v["stopped"] and v["confirm_date"] == "2024-01-05"

    def test_delay2_first_bar_confirm_no_wait(self):
        """首根即确认 → 不等待（与现状一致）"""
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),
            (10.1, 10.4, 10.0, 10.2, 1e6),
            (10.2, 10.9, 10.1, 10.3, 1e6),
            (10.5, 10.8, 10.4, 10.6, 1e6),   # 收 10.6 ≥ 进 10.5 且 ≥ 触发日收 10.3
        ], start="2024-01-01")
        v = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5,
                                     confirm_mode="delay2")
        assert v["confirmed"] and v["confirm_date"] == "2024-01-04"

    def test_no_c2_mode_passthrough(self):
        """no_c2：首根 C2 不满足但 C1+C3 满足 → 确认"""
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),
            (10.1, 10.4, 10.0, 10.2, 1e6),
            (10.2, 10.9, 10.1, 10.6, 1e6),   # 触发日收 10.6
            (10.4, 10.7, 10.3, 10.5, 1e6),   # 确认日收 10.5 ≥ 进 10.5，< 触发日收 10.6
        ], start="2024-01-01")
        v_strict = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5)
        assert v_strict["reject"]
        v = phase_confirm_from_kline(k, "2024-01-02", 10.5, 9.5,
                                     confirm_mode="no_c2")
        assert v["confirmed"]


# ──────────────────────────────────────────────
# 4. replay_confirm confirm_mode 参数化
# ──────────────────────────────────────────────

class TestReplayConfirmMode:
    def test_no_c2_replay_rescues_c2_only_fail(self):
        """现状 reject 的票（仅 C2 不满足）→ no_c2 回放确认"""
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),
            (10.1, 10.4, 10.0, 10.2, 1e6),     # 01-02 信号日
            (10.2, 10.9, 10.1, 10.6, 1e6),     # 01-03 触发日收 10.6
            (10.4, 10.7, 10.3, 10.5, 1e6),     # 01-04 确认日收 10.5（C2 不满足）
            (10.0, 10.4, 9.9, 10.2, 1e6),      # 后续 20 根
        ] * 1 + [(10.0, 10.4, 9.9, 10.2, 1e6)] * 20, start="2024-01-01")
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": -0.3}])
        r_strict = replay_confirm(s, {"A": k})
        assert r_strict["n_reject"] == 1
        r = replay_confirm(s, {"A": k}, confirm_mode="no_c2")
        assert r["n_confirm"] == 1 and r["confirm_mode"] == "no_c2"

    def test_delay2_replay_second_bar_confirm(self):
        """首根 reject 第二根收回 → delay2 回放确认"""
        k = _delay_kline()
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": -0.3}])
        r_strict = replay_confirm(s, {"A": k})
        assert r_strict["n_reject"] == 1
        r = replay_confirm(s, {"A": k}, confirm_mode="delay2")
        assert r["n_confirm"] == 1
        assert r["detail"]["confirm_date"].iloc[0] == "2024-01-05"


# ──────────────────────────────────────────────
# 5. rebuild_exit_for_mode（strict 原样 / 放宽重算）
# ──────────────────────────────────────────────

class TestRebuildExit:
    def test_strict_returns_unchanged(self):
        """strict 模式：出场列原样（现状锚点零改动）"""
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": 1.0,
                         "exit_20d": 12.0, "exit_date_20d": "2024-02-01"}])
        k = kline_simple()
        out, _verify = rebuild_exit_for_mode(s, {"A": k}, confirm_mode="strict")
        assert out["exit_20d"].iloc[0] == 12.0
        assert out["r_20d"].iloc[0] == 1.0

    def test_no_c2_rebuilds_new_confirm_exit(self):
        """放宽新确认票：exit/r 按引擎同规则重算（信号日窗口 + 止损/到期收盘）"""
        # 信号日 01-02 触发 10.5 → 触发日 01-03 → 确认日 01-04 弱（reject）
        # 后续 20 根横盘收 11.0 → 重算 exit ≈ 窗口末收盘
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),     # 01-01
            (10.1, 10.4, 10.0, 10.2, 1e6),    # 01-02 信号日
            (10.2, 10.9, 10.1, 10.6, 1e6),    # 01-03 触发日收 10.6
            (10.4, 10.7, 10.3, 10.5, 1e6),    # 01-04 确认日收 10.5（C2 不满足 → 现状 reject）
            (10.7, 11.2, 10.6, 11.0, 1e6),    # 后续（确认日次日起）
        ] + [(10.7, 11.2, 10.6, 11.0, 1e6)] * 19, start="2024-01-01")
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "stop": 9.5, "r_20d": -0.3,
                         "exit_20d": 10.5, "exit_date_20d": "2024-01-04"}])
        out, verify = rebuild_exit_for_mode(s, {"A": k}, confirm_mode="no_c2")
        assert verify["n_rebuild"] == 1
        # 持有到信号日+20 窗口末（无止损触发）→ 收盘 11.0；r = (11.0-10.5)/0.5 = 1.0
        assert out["exit_20d"].iloc[0] == pytest.approx(11.0, abs=0.01)
        assert out["r_20d"].iloc[0] == pytest.approx(1.0, abs=0.05)
        # 引擎原 reject 的 exit 已被替换（不再是确认日收盘 10.5）
        assert out["exit_20d"].iloc[0] != 10.5

    def test_strict_verifies_engine_consistency(self):
        """strict 模式校验统计：confirm 票重算与引擎 exit 一致"""
        # 构造一个确认票：确认后横盘 20 根 → 引擎 exit = 窗口末收盘
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1e6),     # 01-01
            (10.1, 10.4, 10.0, 10.2, 1e6),    # 01-02 信号日
            (10.2, 10.9, 10.1, 10.3, 1e6),    # 01-03 触发日
            (10.5, 10.8, 10.4, 10.6, 1e6),    # 01-04 确认日（confirm）
            (10.7, 11.2, 10.6, 11.0, 1e6),    # 后续横盘
        ] + [(10.7, 11.2, 10.6, 11.0, 1e6)] * 19, start="2024-01-01")
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "stop": 9.5, "r_20d": 1.0,
                         "exit_20d": 11.0, "exit_date_20d": "2024-02-01"}])
        _out, verify = rebuild_exit_for_mode(s, {"A": k}, confirm_mode="strict")
        assert verify["n_confirm_strict"] == 1
        assert verify["exit_identical"] == 1


def kline_simple():
    """信号日 01-02 触发 10.5 → 01-03 触发日 → 01-04 确认日（强确认）"""
    return mk_kline([
        (10.0, 10.3, 9.9, 10.1, 1e6),
        (10.1, 10.4, 10.0, 10.2, 1e6),
        (10.2, 10.9, 10.1, 10.3, 1e6),
        (10.5, 10.8, 10.4, 10.6, 1e6),
        (10.7, 11.2, 10.6, 11.0, 1e6),
    ] + [(10.7, 11.2, 10.6, 11.0, 1e6)] * 19, start="2024-01-01")
