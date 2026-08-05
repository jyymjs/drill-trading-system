"""突破日量能确认单元测试（2026-08-06 实验参数 dn_confirm）

覆盖：默认关（0.0）行为不变 / 放量触发计入 / 缩量视为未触发 /
量比恰等于阈值不通过（>阈值才计入）/ 对照组也记录 vol_ratio / normal 不受影响 / 均量缺失防御
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

from 回测系统.tracking import Signal, track_signal


def make_df(closes: list[float], highs=None, lows=None, volumes=None) -> pd.DataFrame:
    """手工构造 K 线（价格/成交量数组显式可控）"""
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    volumes = volumes or [100000] * n
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="B"),
        "开盘": closes, "收盘": closes, "最高": highs, "最低": lows,
        "成交量": volumes,
    })


def sig_prebreak(date_idx: int = 5, trigger: float = 10.5, stop: float = 9.5,
                 risk: float = 1.0) -> Signal:
    """信号日 = 2024-01-08（索引 5）；触发日 = 索引 7（T+2，最高 10.6 ≥ 10.5）"""
    return Signal(code="600000", date=pd.Timestamp("2024-01-08"), mode="prebreak",
                  grade="A", scores={}, close=10.0, trigger=trigger, stop=stop, risk=risk)


BASE_DATES = pd.date_range("2024-01-01", periods=20, freq="B")


def test_dn_confirm_default_off_behavior_unchanged():
    """默认 dn_confirm=0.0（关）：触发即计入，行为与现有 prebreak 一致（含缩量触发）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [9.6] * 20
    highs[7] = 10.6                       # T+2 触发
    df = make_df(closes, highs=highs, lows=lows, volumes=[10000] * 20)  # 明显缩量也不拦
    oc = track_signal(sig_prebreak(), df, hold=10)
    assert oc.triggered and oc.participate()
    assert oc.entry_price == 10.5
    assert oc.exit_date == BASE_DATES[15]


def test_dn_confirm_high_volume_passes():
    """触发日放量（量比 2.5 > 2.0）→ 量能确认通过，正常计入"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [9.6] * 20
    highs[7] = 10.6                       # T+2 触发
    vols = [100000] * 20
    vols[7] = 250000                      # 触发日量比 = 250000/100000 = 2.5
    df = make_df(closes, highs=highs, lows=lows, volumes=vols)
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=2.0)
    assert oc.triggered and oc.participate()
    assert oc.entry_price == 10.5
    assert oc.vol_ratio == 2.5


def test_dn_confirm_low_volume_rejected():
    """触发日缩量（量比 1.5 ≤ 2.0）→ 视为未触发：不进场、不参与统计"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    lows = [9.6] * 20
    highs[7] = 10.6
    vols = [100000] * 20
    vols[7] = 150000                      # 量比 1.5 ≤ 2.0
    df = make_df(closes, highs=highs, lows=lows, volumes=vols)
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=2.0)
    assert not oc.triggered and not oc.participate()
    assert oc.entry_price == 0.0 and oc.r == 0.0
    assert oc.vol_ratio == 1.5            # 量比保留供"被剔除集"分析


def test_dn_confirm_boundary_equal_not_pass():
    """量比恰等于阈值 → 不通过（要求 > 阈值才计入，严格大于）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    highs[7] = 10.6
    vols = [100000] * 20
    vols[7] = 200000                      # 量比 2.0 == 2.0
    df = make_df(closes, highs=highs, volumes=vols)
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=2.0)
    assert not oc.triggered
    assert oc.vol_ratio == 2.0


def test_dn_confirm_control_group_records_vol_ratio():
    """对照组（dn_confirm=0.0）也记录 vol_ratio——供被剔除集表现分析（实验脚本第三节）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    highs[7] = 10.6
    vols = [100000] * 20
    vols[7] = 150000                      # 量比 1.5
    df = make_df(closes, highs=highs, volumes=vols)
    oc = track_signal(sig_prebreak(), df, hold=10)     # 默认 0.0
    assert oc.triggered                    # 对照组：触发即计入
    assert oc.vol_ratio == 1.5             # 但量比已记录


def test_dn_confirm_normal_mode_ignored():
    """normal 模式不受 dn_confirm 影响（vol_ratio=None）"""
    closes = [10.0] * 20
    lows = [9.8] * 20
    df = make_df(closes, lows=lows)
    s = Signal(code="600000", date=pd.Timestamp("2024-01-08"), mode="normal",
               grade="S", scores={}, close=10.0, trigger=0.0, stop=9.0, risk=1.0)
    oc = track_signal(s, df, hold=10, dn_confirm=2.0)
    assert oc.triggered
    assert oc.vol_ratio is None


def test_dn_confirm_ref_mean_zero_guard():
    """前 20 日均量为 0（防御）→ 量比 0 → 不达标（保守不进）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    highs[7] = 10.6
    vols = [0] * 6 + [0] * 14             # 触发日前全部 0 量
    vols[7] = 100000
    df = make_df(closes, highs=highs, volumes=vols)
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=1.5)
    assert not oc.triggered
    assert oc.vol_ratio == 0.0


def test_dn_confirm_untracked_no_vol_ratio():
    """未触发（价格层面没到 trigger）→ vol_ratio=None 且不参与"""
    closes = [10.0] * 20
    highs = [10.0] * 20                   # 最高恒 10.0 < 10.5
    df = make_df(closes, highs=highs)
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=1.5)
    assert not oc.triggered
    assert oc.vol_ratio is None
    assert not oc.participate()


def test_dn_confirm_threshold_greater_than_zero_only():
    """dn_confirm 必须 ≥0：负数由 params.validate 拦（tracking 层负数视为关）"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    highs[7] = 10.6
    df = make_df(closes, highs=highs)
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=-1.0)
    assert oc.triggered                    # 负数按关处理，不拦


def test_dn_confirm_vol_ratio_ref_excludes_trigger_day():
    """量比分母 = 触发日前 20 日均量（不含触发日）：前 20 根中一根异常大也不影响分母"""
    closes = [10.0] * 20
    highs = [10.0] * 20
    highs[7] = 10.6
    vols = [100000] * 20
    vols[6] = 500000                      # 触发日前一日放巨量 → 分母变大
    vols[7] = 200000
    df = make_df(closes, highs=highs, volumes=vols)
    # 分母 = mean(vols[0:7]) = (100000*6 + 500000)/7 = 157142.9 → 量比 = 200000/157142.9 ≈ 1.27
    oc = track_signal(sig_prebreak(), df, hold=10, dn_confirm=1.2)
    assert oc.triggered                    # 1.27 > 1.2
    assert np.isclose(oc.vol_ratio, 200000 / 157142.8571428571, atol=1e-3)
