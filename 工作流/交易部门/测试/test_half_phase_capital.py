"""sim_capital half_phase 资金占用测试（2026-08-06 老板确认四连包②）

覆盖：默认行为零变化（half_phase=False 与旧逻辑逐值一致）/
0.5R 起步半仓占用（首日资金占用减半）/ 确认补仓（翻倍 + 补款 + pnl 重算）/
不确认半仓止步（pnl 减半）/ 待补预留（半仓释放资金不可用于其他开仓）/
半仓买不起一手回退 1R 直开。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 回测系统.sim_capital import simulate_capital


def make_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("mode", "code", "grade", "triggered_20d", "entry_20d", "exit_20d",
                "exit_date_20d", "r_20d"):
        if col not in df.columns:
            df[col] = ""
    df["mode"] = df["mode"].fillna("prebreak")
    tail = max(pd.to_datetime(r["exit_date_20d"]) for r in rows if r.get("exit_date_20d"))
    tail = tail + pd.Timedelta(days=3)
    sentinel = {
        "mode": "prebreak", "code": "SENT", "date": tail.strftime("%Y-%m-%d"),
        "grade": "S", "close": 100.0, "risk": 999.0,
        "triggered_20d": 1, "entry_20d": 100.0, "exit_20d": 100.0,
        "exit_date_20d": tail.strftime("%Y-%m-%d"), "r_20d": 0.0,
    }
    return pd.concat([df, pd.DataFrame([sentinel])], ignore_index=True)


def sig(code: str, date: str, close: float, risk: float, exit_date: str,
        exit: float, r: float, grade: str = "S", entry: float | None = None) -> dict:
    return {
        "mode": "prebreak", "code": code, "date": date, "grade": grade,
        "close": close, "risk": risk,
        "triggered_20d": 1, "entry_20d": entry if entry is not None else close,
        "exit_20d": exit, "exit_date_20d": exit_date, "r_20d": r,
    }


def confirm_ok(code, signal_date, entry, stop, confirm_date=None):
    """fake 确认函数：全部确认补仓"""
    return {"confirmed": True, "stopped": False, "close": entry,
            "confirm_date": confirm_date or "2024-01-06"}


def confirm_reject(code, signal_date, entry, stop, confirm_date=None):
    """fake 确认函数：全部不确认（收线 reject → 半仓止步）"""
    return {"confirmed": False, "stopped": False, "close": entry * 0.98,
            "confirm_date": confirm_date or "2024-01-06"}


def test_default_off_unchanged_behavior():
    """half_phase=False = 现有行为（整仓占用）零变化"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),
        sig("B", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),
        sig("C", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),
    ])
    r1 = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    r2 = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2,
                          half_phase=True, confirm_fn=confirm_ok)
    # 全部 1R 直开（每股风险 1.0 → 半额预算 75 元 → 75 股 <100 → 直开）
    assert r1["n_exec"] == r2["n_exec"] == 2
    assert r1["end_balance"] == r2["end_balance"]
    assert not any(t.get("half") for t in r2["trades"])


def test_half_entry_half_cash_occupation():
    """0.5R 起步：首日半仓占用（资金占用 = 半仓成本），确认日补款翻倍"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 0.5, "2024-02-05", 12.0, 4.0),
    ])
    # 每股风险 0.5 → 全仓预算 150/0.5=300 股；半额 75/0.5=150 → 100 股起步
    r = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2,
                         half_phase=True, confirm_fn=confirm_ok)
    t = r["trades"][0]
    assert t["half"] is True and t["half_ok"] is True
    assert t["shares"] == 200          # 确认补仓后翻倍至 1R
    assert t["risk_actual"] == pytest.approx(0.5 * 200, abs=0.01)
    # pnl = 全仓金额盈亏 - 双笔半仓买入费(含印花税, calc_trade_fee 既有口径) - 补仓费 - 出场费
    # calc_trade_fee(1000) = 1.5（佣金1.0 + 印花税0.5）；calc_trade_fee(2400) = 2.2
    assert t["pnl"] == pytest.approx(400.0 - 2 * 1.5 - 1.5 - 2.2, abs=0.1)


def test_reject_half_position_pnl_halved():
    """不确认 → 半仓止步：股数不变（100），pnl 为半仓口径"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 0.5, "2024-01-06", 10.0, 0.0),
    ])
    r = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2,
                         half_phase=True, confirm_fn=confirm_reject)
    t = r["trades"][0]
    assert t["half"] is True and t["half_ok"] is False
    assert t["shares"] == 100          # 不翻倍
    assert t["pnl"] < 1.0              # 平仓价 ≈ 进场价，半仓费用后小亏


def test_pending_reserve_blocks_other_open():
    """待补预留：半仓释放的资金中"待补部分"不可用于其他开仓"""
    df = make_df([
        # A 半仓起步（确认未决，待补预留 500 元）——确认日在 01-06（下一信号日前）
        sig("A", "2024-01-05", 10.0, 0.5, "2024-02-05", 12.0, 4.0),
        # B 同日信号：若半仓释放的现金全可用会成交；待补预留 → 资金不足被拒
        sig("B", "2024-01-05", 100.0, 1.0, "2024-02-05", 110.0, 1.0),
    ])
    r = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=3,
                         half_phase=True, confirm_fn=confirm_ok)
    # A 首日 100 股 × 10 = 1000 元（半仓）；预留待补 1000；B 需要 100 股 × 100 = 10000
    # → 现金不足 → B 拒（A 半仓只释放了一半现金，且待补部分冻结）
    assert [t["code"] for t in r["trades"]] == ["A"]
    assert "资金不足" in r["reasons"]


