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
        text = execution_card.order_card([cand()], capital=5600, risk_ratio=0.025)
        assert "1R 日" in text
        assert "140 元" in text  # 5600×2.5%（R-050 配置行风险额断言）
        assert "5600×" in text

    def test_05r_day_gives_half_risk_and_confirm_flow(self, monkeypatch):
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 0.5)
        text = execution_card.order_card([cand()], capital=5600, risk_ratio=0.025)
        assert "0.5R 日" in text
        assert "70 元" in text  # 5600×2.5%×0.5
        # 次日收线确认流程说明（三条件 + 补仓/平仓）
        assert "收线确认" in text
        assert "补 0.5R 至总 1R" in text
        assert "止损" in text

    def test_1r_day_shares_full_lot(self, monkeypatch):
        """1R 日：每股风险 0.7 → 实盘资金 8401.26×0.025=210 → 210//0.7=300 股"""
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 1.0)
        text = execution_card.order_card([cand(risk=0.7)], capital=5600,
                                         risk_ratio=0.025)
        assert "挂单 300 股" in text

    def test_05r_day_shares_half_budget(self, monkeypatch):
        """0.5R 日：每股风险 0.5 → 实盘资金 8401.26×0.025×0.5=105 → 105//0.5=210 → 200 股"""
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 0.5)
        text = execution_card.order_card([cand(risk=0.5)], capital=5600,
                                         risk_ratio=0.025)
        assert "挂单 200 股" in text
        assert "0.5R 半额风险预算（70 元）" in text

    def test_unaffordable_marked(self, monkeypatch):
        """0.5R 日：每股风险 1.1 → 实盘半额预算 105/1.1=95 股 <100 → 不可买"""
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale",
                            lambda: 0.5)
        text = execution_card.order_card([cand(risk=1.1)], capital=5600,
                                         risk_ratio=0.025)
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
        # K线：01-03 阴 / 01-04 开仓（收盘 10.2，放量 3M 量比 3.0 满足 R-053 B）/ 01-05 确认
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 3000000),   # 2025-01-04 开仓日（R-053 放量达标）
            (10.3, 10.7, 10.2, 10.5, 1000000),   # 2025-01-05 确认日
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        monkeypatch.setattr(execution_card.sim_trading, "check_affordability",
                            lambda price, risk_ps, risk_scale=1.0, capital=None: (100, ""))
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "补 0.5R 挂单 100 股 @ 10.50" in text

    def test_live_row_add_uses_live_capital(self, monkeypatch):
        """2026-08-11 修复：实盘行（LIVE trade_id）补仓走实盘口径 capital=None；
        模拟行（SIM trade_id）走 SIM_CAPITAL——此前实盘 0.5R 仓补仓量错用
        模拟线 10 万口径（600833 实盘 100 股提示补 2300 股）"""
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 3000000),   # 2025-01-04 开仓日（R-053 放量达标）
            (10.3, 10.7, 10.2, 10.5, 1000000),   # 2025-01-05 确认日
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        captured = {}

        def fake_afford(price, risk_ps, risk_scale=1.0, capital=None):
            captured["capital"] = capital
            return (100, "")

        monkeypatch.setattr(execution_card.sim_trading, "check_affordability", fake_afford)
        base = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
                "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
                "phase": "half", "status": "open"}
        # 实盘行
        captured.clear()
        execution_card.position_card(rows=[dict(base, trade_id="LIVE20250104193001")])
        assert captured.get("capital") is None, "实盘行补仓必须走实盘 capital.json 口径"
        # 模拟行
        captured.clear()
        execution_card.position_card(rows=[dict(base, trade_id="SIM2025010402074601")])
        assert captured.get("capital") == execution_card.sim_trading.SIM_CAPITAL, \
            "模拟行补仓保持 SIM_CAPITAL（10 万名义）"

    def test_reject_exit_action(self, monkeypatch):
        """首根收盘跌破进场价 + T+2 仍未确认 → delay2 以 T+2 收盘平仓指令"""
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 1000000),   # 2025-01-04 开仓日
            (10.0, 10.2, 9.9, 10.05, 1000000),   # 2025-01-05 首根收盘 10.05 < 进场 10.2 → 不确认
            (10.0, 10.2, 9.9, 10.0, 1000000),    # 2025-01-06 T+2 收盘 10.0 仍 < 进场 10.2 → 平仓
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "收线未确认" in text
        assert "平仓" in text

    def test_reject_waits_no_t2(self, monkeypatch):
        """首根收盘跌破进场价但 T+2 未出现 → delay2 等待二次确认（不平仓）"""
        k = mk_kline([
            (10.0, 10.4, 9.9, 10.1, 1000000),
            (10.1, 10.5, 10.0, 10.2, 1000000),   # 2025-01-04 开仓日
            (10.0, 10.2, 9.9, 10.05, 1000000),   # 2025-01-05 首根收盘 10.05 < 进场 10.2 → 不确认
        ])
        monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                            lambda code, use_cache=True: k)
        row = {"symbol": "600419", "name": "测试股", "date": "2025-01-04",
               "entry_price": "10.20", "stop_loss": "9.80", "volume": "100",
               "phase": "half", "status": "open"}
        text = execution_card.position_card(rows=[row])
        assert "延迟二次确认" in text
        assert "等待" in text
        assert "平仓" not in text

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


