"""模拟资金模块单元测试（R-009 模块2 · 2026-08-06 多持仓参数化升级）

覆盖：多持仓并发（上限 2）/ 持仓到期释放名额 / 单持仓旧行为（max_positions=1）/
整手 100 股 / 每股风险上限（单笔风险额恒定）/ 实际风险恒 ≤ 单笔风险额 /
费用（佣金万1.3最低1元 + 印花税万5）/ 评级过滤 / 资金曲线/最大回撤（金额+%+时长）/
100 笔节奏预估
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

from 回测系统.sim_capital import simulate_capital


def make_df(rows: list[dict]) -> pd.DataFrame:
    """构造 signals-like DataFrame（20d 列齐全）+ 尾部哨兵行

    哨兵行：把"数据末交易日"（max_date）推到最后信号日之后——真实持仓的
    exit_date 晚于自身信号日，若不加哨兵，末笔信号会因"持仓未完成(数据末尾)"
    被误拒。哨兵自身 risk=999 恒被拒买（每股风险超限），不产生成交。
    """
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
        exit: float, r: float, grade: str = "S", entry: float | None = None,
        mom20: float = 0.05) -> dict:
    """单笔信号行（20d 主口径；entry 缺省 = close；mom20 默认 5% 在 C23 内）"""
    return {
        "mode": "prebreak", "code": code, "date": date, "grade": grade,
        "close": close, "risk": risk,
        "triggered_20d": 1, "entry_20d": entry if entry is not None else close,
        "exit_20d": exit, "exit_date_20d": exit_date, "r_20d": r,
        "mom20": mom20,
    }


def test_two_positions_concurrent_then_slot_released():
    """2 持仓并发：同日 3 只信号 → 前 2 只成交、第 3 只拒（持仓满）；到期后释放名额"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),
        sig("B", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),
        sig("C", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),  # 持仓满
        sig("D", "2024-02-06", 10.0, 1.0, "2024-03-06", 11.0, 1.0),  # A/B 已到期 → 可开
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    assert res["n_exec"] == 3, res["n_exec"]
    codes = [t["code"] for t in res["trades"]]
    assert codes == ["A", "B", "D"], codes
    assert res["reasons"].get("持仓数已满(最多2只)") == 1
    # 资金占用校验：两笔并发买入后现金足够第三笔被拒（而非资金不足）
    assert "资金不足" not in res["reasons"]


def test_single_position_legacy_behavior():
    """max_positions=1 = 旧版单持仓顺序：持仓期内新信号一律拒"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),
        sig("B", "2024-01-05", 10.0, 1.0, "2024-02-05", 12.0, 2.0),  # A 未到期 → 拒
        sig("C", "2024-02-06", 10.0, 1.0, "2024-03-06", 11.0, 1.0),  # A 到期 → 开
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=1)
    assert res["n_exec"] == 2
    assert [t["code"] for t in res["trades"]] == ["A", "C"]
    assert res["reasons"].get("持仓数已满(最多1只)") == 1


def test_lot_size_100_and_risk_per_share_cap():
    """整手 100 股 + 每股风险上限（单笔风险额/100=0.84）：每股风险过大 → 拒买"""
    df = make_df([
        sig("OK", "2024-01-05", 10.0, 0.8, "2024-02-05", 11.0, 1.0),   # 84//0.8=105 → 100股
        sig("NO", "2024-01-05", 10.0, 2.0, "2024-02-05", 11.0, 1.0),   # 84//2=42 <100 → 拒
    ])
    res = simulate_capital(df, capital=5600, risk_ratio=0.015, max_positions=2)
    assert res["n_exec"] == 1
    assert res["trades"][0]["shares"] == 100
    assert res["trades"][0]["risk_actual"] == 80.0  # 0.8 × 100 = 80 ≤ 84
    assert any("超限" in k for k in res["reasons"])
    # 资金上限方向：股票单价高时受资金约束（如 55 元/股 → 5600//55=101 → 100股）
    df2 = make_df([sig("P", "2024-01-05", 55.0, 0.8, "2024-02-05", 60.0, 1.0)])
    res2 = simulate_capital(df2, capital=5600, risk_ratio=0.015, max_positions=2)
    assert res2["trades"][0]["shares"] == 100


def test_risk_actual_never_exceeds_risk_amount():
    """单笔实际风险（每股风险×股数）恒 ≤ 单笔风险额（84 元）"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 1.5, "2024-02-05", 12.0, 2.0),   # 84//1.5=56 → 拒
        sig("B", "2024-01-05", 10.0, 0.83, "2024-02-05", 12.0, 2.0),  # 84//0.83=101 → 100
        sig("C", "2024-01-05", 10.0, 0.84, "2024-02-05", 12.0, 2.0),  # 84//0.84=100 → 100
        sig("D", "2024-02-06", 10.0, 0.5, "2024-03-06", 11.0, 1.0),   # 84//0.5=168 → 100股? no
    ])
    res = simulate_capital(df, capital=5600, risk_ratio=0.015, max_positions=2)
    assert res["risk_exec"]["over_risk_amt"] == 0
    assert all(t["risk_actual"] <= 84.0 for t in res["trades"])


