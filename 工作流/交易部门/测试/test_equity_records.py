"""净值记录 + 双线对照单测——2026-08-07 老板拍板"从今天起记录曲线"· T-030 落地"""
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from 分析决策.跟踪 import equity_records as er
from 分析决策.跟踪 import dual_line


@pytest.fixture()
def tmp_csv(tmp_path, monkeypatch):
    """隔离账本：测试用临时目录"""
    monkeypatch.setattr(er, "EQUITY_CSV", tmp_path / "equity_records.csv")
    monkeypatch.setattr(er, "_JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(dual_line, "_JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(er, "OUT_DIR", tmp_path)
    monkeypatch.setattr(dual_line, "OUT_DIR", tmp_path)
    return tmp_path


def test_add_record_and_report(tmp_csv):
    """净值登记 → 报告修正收益率（无注入 = 0%）"""
    er.add_record("2026-08-07", 5600.0)
    cr = er.corrected_return()
    assert cr["initial"] == 5600.0
    assert cr["last_equity"] == 5600.0
    assert cr["corrected_ret"] == 0.0
    assert "修正收益率" in er.render_report()


def test_inject_and_corrected_return(tmp_csv):
    """注入 3000 → 修正收益率剔除注入"""
    er.add_record("2026-08-07", 5600.0)
    er.add_inject("2026-08-10", 3000.0)
    er.add_record("2026-08-10", 8600.0)          # 注入后净值 = 5600+3000
    cr = er.corrected_return()
    assert cr["total_inject"] == 3000.0
    assert cr["corrected_ret"] == 0.0            # 剔除注入 = 没赚
    assert cr["uncorrected_ret"] == pytest.approx(8600 / 5600 - 1, abs=1e-9)


def test_corrected_return_real_gain(tmp_csv):
    """真实盈利：净值 9000（注入 3000）→ 修正 +600/5600 = +10.7%"""
    er.add_record("2026-08-07", 5600.0)
    er.add_inject("2026-08-10", 3000.0)
    er.add_record("2026-08-10", 9000.0)
    cr = er.corrected_return()
    assert cr["corrected_ret"] == pytest.approx((9000 - 3000) / 5600 - 1, abs=1e-9)


def test_same_day_overwrite(tmp_csv):
    """同日重复登记 → 覆盖不叠加"""
    er.add_record("2026-08-07", 5600.0)
    er.add_record("2026-08-07", 5700.0)
    rows = er.get_records()
    assert len(rows) == 1
    assert float(rows[0]["equity"]) == 5700.0


def test_dual_line_empty(tmp_csv):
    """双线对照：两线零记录 → 不崩溃"""
    c = dual_line.compare()
    assert c["n_live"] == 0 and c["n_sim"] == 0
    assert "双线对照" in dual_line.render_report(c)


def test_dual_line_stats(tmp_csv):
    """双线对照：有记录 → 统计正确（实盘 2 笔 vs 模拟 3 笔）"""
    (tmp_csv / "trade_journal.csv").write_text(
        "trade_id,date,symbol,name,direction,entry_price,exit_price,volume,"
        "stop_loss,r_multiple,pnl,grade_at_entry,exit_reason\n"
        "1,2026-08-07,600001,A,long,10,11,100,9,1.0,100,S,20d\n"
        "2,2026-08-08,600002,B,long,10,9,100,9,-1.0,-100,S,20d\n",
        encoding="utf-8-sig")
    (tmp_csv / "sim_journal.csv").write_text(
        "trade_id,date,symbol,name,direction,market,entry_price,stop_loss,volume,"
        "grade_at_entry,ty_high,ty_low,status,exit_price,exit_date,exit_reason,"
        "r_multiple,pnl,env_scale,phase\n"
        "1,2026-08-07,600001,A,long,stock,10,9,100,S,11,9,closed,11,2026-08-10,"
        "20d,1.0,100,1.0,\n"
        "2,2026-08-08,600002,B,long,stock,10,9,100,S,11,9,closed,9,2026-08-11,"
        "20d,-1.0,-100,1.0,\n"
        "3,2026-08-09,600003,C,long,stock,10,9,100,S,11,9,closed,12,2026-08-12,"
        "20d,2.0,200,1.0,\n",
        encoding="utf-8-sig")
    c = dual_line.compare()
    assert c["n_live"] == 2
    assert c["n_sim"] == 3
    assert c["live"]["avg_r"] == 0.0
    assert c["sim"]["avg_r"] == pytest.approx(2 / 3, abs=1e-9)
    assert c["sim"]["max_streak"] == 1
    assert "执行一致性" in dual_line.render_report(c)