class TestR051FundCheck:
    def test_051_fund_occupancy_check(self, monkeypatch):
        """R-051 挂单资金占用校验行（2026-08-11 老板拍板采纳；R-074 修订口径 2026-08-12）

        修订（交易部复核 P1-1）：触发占用 = 新候选 + 已挂单（云单表）；加可用现金行。
        """
        monkeypatch.setattr(execution_card.sim_trading, "_market_env_scale", lambda: 1.0)
        monkeypatch.setattr(execution_card, "_open_hold_cost", lambda: 2000.0)
        monkeypatch.setattr(execution_card, "_open_pending_add", lambda: 1211.0)
        monkeypatch.setattr(execution_card, "_pending_orders_cost", lambda: 1952.0)
        text = execution_card.order_card([cand(price=18.0)], capital=8401, risk_ratio=0.025)
        assert "资金占用校验" in text
        assert "已持 2000" in text and "待补仓 1211" in text
        assert "触发占用 3752" in text and "新候选 1800" in text and "已挂单 1952" in text
        assert "可用现金约 6401" in text   # 8401 - 已持 2000（R-051 补仓判定口径）


def test_cloud_order_reminder_calibration_states(monkeypatch, tmp_path):
    """R-065 云单校准三态：一致→持续埋伏 / 过时→撤单重挂 / 不在候选→撤单（均须含撤单提示）"""
    from 分析决策.跟踪.execution_card import cloud_order_reminder
    track = tmp_path / "云条件单跟踪.md"
    track.write_text(
        "| 股票 | 挂单日 | 买入触发价 | 止损价 | 股数 | 状态 | 触发日 | 备注 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 600001 甲 | 2026-08-11 | ≥ 10.50 | ≤ 9.80 | 100 | 挂单中 | | 一致样例 |\n"
        "| 600002 乙 | 2026-08-11 | ≥ 14.02 | ≤ 13.33 | 100 | 挂单中 | | 过时样例 |\n"
        "| 600003 丙 | 2026-08-11 | ≥ 8.80 | ≤ 8.10 | 100 | 挂单中 | | 不在候选样例 |\n",
        encoding="utf-8")
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "scan_result_20260811_190000.csv").write_text(
        "code,name,触发价,止损价\n"
        "600001,甲,10.50,9.80\n"
        "600002,乙,13.45,12.78\n",
        encoding="utf-8-sig")
    text = cloud_order_reminder(track_file=track, scan_dir=scan_dir)
    assert "持续埋伏中" in text and "600001" in text
    assert "参数过时" in text and "600002" in text and "建议撤单" in text
    assert "已不在最新扫描" in text and "600003" in text and "建议撤单" in text
    assert "13.45" in text and "12.78" in text  # 最新口径展示


