"""模拟条件单单元测试（2026-08-08 老板确认方案①③：无资金限制对照线）

覆盖：扫描候选 → 模拟条件单（status=pending，10 万名义资金）/
到价才成交（未到价不假买入——老板核心担忧）/
超期（5 交易日）未触发撤销留痕 / 幂等防重复挂单 /
实盘 5600 买不起的票模拟线买得起（无资金限制语义）/
参数无效 + 池外票不挂单（600001 污染防护同源）。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import 分析决策.跟踪.sim_trading as st


def mk_kline(days: list[tuple]) -> pd.DataFrame:
    """days = [(日期, 开盘, 最高, 最低, 收盘), ...]"""
    return pd.DataFrame({
        "日期": [d[0] for d in days],
        "开盘": [d[1] for d in days],
        "最高": [d[2] for d in days],
        "最低": [d[3] for d in days],
        "收盘": [d[4] for d in days],
        "成交量": [1_000_000] * len(days),
    })


def pend_row(code: str = "600001", trigger: float = 10.5, stop: float = 9.8,
             created: str = "2025-01-03") -> dict:
    return {
        "trade_id": "SIMTEST1", "date": created, "symbol": code, "name": "测试",
        "direction": "long", "market": "stock",
        "entry_price": str(trigger), "stop_loss": str(stop), "volume": "100",
        "grade_at_entry": "S", "ty_high": "0", "ty_low": "0",
        "status": "pending", "exit_price": "", "exit_date": "",
        "exit_reason": "", "r_multiple": "", "pnl": "",
        "env_scale": "1.0", "phase": "", "created_date": created,
    }


def write_scan_csv(tmp_path, rows: list[dict]) -> str:
    """构造扫描结果 CSV（带 BOM，同真实扫描文件编码）"""
    path = tmp_path / "scan_result_test.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def use_tmp_journal(monkeypatch, tmp_path):
    """把 sim_journal 重定向到临时文件（测试隔离）"""
    f = tmp_path / "sim_journal.csv"
    monkeypatch.setattr(st, "SIM_FILE", f)


def test_auto_open_creates_pending_orders(monkeypatch, tmp_path):
    """扫描候选 → 全部生成 pending 条件单（10 万名义资金 2% 规则）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(st, "_market_env_scale", lambda: 1.0)
    monkeypatch.setattr("数据基础.配置.stock_pool.get_stock_codes",
                        lambda: {"600001", "600002"})
    csv_path = write_scan_csv(tmp_path, [
        {"code": "600001", "name": "甲", "评级": "S", "触发价": 10.5,
         "止损价": 9.8, "每股风险": 0.7, "TY高": 11.0, "TY低": 9.5},
        {"code": "600002", "name": "乙", "评级": "A", "触发价": 8.2,
         "止损价": 7.9, "每股风险": 0.3, "TY高": 8.5, "TY低": 7.8},
    ])
    text = st.sim_auto_open(csv_path=csv_path)
    assert "新建 2 笔条件单" in text
    assert "10 万名义资金" in text
    rows = st._read_all()
    assert [r["status"] for r in rows] == ["pending", "pending"]
    # 10 万 × 2% × 1R = 2000 元风险预算 → 每股风险 0.7 → 2000//0.7//100*100 = 2800 股
    assert int(rows[0]["volume"]) == 2800
    assert rows[0]["entry_price"] == "10.5"  # 条件单记录触发价


def test_auto_open_skips_invalid_and_out_of_pool(monkeypatch, tmp_path):
    """参数无效（触发价=0）与池外票不挂单；重复调用幂等"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(st, "_market_env_scale", lambda: 1.0)
    monkeypatch.setattr("数据基础.配置.stock_pool.get_stock_codes",
                        lambda: {"600001"})
    csv_path = write_scan_csv(tmp_path, [
        {"code": "600001", "name": "甲", "评级": "S", "触发价": 10.5,
         "止损价": 9.8, "每股风险": 0.7},
        {"code": "600002", "name": "池外", "评级": "S", "触发价": 8.2,
         "止损价": 7.9, "每股风险": 0.3},
        {"code": "600003", "name": "无效", "评级": "S", "触发价": 0,
         "止损价": 7.0, "每股风险": 0.5},
    ])
    st.sim_auto_open(csv_path=csv_path)
    rows = st._read_all()
    assert len(rows) == 1 and rows[0]["symbol"] == "600001"
    # 幂等：同日再跑一遍 → 不重复建单
    st.sim_auto_open(csv_path=csv_path)
    assert len(st._read_all()) == 1


def test_pending_fills_only_when_high_hits_trigger(monkeypatch, tmp_path):
    """核心语义：到价才成交——挂单日后 K 线最高价 ≥ 触发价 → open"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-02", 9.0, 9.5, 8.8, 9.2),   # 挂单前
                            ("2025-01-06", 9.6, 11.2, 9.4, 11.0),  # 最高 11.2 ≥ 10.5 → 成交
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "到价成交" in text
    rows = st._read_all()
    assert rows[0]["status"] == "open"
    assert rows[0]["date"] == "2025-01-06"      # 成交日 = 触发日
    assert rows[0]["entry_price"] == "10.5"     # 按触发价成交（条件单语义）