def test_half_unaffordable_falls_back_to_full():
    """半额预算买不起 1 手 → 回退 1R 直开（执行集与默认一致）"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 0.8, "2024-02-05", 12.0, 2.5),
    ])
    # 每股风险 0.8 → 半额 75/0.8=93 <100 → 直开全仓（150/0.8=187 → 100 股）
    r = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2,
                         half_phase=True, confirm_fn=confirm_ok)
    t = r["trades"][0]
    assert not t.get("half")
    assert t["shares"] == 100


def _confirm_by_code(code, signal_date, entry, stop, confirm_date=None):
    """按代码分发：A 确认 / B reject（其余默认确认）"""
    if str(code) == "B":
        return confirm_reject(code, signal_date, entry, stop, confirm_date)
    return confirm_ok(code, signal_date, entry, stop, confirm_date)


def test_half_stats_counts():
    """half_stats 汇总：半仓笔数/确认笔数/止步笔数"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 0.5, "2024-02-05", 12.0, 4.0),   # 确认
        sig("B", "2024-01-05", 10.0, 0.5, "2024-01-06", 10.0, 0.0),  # reject
        sig("C", "2024-01-05", 10.0, 0.8, "2024-02-05", 12.0, 2.5),  # 直开
    ])
    r = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=3,
                         half_phase=True, confirm_fn=_confirm_by_code)
    assert r["half_stats"]["n_half"] == 2
    assert r["half_stats"]["n_confirm"] == 1
    assert r["half_stats"]["n_reject"] == 1


def test_half_shortfall_skips_confirm():
    """R-051 B 变体（confirm_shortfall_skip=True）：确认日余额不足 → 不补仓维持半仓

    场景：capital 5000、3 笔同日 half 起步（每股风险 0.5、entry 10 → 半额预算
    62.5//0.5=125 → 100 股，add_cost 各 1000）。B 模式无预留：3 笔起步后
    balance=2000；确认日 A 补 1000 → 1000；B 补 1000 → 0；C 补 1000 → 余额不足
    → 不补（维持 100 股半仓）+ reasons["补仓资金不足"] + half_shortfall 标记。
    对照：A 模式（默认预留）3 笔全补（n_confirm_shortfall==0）。
    """
    df = make_df([
        sig("A", "2024-01-05", 10.0, 0.5, "2024-03-05", 12.0, 2.0),
        sig("B", "2024-01-05", 10.0, 0.5, "2024-03-05", 12.0, 2.0),
        sig("C", "2024-01-05", 10.0, 0.5, "2024-03-05", 12.0, 2.0),
        # 2024-01-06 超限哨兵：不成交但让确认日进入主循环（confirm_date=2024-01-06）
        sig("D", "2024-01-06", 100.0, 999.0, "2024-03-05", 100.0, 0.0),
    ])
    # A 模式（默认，pending 预留）：C 开仓时 avail=999 < 买入费后 1000 → 被拒
    # （买入佣金 1 元/笔耗尽预算——审核意见 4 修正：真实行为 = 2 笔成交，3 笔全补从未发生）
    rA = simulate_capital(df, capital=5000, risk_ratio=0.025, max_positions=3,
                          half_phase=True, confirm_fn=confirm_ok)
    assert rA["n_exec"] == 2
    assert rA["n_confirm_shortfall"] == 0
    assert all(int(t["shares"]) == 200 for t in rA["trades"])

    # B 模式（无预留）：确认日余额不足 → 不补维持半仓
    # 账本：3 笔起步后 balance=2000；A 补 1000+费1 → 999；B 补 → 缺；C 补 → 缺
    # （fee_add 未预留——B 模式比理论更敏感，2 笔缺钱是预期行为）
    rB = simulate_capital(df, capital=5000, risk_ratio=0.025, max_positions=3,
                          half_phase=True, confirm_fn=confirm_ok,
                          confirm_shortfall_skip=True)
    assert rB["n_exec"] == 3
    assert rB["n_confirm_shortfall"] == 2
    assert rB["reasons"].get("补仓资金不足") == 2
    a_trade = [t for t in rB["trades"] if t["code"] == "A"][0]
    bc = [t for t in rB["trades"] if t["code"] != "A"]
    assert int(a_trade["shares"]) == 200 and not a_trade.get("half_shortfall")
    assert all(int(t["shares"]) == 100 and t.get("half_shortfall") is True for t in bc)
    # 审核意见 2/10：补仓失败笔出场费用单计（只付 1 次买入费，不双计）
    # C 笔：半仓 100 股 10→12 元：pnl = 200 - 买入费1.5(佣金1+印花税0.5, 既有口径)
    #                                    - 卖出费1.6(佣金1+印花税0.6) = 196.9
    c_trade = [t for t in rB["trades"] if t["code"] == "C"][0]
    assert abs(c_trade["pnl"] - (200 - 1.5 - 1.6)) < 0.01
    assert c_trade["fee_in"] == 1.5  # 单笔买入费（非双计——若双计会多扣 1.5）
