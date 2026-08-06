"""执行卡单元测试（2026-08-06 老板确认四连包①：1R/0.5R 双路径执行卡）

覆盖：挂单指引卡（1R 日/0.5R 日标注 + 风险额 112/56 元 + 逐票手数）/
分步建仓持仓卡（phase=="half" 持仓的判定指令：确认补仓/不确认平仓/止损/等待）/
无持仓与无候选的兜底文本。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 分析决策.跟踪 import execution_card


def mk_kline(rows: list[tuple]) -> pd.DataFrame:
    """rows = [(开盘, 最高, 最低, 收盘, 成交量), ...]；日期从 2025-01-03 起逐日（含周末，测试用）"""
    import datetime as _dt
    dates = [(_dt.date(2025, 1, 3) + _dt.timedelta(days=i)).strftime("%Y-%m-%d")
             for i in range(len(rows))]
    return pd.DataFrame({
        "日期": dates,
        "开盘": [r[0] for r in rows],
        "最高": [r[1] for r in rows],
        "最低": [r[2] for r in rows],
        "收盘": [r[3] for r in rows],
        "成交量": [r[4] if len(r) > 4 else 1_000_000 for r in rows],
    })


def cand(code: str = "600419", price: float = 10.5, stop: float = 9.8,
         risk: float = 0.7, grade: str = "S") -> dict:
    return {"code": code, "name": "测试股", "触发价": price, "止损价": stop,
            "每股风险": risk, "评级": grade}


class TestOrderCard:
    def test_1r_day_gives_full_risk_amount(self, monkeypatch):
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 1.0)
        text = execution_card.order_card([cand()], capital=5600, risk_ratio=0.02)
        assert "1R 日" in text
        assert "112 元" in text  # 5600×2%
        assert "5600×2%" in text

    def test_05r_day_gives_half_risk_and_confirm_flow(self, monkeypatch):
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 0.5)
        text = execution_card.order_card([cand()], capital=5600, risk_ratio=0.02)
        assert "0.5R 日" in text
        assert "56 元" in text  # 5600×2%×0.5
        # 次日收线确认流程说明（三条件 + 补仓/平仓）
        assert "收线确认" in text
        assert "补 0.5R 至总 1R" in text
        assert "止损" in text

    def test_1r_day_shares_full_lot(self, monkeypatch):
        """1R 日：风险额 112 → 每股风险 0.7 → 160 股 → 整手 100 股"""
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 1.0)
        text = execution_card.order_card([cand(risk=0.7)], capital=5600,
                                         risk_ratio=0.02)
        assert "挂单 100 股" in text

    def test_05r_day_shares_half_budget(self, monkeypatch):
        """0.5R 日：风险额 56 → 每股风险 0.5 → 112 股 → 100 股（半额预算手数）"""
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 0.5)
        text = execution_card.order_card([cand(risk=0.5)], capital=5600,
                                         risk_ratio=0.02)
        assert "挂单 100 股" in text
        assert "0.5R 半额风险预算（56 元）" in text

    def test_unaffordable_marked(self, monkeypatch):
        """0.5R 日：每股风险 0.7 → 半额预算 56/0.7=80 股 <100 → 不可买"""
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 0.5)
        text = execution_card.order_card([cand(risk=0.7)], capital=5600,
                                         risk_ratio=0.02)
        assert "不可买" in text

    def test_no_candidates(self, monkeypatch):
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 1.0)
        text = execution_card.order_card([], capital=5600)
        assert "今日无新候选" in text

    def test_index_unavailable_defaults_1r(self, monkeypatch):
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: None)
        text = execution_card.order_card([cand()], capital=5600)
        assert "1R 日" in text  # 放行侧


class TestPositionCard:
    def test_no_half_positions(self, monkeypatch):
        text = execution_card.position_card(rows=[])
        assert "无在持 0.5R 试探仓" in text

    def test_confirm_add_action(self, monkeypatch):
        """确认收线 → 补仓指令（开仓日 2025-01-06，确认日 2025-01-07 收阳走高）"""
        # K线：01-03 阴 / 01-04 开仓（收盘 10.2）/ 01-05 确认（收盘 10.5 > 进场 10.2 且 > 开仓日收盘）
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 1000000),   # 2025-01-04 开仓日
            (10.3, 10.7, 10.2, 10.5, 1000000),   # 2025-01-05 确认日
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        monkeypatch.setattr(execution_card.sim_trading, "check_affordability",
                            lambda price, risk_ps, risk_scale=1.0: (100, ""))
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "补 0.5R 挂单 100 股 @ 10.50" in text

    def test_reject_exit_action(self, monkeypatch):
        """确认日收盘跌破进场价 → 不确认平仓指令"""
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 1000000),   # 2025-01-04 开仓日
            (10.0, 10.2, 9.9, 10.05, 1000000),   # 2025-01-05 收盘 10.05 < 进场 10.2
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "收线未确认" in text
        assert "平仓" in text

    def test_stop_exit_action(self, monkeypatch):
        """确认日最低 ≤ 止损价 → 层面1 止损平仓指令"""
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 1000000),   # 2025-01-04 开仓日
            (10.0, 10.3, 9.75, 10.15, 1000000),  # 2025-01-05 最低 9.75 ≤ 止损 9.8
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "触止损" in text
        assert "9.80" in text

    def test_wait_when_no_confirm_bar(self, monkeypatch):
        """收线未出现（数据不足两日）→ 持有等待指令"""
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 1000000),   # 仅到开仓日 01-04（无确认收线）
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "收线未出现" in text
        assert "等待" in text

    def test_non_half_open_ignored(self, monkeypatch):
        """phase==""（直接 1R 持仓）不进入分步持仓卡"""
        rows = [{"symbol": "600000", "date": "2025-01-04",
                 "entry_price": "10.00", "stop_loss": "9.50", "volume": "200",
                 "phase": "", "status": "open"}]
        text = execution_card.position_card(rows=rows)
        assert "无在持 0.5R 试探仓" in text