def test_pending_not_filled_when_price_never_hits(monkeypatch, tmp_path):
    """未到价不成交（老板核心担忧：实盘未触发模拟线不得假买入）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-02", 9.0, 9.5, 8.8, 9.2),
                            ("2025-01-06", 9.0, 10.4, 8.9, 10.0),  # 最高 10.4 < 10.5
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "挂单中" in text
    assert st._read_all()[0]["status"] == "pending"  # 未成交、未撤销


def test_pending_cancelled_after_expire_days(monkeypatch, tmp_path):
    """挂单后 3 个交易日仍未触发 → 撤销留痕（机会成本真实记录，老板拍板 3 日）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-06", 9.0, 10.4, 8.9, 10.0),  # 第1日
                            ("2025-01-07", 9.0, 10.4, 8.9, 10.0),  # 第2日
                            ("2025-01-08", 9.0, 10.4, 8.9, 10.0),  # 第3日 → 撤销
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "撤销留痕" in text
    r = st._read_all()[0]
    assert r["status"] == "cancelled"
    assert "超期未触发" in r["exit_reason"]


def test_sim_10w_affordable_where_live_5600_rejects(monkeypatch):
    """无资金限制语义：实盘 5600 买不起（每股风险 1.5 → 112//1.5 <100）的票，
    模拟线 10 万按 2% 规则买得起（2000//1.5 = 1300 股）"""
    sim_shares, _ = st.check_affordability(30.0, 1.5, risk_scale=1.0,
                                           capital=st.SIM_CAPITAL)
    live_shares, live_reason = st.check_affordability(30.0, 1.5, risk_scale=1.0)
    assert sim_shares == 1300, sim_shares
    assert live_shares < 100 and "买不起" in live_reason


def test_auto_open_default_picks_main_file_not_variants(monkeypatch, tmp_path):
    """无参调用（自动链路路径）：只认主文件 scan_result_YYYYMMDD_HHMMSS.csv，
    排除同秒实验变体 _broken/_vol/_c23（2026-08-08 冒烟踩坑回归）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(st, "_market_env_scale", lambda: 1.0)
    monkeypatch.setattr("数据基础.配置.stock_pool.get_stock_codes",
                        lambda: {"600001"})
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "数据基础" / "扫描输出"
    out_dir.mkdir(parents=True)
    main_csv = out_dir / "scan_result_20260807_223326.csv"
    pd.DataFrame([{"code": "600001", "name": "主文件票", "评级": "S",
                   "触发价": 10.5, "止损价": 9.8, "每股风险": 0.7}]
                 ).to_csv(main_csv, index=False, encoding="utf-8-sig")
    # 实验变体（同秒生成）：里面放不同的票，若被误取测试将失败
    pd.DataFrame([{"code": "600002", "name": "变体票", "评级": "S",
                   "触发价": 8.2, "止损价": 7.9, "每股风险": 0.3}]
                 ).to_csv(out_dir / "scan_result_20260807_223326_broken.csv",
                          index=False, encoding="utf-8-sig")
    text = st.sim_auto_open()  # 无参：走 glob 选择逻辑
    assert "scan_result_20260807_223326.csv" in text
    rows = st._read_all()
    assert len(rows) == 1 and rows[0]["symbol"] == "600001"  # 主文件票，非变体票


def test_sim_check_with_only_pending_no_open(monkeypatch, tmp_path):
    """仅挂单中（无持仓）时 sim_check 正常返回跟进信息，不崩"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-02", 9.0, 9.5, 8.8, 9.2),
                            ("2025-01-06", 9.0, 10.4, 8.9, 10.0),
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "挂单中" in text
    assert st._read_all()[0]["status"] == "pending"
