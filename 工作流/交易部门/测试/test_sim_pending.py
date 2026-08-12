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
    """days = [(日期, 开盘, 最高, 最低, 收盘[, 成交量]), ...]（R-053 支持自定义量）"""
    return pd.DataFrame({
        "日期": [d[0] for d in days],
        "开盘": [d[1] for d in days],
        "最高": [d[2] for d in days],
        "最低": [d[3] for d in days],
        "收盘": [d[4] for d in days],
        "成交量": [d[5] if len(d) > 5 else 1_000_000 for d in days],
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
    """扫描候选 → 全部生成 pending 条件单（10 万名义资金 0.025 比例（R-050））"""
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
    # 10 万 × 0.025 × 1R = 2500 元 → 每股风险 0.7 → 2500//0.7//100*100 = 3500 股（R-050）
    assert int(rows[0]["volume"]) == 3500
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
    """未到价不成交（老板核心担忧：实盘未触发模拟线不得假买入）——挂单当日 K 线
    （fill_days=0 未超 R-065 隔天有效期）→ 保持挂单中"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-02", 9.0, 9.5, 8.8, 9.2),
                            ("2025-01-03", 9.0, 10.4, 8.9, 10.0),  # 挂单日：最高 10.4 < 10.5
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "挂单中" in text
    assert st._read_all()[0]["status"] == "pending"  # 未成交、未撤销


def test_pending_cancelled_after_expire_days(monkeypatch, tmp_path):
    """挂单后 1 个交易日仍未触发 → 撤销留痕（R-065 老板拍板：隔天失效 + 每日重挂 =
    每日校准触发价；次日 sim_auto_open 仍在候选则自动重挂）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-02", 9.0, 9.5, 8.8, 9.2),   # 挂单前
                            ("2025-01-06", 9.0, 10.4, 8.9, 10.0),  # 第1日（挂单后）→ 撤销
                            ("2025-01-07", 9.0, 10.4, 8.9, 10.0),  # 数据完整性
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "撤销留痕" in text
    r = st._read_all()[0]
    assert r["status"] == "cancelled"
    assert "超期未触发" in r["exit_reason"]


def test_sim_10w_affordable_where_live_5600_rejects(monkeypatch):
    """无资金限制语义：实盘 5600 买不起（每股风险 1.5 → 112//1.5 <100）的票，
    模拟线 10 万按 0.025 规则买得起（2500//1.5 = 1600 股）（R-050）"""
    monkeypatch.setattr(st, "get_capital", lambda: 5600)  # 实盘对照：5600×0.025=140 → 140//1.5=93 <100
    sim_shares, _ = st.check_affordability(30.0, 1.5, risk_scale=1.0,
                                           capital=st.SIM_CAPITAL)
    live_shares, live_reason = st.check_affordability(30.0, 1.5, risk_scale=1.0)
    assert sim_shares == 1600, sim_shares
    assert live_shares < 100 and "买不起" in live_reason


def test_auto_open_default_picks_main_file_not_variants(monkeypatch, tmp_path):
    """无参调用（自动链路路径）：只认主文件 scan_result_YYYYMMDD_HHMMSS.csv，
    排除同秒实验变体 _broken/_vol/_c23（2026-08-08 冒烟踩坑回归）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(st, "_market_env_scale", lambda: 1.0)
    monkeypatch.setattr("数据基础.配置.stock_pool.get_stock_codes",
                        lambda: {"600001"})
    monkeypatch.chdir(tmp_path)
    # R-073 修正（2026-08-13）：批次须用"今日"日期——数据新鲜度检查
    # （批次 < 今日 → 过时跳过）使写死日期的夹具跨天后失效
    import datetime as _dt
    _today = _dt.datetime.now().strftime("%Y%m%d")
    out_dir = tmp_path / "数据基础" / "扫描输出"
    out_dir.mkdir(parents=True)
    main_csv = out_dir / f"scan_result_{_today}_223326.csv"
    pd.DataFrame([{"code": "600001", "name": "主文件票", "评级": "S",
                   "触发价": 10.5, "止损价": 9.8, "每股风险": 0.7}]
                 ).to_csv(main_csv, index=False, encoding="utf-8-sig")
    # 实验变体（同秒生成）：里面放不同的票，若被误取测试将失败
    pd.DataFrame([{"code": "600002", "name": "变体票", "评级": "S",
                   "触发价": 8.2, "止损价": 7.9, "每股风险": 0.3}]
                 ).to_csv(out_dir / f"scan_result_{_today}_223326_broken.csv",
                          index=False, encoding="utf-8-sig")
    text = st.sim_auto_open()  # 无参：走 glob 选择逻辑
    assert f"scan_result_{_today}_223326.csv" in text
    rows = st._read_all()
    assert len(rows) == 1 and rows[0]["symbol"] == "600001"  # 主文件票，非变体票


def test_sim_check_with_only_pending_no_open(monkeypatch, tmp_path):
    """仅挂单中（无持仓）时 sim_check 正常返回跟进信息，不崩（挂单当日 K 线，
    fill_days=0 未超 R-065 隔天有效期）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr("数据基础.数据.fetcher.get_daily_kline",
                        lambda code, use_cache=True: mk_kline([
                            ("2025-01-02", 9.0, 9.5, 8.8, 9.2),
                            ("2025-01-03", 9.0, 10.4, 8.9, 10.0),
                        ]))
    st._write_all([pend_row(created="2025-01-03")])
    text = st.sim_check()
    assert "挂单中" in text
    assert st._read_all()[0]["status"] == "pending"


# ============ R-053 突破质量双条件（2026-08-11 老板拍板 · 交易部审核后 v2）============
# A 收盘站稳：触发日收盘 ≥ 触发价；B 放量：触发日量比 > 1.5（前20日均量分母）
# 恒绑定触发日 entry_idx，不随 delay2 二判漂移（审核 A-2）


def _mk_row(trigger: float = 10.0, stop: float = 9.5, created: str = "2025-01-21") -> dict:
    """持仓行（phase=half/open），date = 触发日"""
    r = pend_row(code="600419", trigger=trigger, stop=stop, created=created)
    r["status"] = "open"
    r["phase"] = "half"
    return r


def _mk_series(vol_base: float, trig_vol: float, trig_close: float,
               conf_close: float, t2_close: float | None = None,
               trig_open: float = 10.0, trig_high: float = 10.5) -> pd.DataFrame:
    """构造 R-053 测试 K 线：前 20 根均量 vol_base → 触发日（第21根）→ 确认日（第22根）→ 可选 T+2"""
    days = [(f"2025-01-{i:02d}", 9.8, 9.9, 9.7, 9.85, vol_base) for i in range(1, 21)]
    days.append(("2025-01-21", trig_open, trig_high, trig_open - 0.2, trig_close, trig_vol))
    days.append(("2025-01-22", conf_close - 0.1, conf_close + 0.2, conf_close - 0.2, conf_close, 1_000_000))
    if t2_close is not None:
        days.append(("2025-01-23", t2_close - 0.1, t2_close + 0.2, t2_close - 0.2, t2_close, 1_000_000))
    return mk_kline(days)


class TestR053BreakQuality:
    """_check_half_position 突破质量双条件（A 收盘站稳 + B 放量）"""

    def test_quality_ok_confirms_add(self):
        """A/B 双达标（600833 型：开仓日收 11.01≥10.18、量比 2.75）→ 正常确认补仓"""
        # 触发日收 10.4 ≥ 触发价 10.0 ✓；量比 250万/100万 = 2.5 >1.5 ✓；确认日收 10.6 三条件过
        df = _mk_series(vol_base=1_000_000, trig_vol=2_500_000, trig_close=10.4, conf_close=10.6)
        step = st._check_half_position(df, _mk_row())
        assert step["action"] == "add", step
        assert step["open_close_ok"] is True and step["vol_ok"] is True

    def test_shrink_volume_rejects(self):
        """缩量突破（600315 型：量比 1.2 ≤1.5）→ 三条件过也平仓"""
        df = _mk_series(vol_base=1_000_000, trig_vol=1_200_000, trig_close=10.4, conf_close=10.6)
        step = st._check_half_position(df, _mk_row())
        assert step["action"] == "exit_reject", step
        assert "缩量突破" in step["reason"]
        assert step["vol_ok"] is False

    def test_close_below_trigger_rejects(self):
        """触发日收盘 < 触发价（盘中假突破，600315 型 A 不达标）→ 平仓"""
        # 触发日盘中最高 10.5 ≥ 10.0（触发）但收盘 9.9 < 10.0；量比 2.5 达标 → A 拦
        df = _mk_series(vol_base=1_000_000, trig_vol=2_500_000, trig_close=9.9, conf_close=10.6)
        step = st._check_half_position(df, _mk_row())
        assert step["action"] == "exit_reject", step
        assert "开仓日收盘" in step["reason"]
        assert step["open_close_ok"] is False

    def test_vol_ratio_boundary_exact_15(self):
        """量比恰 = 1.5 → 不达标（严格 >1.5，tracking.py:318 同语义）"""
        df = _mk_series(vol_base=1_000_000, trig_vol=1_500_000, trig_close=10.4, conf_close=10.6)
        step = st._check_half_position(df, _mk_row())
        assert step["action"] == "exit_reject", step
        assert step["vol_ok"] is False

    def test_zero_ref_volume_not_ok(self):
        """前 20 日均量为 0（数据不足）→ 量比 0 不达标（tracking.py:316 同语义）"""
        df = _mk_series(vol_base=0, trig_vol=1_000_000, trig_close=10.4, conf_close=10.6)
        step = st._check_half_position(df, _mk_row())
        assert step["action"] == "exit_reject", step
        assert step["vol_ratio"] == 0.0 and step["vol_ok"] is False

    def test_delay2_uses_trigger_day_not_slice(self):
        """603970 型：首判 c1 失败（确认日收 10.48 < 10.58）→ 二判 confirm 时 A/B 仍用
        触发日数据（收 10.4≥10.0 ✓ 量比 1.87 ✓）→ 正常补仓，不被二判日漂移误杀（审核 A-2）"""
        # 触发日收 10.4 ≥ 10.0 ✓、量比 187万/100万 = 1.87 ✓
        # 确认日收 9.8 < 10.0 → c1 失败 → 首判 reject → T+2 收 10.7 ≥ 10.0 → 二判 confirm
        df = _mk_series(vol_base=1_000_000, trig_vol=1_870_000, trig_close=10.4,
                        conf_close=9.8, t2_close=10.7)
        step = st._check_half_position(df, _mk_row())
        assert step["action"] == "add", step
        assert step["open_close_ok"] is True and step["vol_ok"] is True


def test_auto_open_rehangs_cancelled_while_still_candidate(monkeypatch, tmp_path):
    """R-065：撤销后的票仍在扫描候选 → sim_auto_open 重新挂单（每日校准/持续埋伏）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(st, "_market_env_scale", lambda: 1.0)
    monkeypatch.setattr("数据基础.配置.stock_pool.get_stock_codes",
                        lambda: {"600001"})
    csv_path = write_scan_csv(tmp_path, [
        {"code": "600001", "name": "甲", "评级": "S", "触发价": 10.5,
         "止损价": 9.8, "每股风险": 0.7, "TY高": 11.0, "TY低": 9.5},
    ])
    # 前一日撤销留痕（R-065：隔天失效）——票仍在候选 → 今日应重挂
    rows = st._read_all()
    rows.append({**pend_row(code="600001", trigger=10.5, stop=9.8),
                 "status": "cancelled",
                 "exit_reason": "模拟条件单超期未触发（1 个交易日）"})
    st._write_all(rows)
    text = st.sim_auto_open(csv_path=csv_path)
    assert "新建 1 笔条件单" in text or "新建 1 笔" in text
    r = st._read_all()
    assert any(x["status"] == "pending" and x["symbol"] == "600001" for x in r)


def test_auto_open_skips_stale_batch(monkeypatch, tmp_path):
    """R-066：自动链路读到旧批次主文件（日期 < 当日）→ 跳过挂单 + 明确告警
    （08-11 主文件缺失后曾静默用 08-10 旧批次挂旧触发价单）"""
    use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "数据基础" / "扫描输出"
    out_dir.mkdir(parents=True)
    pd.DataFrame([{"code": "600001", "name": "旧批次票", "评级": "S",
                   "触发价": 10.5, "止损价": 9.8, "每股风险": 0.7}]
                 ).to_csv(out_dir / "scan_result_20260810_190829.csv",
                          index=False, encoding="utf-8-sig")
    text = st.sim_auto_open()  # 无参自动链路 → 应被新鲜度校验拦截
    assert "扫描主文件缺失或过时" in text
    assert "跳过模拟挂单" in text
    assert st._read_all() == []  # 未挂任何单