def test_fees_included_in_pnl():
    """费用已含：佣金万1.3（最低1元）+ 印花税万5（卖出）"""
    df = make_df([sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0)])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    t = res["trades"][0]
    # 100 股：买入 1000 元 → 佣金 1.0 + 印花税 0.5（calc_trade_fee 现有口径：印花税无条件计）；
    # 卖出 1100 元 → 佣金 1.0 + 印花税 0.55 → 总费用 3.05（保守口径：买入多扣万5）
    assert t["pnl"] == round(100.0 - 3.05, 2) == 96.95, t["pnl"]
    # 终值 = 初始 + 已实现盈亏
    assert abs(res["end_balance"] - (10000 + 96.95)) < 1e-6


def test_grades_filter():
    """评级过滤：只做 S 级（老板约束）"""
    df = make_df([
        sig("S1", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0, grade="S"),
        sig("A1", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0, grade="A"),
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2, grades=["S"])
    assert res["n_exec"] == 1 and res["trades"][0]["code"] == "S1"
    # 不传 grades（None）→ 全评级
    res2 = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2, grades=None)
    assert res2["n_exec"] == 2


def test_equity_curve_and_max_drawdown():
    """资金曲线终点 = 终值；最大回撤（金额+百分比）计算正确"""
    df = make_df([
        sig("LOSS", "2024-01-05", 10.0, 1.0, "2024-02-05", 8.0, -2.0),   # 亏
        sig("WIN", "2024-02-06", 10.0, 1.0, "2024-03-06", 13.0, 3.0),    # 赚
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    eq = res["equity"]
    assert len(eq) == 3  # 起点 + 两笔平仓
    assert eq["balance"].iloc[0] == 10000.0
    assert abs(eq["balance"].iloc[-1] - res["end_balance"]) < 1e-6
    # 最大回撤 = 峰值(起点) - 谷值（首笔亏损平仓后）
    expected_dd = 10000.0 - (10000.0 + res["trades"][0]["pnl"])
    assert abs(res["max_dd"] - expected_dd) < 1e-6
    assert abs(res["max_dd_pct"] - res["max_dd"] / 10000 * 100) < 1e-9


def test_drawdown_duration():
    """回撤时长：峰值 → 修复到新高 的最长自然日跨度"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 8.0, -2.0),   # 峰 → 谷
        sig("B", "2024-02-06", 10.0, 1.0, "2024-03-06", 8.0, -2.0),   # 谷底延伸 30 天
        sig("C", "2024-04-08", 10.0, 1.0, "2024-05-08", 15.0, 5.0),   # 修复创新高
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    # 谷底 2024-03-06 距峰 2024-02-05 = 30 天；修复笔平仓 2024-05-08 距峰 = 93 天
    assert res["dd_days"] >= 60, res["dd_days"]


def test_pace_100_trades_estimate():
    """100 笔节奏预估 = 100 / 年化笔数（按信号日跨度；3 笔/约 0.4 年 → 约 7.5 笔/年 → 约 160 个月）"""
    df = make_df([
        sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0),
        sig("B", "2024-03-06", 10.0, 1.0, "2024-04-05", 11.0, 1.0),
        sig("C", "2024-05-06", 10.0, 1.0, "2024-06-05", 11.0, 1.0),
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    assert res["n_exec"] == 3
    assert res["per_year"] > 5  # 3 笔 / ~0.4 年
    # 自洽：months_for_100 = 100 / per_year × 12
    assert abs(res["months_for_100"] - 100 / res["per_year"] * 12) < 1e-6
    assert 140 < res["months_for_100"] < 180
    assert res["months_for_100"] > 0


def test_entry_uses_engine_entry_price_not_close():
    """成交价 = 引擎入场价（entry_20d，prebreak=触发价）而非信号日 close——R 口径一致"""
    # 突破日大阳线：close=10（信号日收盘），触发价 entry=12（远离收盘）
    df = make_df([sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 13.0, 1.0, entry=12.0)])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    t = res["trades"][0]
    assert t["entry"] == 12.0  # 触发价成交，不是 close=10
    # 资金约束按触发价算：10000//12 = 833 → 整手 800 股；风险 150//1.0=150 → 100 股
    assert t["shares"] == 100
    # R 与金额盈亏同号：exit 13 > entry 12 → R>0 且 pnl>0（费用约 3.25 元 < 毛利 100）
    assert t["r"] > 0 and t["pnl"] > 0
    assert t["pnl"] == round((13.0 - 12.0) * 100 - 1.6 - 1.65, 2)  # 96.75


def test_avg_hold_days_trading_days():
    """平均持有天数 = 交易日口径（busday_count）"""
    df = make_df([sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0)])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    assert res["avg_hold_days"] == np.busday_count("2024-01-05", "2024-02-05")


# ============ C23 收紧（2026-08-06 老板拍板替换进策略）：动量≤10% + 止损 0.5~3 元 ============

def test_c23_mask_filters_momentum_and_risk():
    """c23_mask: 动量>10% 或 止损<0.5/3.0元 均被滤——与 tighten_compare 同式同常量"""
    from 回测系统.sim_capital import c23_mask
    df = pd.DataFrame([
        {"mom20": 0.05, "risk": 1.0},     # 达标
        {"mom20": 0.12, "risk": 1.0},     # 动量超
        {"mom20": 0.05, "risk": 0.4},     # 止损太近
        {"mom20": 0.05, "risk": 3.5},     # 止损太远
        {"mom20": float("nan"), "risk": 1.0},  # mom20 复算失败 → 不达标（未知按不达标）
    ])
    mask = c23_mask(df)
    assert list(mask) == [True, False, False, False, False]


def test_simulate_capital_c23_filter():
    """simulate_capital(c23=True)：只成交 C23 达标信号（动量≤10% 且 止损 0.5~3 元）"""
    rows = [
        sig("OK1", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0, mom20=0.05),   # 达标
        sig("MOM", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0, mom20=0.15),   # 动量超
        sig("NEAR", "2024-01-05", 10.0, 0.4, "2024-02-05", 11.0, 1.0, mom20=0.05),  # 止损太近
    ]
    df = make_df(rows)
    # make_df 原哨兵 risk=999 会被 C23 掩码滤掉 → max_date 判定失效；补 C23 达标哨兵：
    # 高价股（1e6 元/股）→ 资金不足恒拒买（不成交），但留在 sub 推高 max_date
    tail = pd.Timestamp("2024-02-05") + pd.Timedelta(days=3)
    sentinel_c23 = {
        "mode": "prebreak", "code": "SENT2", "date": tail.strftime("%Y-%m-%d"),
        "grade": "S", "close": 1e6, "risk": 1.0,
        "triggered_20d": 1, "entry_20d": 1e6, "exit_20d": 1e6,
        "exit_date_20d": tail.strftime("%Y-%m-%d"), "r_20d": 0.0, "mom20": 0.05,
    }
    df = pd.concat([df, pd.DataFrame([sentinel_c23])], ignore_index=True)

    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2, c23=True)
    assert [t["code"] for t in res["trades"]] == ["OK1"]
    # 不开启 c23 → 三笔同日均进（持仓满 2 → 按 code 序前两笔成交；对照）
    res2 = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    assert [t["code"] for t in res2["trades"]] == ["MOM", "NEAR"]
