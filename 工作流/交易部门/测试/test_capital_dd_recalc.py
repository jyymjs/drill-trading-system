"""总资产口径回撤重算单元测试（2026-08-06 老板拍板）

覆盖：逐日总资产曲线构建（现金+持仓市值）/ 现金流终点与 simulate_capital 终值一致 /
同日先平后开顺序 / 停牌前值填充 / 最大回撤计算（金额+%+时长+峰谷日期）/ 空交易集
（纯函数测试，kline 用假数据注入，不依赖 duckdb）
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))  # 回测系统 包

from 回测系统.capital_dd_recalc import build_total_asset_curve, max_drawdown
from 回测系统.sim_capital import simulate_capital

# ── 假数据工具 ──

_TD = pd.date_range("2024-01-01", "2024-03-31", freq="B")  # 工作日序列（近似交易日）


def fake_kline(close: float, dates: pd.DatetimeIndex | None = None,
               gap: tuple[str, str] | None = None) -> pd.DataFrame:
    """假 qfq K线（列名与 read_kline 一致：日期/收盘，升序）

    gap: (start, end) 停牌区间（闭区间剔除）
    """
    d = _TD if dates is None else dates
    df = pd.DataFrame({"日期": d, "收盘": close})
    if gap:
        s, e = pd.Timestamp(gap[0]), pd.Timestamp(gap[1])
        df = df[~((df["日期"] >= s) & (df["日期"] <= e))].reset_index(drop=True)
    return df


def kline_provider(*mapping: tuple[str, pd.DataFrame]) -> callable:
    """构造 kline_fn：code → DataFrame（缺省返回 None）"""
    m = {c: k for c, k in mapping}

    def _fn(code):
        return m.get(str(code))
    return _fn


def make_sig(code: str, date: str, close: float, risk: float, exit_date: str,
             exit_px: float, r: float, entry: float | None = None) -> dict:
    """单笔信号行（与 test_sim_capital 同构，含哨兵可被拒）"""
    return {
        "mode": "prebreak", "code": code, "date": date, "grade": "S",
        "close": close, "risk": risk,
        "triggered_20d": 1, "entry_20d": entry if entry is not None else close,
        "exit_20d": exit_px, "exit_date_20d": exit_date, "r_20d": r,
    }


def make_df(rows: list[dict]) -> pd.DataFrame:
    """signals-like DataFrame + 尾部哨兵行（同 test_sim_capital 防"持仓未完成"误拒）"""
    df = pd.DataFrame(rows)
    for col in ("mode", "code", "grade", "triggered_20d", "entry_20d", "exit_20d",
                "exit_date_20d", "r_20d"):
        if col not in df.columns:
            df[col] = ""
    tail = max(pd.to_datetime(r["exit_date_20d"]) for r in rows if r.get("exit_date_20d"))
    tail = tail + pd.Timedelta(days=3)
    sentinel = {
        "mode": "prebreak", "code": "SENT", "date": tail.strftime("%Y-%m-%d"),
        "grade": "S", "close": 100.0, "risk": 999.0,
        "triggered_20d": 1, "entry_20d": 100.0, "exit_20d": 100.0,
        "exit_date_20d": tail.strftime("%Y-%m-%d"), "r_20d": 0.0,
    }
    return pd.concat([df, pd.DataFrame([sentinel])], ignore_index=True)


# ── 总资产曲线构建 ──

def test_curve_single_trade_cash_and_market_value():
    """单笔：入场日扣款+市值计入；持仓期市值恒定；出场日兑现现金"""
    trades = [{
        "code": "000001", "date": "2024-01-05", "exit_date": "2024-02-05",
        "shares": 100, "entry": 10.0, "exit_price": 11.0,
    }]
    kfn = kline_provider(("000001", fake_kline(10.0)))
    curve = build_total_asset_curve(trades, capital=10000, kline_fn=kfn)
    assert len(curve) > 0
    # 起点行：总资产 = 初始资金
    assert curve["total_asset"].iloc[0] == 10000.0
    # 入场日：现金 = 10000 - (100×10 + 佣金1.0 + 印花税0.5) = 8998.5；市值 = 100×10
    d_in = curve[curve["date"] == pd.Timestamp("2024-01-05")]
    assert len(d_in) == 1
    assert d_in["cash"].iloc[0] == 8998.5
    assert d_in["market_value"].iloc[0] == 1000.0
    assert d_in["total_asset"].iloc[0] == 9998.5
    # 出场日：现金加回（11×100 - 佣金1.0 - 印花税0.55），市值归零
    d_out = curve[curve["date"] == pd.Timestamp("2024-02-05")]
    assert len(d_out) == 1
    assert d_out["market_value"].iloc[0] == 0.0
    assert d_out["cash"].iloc[0] == 8998.5 + 1100.0 - 1.55
    # 终值 = 10000 + pnl(96.95，与 test_fees_included_in_pnl 同口径)
    assert curve["total_asset"].iloc[-1] == 10000.0 + 96.95


def test_curve_end_matches_simulate_capital():
    """现金流一致性：曲线终点 == simulate_capital 终值（事件同源自检）"""
    df = make_df([make_sig("000001", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0)])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    assert res["n_exec"] == 1
    kfn = kline_provider(("000001", fake_kline(10.0)))
    curve = build_total_asset_curve(res["trades"], capital=10000, kline_fn=kfn)
    assert abs(curve["total_asset"].iloc[-1] - res["end_balance"]) < 0.01
    assert curve["total_asset"].iloc[-1] == 10000.0 + 96.95


def test_curve_same_day_exit_then_entry():
    """同日先平后开（对齐 simulate_capital 循环顺序）：出场日旧仓兑现、新仓市值计入"""
    df = make_df([
        make_sig("A", "2024-01-05", 10.0, 1.0, "2024-02-05", 11.0, 1.0),
        make_sig("B", "2024-02-05", 20.0, 1.0, "2024-03-05", 22.0, 2.0),  # 与 A 同日开仓
    ])
    res = simulate_capital(df, capital=10000, risk_ratio=0.015, max_positions=2)
    assert res["n_exec"] == 2, res["n_exec"]
    kfn = kline_provider(("A", fake_kline(10.0)), ("B", fake_kline(20.0)))
    curve = build_total_asset_curve(res["trades"], capital=10000, kline_fn=kfn)
    tb = next(t for t in res["trades"] if t["code"] == "B")
    d = curve[curve["date"] == pd.Timestamp("2024-02-05")]
    assert len(d) == 1
    # 2024-02-05：A 已平（市值 0），B 市值 = B 股数 × 20
    assert d["market_value"].iloc[0] == tb["shares"] * 20.0
    # 曲线终点 = 终值
    assert abs(curve["total_asset"].iloc[-1] - res["end_balance"]) < 0.01


def test_curve_ffill_on_pause():
    """停牌前值填充：A 停牌周、B 正常交易 → 并集日历含停牌日，A 按前值估值"""
    ka = fake_kline(10.0, gap=("2024-01-15", "2024-01-19"))   # A 停牌一周
    kb = fake_kline(20.0)                                     # B 全勤
    trades = [
        {"code": "000001", "date": "2024-01-05", "exit_date": "2024-02-05",
         "shares": 100, "entry": 10.0, "exit_price": 11.0},
        {"code": "000002", "date": "2024-01-05", "exit_date": "2024-02-05",
         "shares": 50, "entry": 20.0, "exit_price": 22.0},
    ]
    curve = build_total_asset_curve(trades, capital=10000,
                                    kline_fn=kline_provider(("000001", ka), ("000002", kb)))
    # 停牌日 1-15：A 市值 = 100×10（前值 1-12 收盘），B 市值 = 50×20
    d_pause = curve[curve["date"] == pd.Timestamp("2024-01-15")]
    assert len(d_pause) == 1
    assert d_pause["market_value"].iloc[0] == 1000.0 + 1000.0
    # 停牌期间（1-15~1-19）A 市值恒 1000：曲线中该区间市场价值 = 2000 - B 波动 = 2000（B 恒 20）
    mask = (curve["date"] >= pd.Timestamp("2024-01-15")) & \
           (curve["date"] <= pd.Timestamp("2024-01-19"))
    assert (curve.loc[mask, "market_value"] == 2000.0).all()


def test_curve_empty_trades():
    """空交易集：返回空曲线，回撤计算零值兜底"""
    curve = build_total_asset_curve([], capital=5600, kline_fn=kline_provider())
    assert curve.empty
    dd = max_drawdown(curve, 5600)
    assert dd["max_dd"] == 0.0 and dd["dd_days"] == 0


# ── 最大回撤 ──

def test_max_drawdown_basic():
    """已知曲线：峰值→谷底最大跌幅（金额/占初始/占峰值）+ 峰谷日期 + 时长"""
    curve = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05", "2024-01-10", "2024-02-05", "2024-03-01"]),
        "total_asset": [10000.0, 10200.0, 9500.0, 10500.0],
    })
    dd = max_drawdown(curve, initial=10000.0)
    assert dd["max_dd"] == 700.0
    assert dd["max_dd_pct"] == 7.0
    assert dd["max_dd_pct_peak"] == round(700.0 / 10200.0 * 100, 2)
    assert dd["peak_date"] == "2024-01-10"
    assert dd["trough_date"] == "2024-02-05"
    assert dd["dd_days"] == 26  # 峰 1-10 → 谷 2-05 自然日跨度


def test_max_drawdown_peak_tracking():
    """历史峰值追踪：新高后回落算新段；时长 = 峰值 → 最远未恢复点（旧口径同语义）"""
    curve = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05", "2024-01-15", "2024-01-20",
                                "2024-02-10", "2024-02-20"]),
        "total_asset": [10000.0, 9900.0, 10800.0, 10100.0, 10400.0],
    })
    dd = max_drawdown(curve, initial=10000.0)
    # 峰 1-20 (10800) → 谷 2-10 (10100)：700 元；时长 = 峰值→最远未恢复点
    # （2-20 仍 10400 < 10800 未修复 → 1-20→2-20 = 31 天，与 sim_capital 旧算法一致）
    assert dd["max_dd"] == 700.0
    assert dd["peak_date"] == "2024-01-20"
    assert dd["trough_date"] == "2024-02-10"
    assert dd["dd_days"] == 31


def test_max_drawdown_flat_or_up_only():
    """无回撤（只涨/平）：金额 0、时长 0"""
    curve = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05", "2024-01-10", "2024-01-15"]),
        "total_asset": [10000.0, 10000.0, 10000.0],
    })
    dd = max_drawdown(curve, initial=10000.0)
    assert dd["max_dd"] == 0.0 and dd["dd_days"] == 0


def test_curve_start_peak_from_initial():
    """起点计入峰值：首段回撤从初始资金起算（与旧口径 peak=capital 一致）"""
    trades = [{
        "code": "000001", "date": "2024-01-05", "exit_date": "2024-02-05",
        "shares": 100, "entry": 10.0, "exit_price": 10.0,   # 平价出场：回撤 = 双边费用
    }]
    kfn = kline_provider(("000001", fake_kline(10.0)))
    curve = build_total_asset_curve(trades, capital=10000, kline_fn=kfn)
    dd = max_drawdown(curve, initial=10000.0)
    # 持仓期回撤 = 买入费用 1.5；出场后回撤 = 双边费用 1.5 + 1.5 = 3.0（历史峰值 10000 起算）
    assert 2.5 < dd["max_dd"] < 3.5
    assert dd["dd_days"] > 0