def test_scan_s_overview_lists_all_s_with_reasons(monkeypatch, tmp_path):
    """R-067：S 级全览列出全部 S 级（含被过滤的）+ 买/不买原因标注"""
    from 分析决策.跟踪.execution_card import scan_s_overview
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    # 主文件（合格候选）
    (scan_dir / "scan_result_20260811_182400.csv").write_text(
        "code,name,评级,触发价,止损价\n600001,甲,S,10.5,9.8\n", encoding="utf-8-sig")
    # C23 不达标变体
    (scan_dir / "scan_result_20260811_182400_c23.csv").write_text(
        "code,name,评级,触发价,止损价,C23原因\n600002,乙,S,8.2,7.9,止损0.4元<0.5\n",
        encoding="utf-8-sig")
    # 放量不达标变体
    (scan_dir / "scan_result_20260811_182400_vol.csv").write_text(
        "code,name,评级,触发价,止损价\n600003,丙,S,9.1,8.5\n", encoding="utf-8-sig")
    # 已突破变体
    (scan_dir / "scan_result_20260811_182400_broken.csv").write_text(
        "code,name,评级,触发价,止损价\n600004,丁,S,7.5,7.0\n", encoding="utf-8-sig")
    text = scan_s_overview(scan_dir=scan_dir)
    assert "600001" in text and "合格候选" in text
    assert "600002" in text and "C23 不达标" in text and "止损0.4元<0.5" in text
    assert "600003" in text and "放量不达标" in text
    assert "600004" in text and "已突破" in text


def test_protect_card_short_hold_active_is_reference_only(monkeypatch, tmp_path):
    """R-068：短持仓（<21 根）主动出场 = 参考信号（继续持有），不构成"建议卖出"
    （600833 教训：曾据保护卡信号误建议开盘卖）"""
    import pandas as pd
    from 分析决策.跟踪.execution_card import protect_card
    from 数据基础.duckdb.reader import read_kline
    df = read_kline('600833', shared=True)
    dates = df['日期'].astype(str).str[:10]
    entry_idx = next(i for i, d in enumerate(dates) if d == '2026-08-10')
    # 构造短持仓行（trail_stop 空 + 日期 08-10 进场——切片仅 2 根）
    rows = [{"symbol": "600833", "name": "第一医药", "date": "2026-08-10",
             "status": "open", "entry_price": "10.18", "stop_loss": "9.64",
             "trail_stop": None, "highest": None, "lowest": None, "volume": "100",
             "grade_at_entry": "A", "ty_high": "0", "ty_low": "0"}]
    text = protect_card(rows)
    assert "参考信号" in text and "继续持有" in text
    assert "建议卖出" not in text
    # 止损值正常（trail_stop None → 用 stop_loss，NaN truthy bug 回归）
    assert "10.18" in text  # 平保止损保护显示


def test_cloud_order_reminder_broken_stop_red_cancel(monkeypatch, tmp_path):
    """R-072：挂单现价 ≤ 止损（结构破坏）→ 🔴 挂单失效建议撤单（000429 教训根因）"""
    from 分析决策.跟踪.execution_card import cloud_order_reminder
    track = tmp_path / "云条件单跟踪.md"
    track.write_text(
        "| 股票 | 挂单日 | 买入触发价 | 止损价 | 股数 | 状态 | 触发日 | 备注 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 000429 破位 | 2026-08-11 | ≥ 14.02 | ≤ 13.33 | 100 | 挂单中 | | 破位样例 |\n",
        encoding="utf-8")
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "scan_result_20260811_190000.csv").write_text(
        "code,name,price,触发价,止损价,TY高,TY低\n000429,粤高速A,13.29,14.02,13.33,13.99,13.36\n",
        encoding="utf-8-sig")
    text = cloud_order_reminder(track_file=track, scan_dir=scan_dir)
    assert "挂单失效" in text and "建议撤单" in text and "持续埋伏中" not in text
