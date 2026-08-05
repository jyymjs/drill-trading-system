"""除权完整性校验单测（T-017 P3 · P2 遗留问题 1 兜底）

核心场景：300093 金刚光伏漏记除权（2025-11-20 通达信 xdxr 仅有 category 9/15、
无 category=1；2025-11-21 价格跳变 -8.37% 无任何记录）→ 规则 A 必须检测告警。

规则 A：送转上市类记录（9/15）缺 category=1 → 疑似漏记（300093 精确命中）
规则 B：跳变超板块涨跌停上限（主板 >10.5%、创业/科创 >20.5%）且无任何 xdxr 记录
        → 疑似漏记（10 送 10 级大比例除权）；普通涨停板（10%/20%）不命中（降噪）
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 数据基础.duckdb.xdxr_check import _limit_pct, check_symbol


def make_daily(dates, opens, closes):
    """构造 daily DataFrame（列 date/open/close）"""
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": opens,
        "close": closes,
    })


def make_xdxr(dates, cats):
    """构造 xdxr DataFrame（列 date/category）"""
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "category": cats,
    })


# ───────────────────────── 规则 A：送转上市缺除权除息 ─────────────────────────

class TestRuleA:
    """规则 A：当日有转配股上市类记录（9/15）但无 category=1 → 疑似漏记除权"""

    def test_300093_missing_xdxr_detected(self):
        """300093 个案：2025-11-20 有 category 9+15、无 category=1 → 必须告警"""
        daily = make_daily(
            ["2025-11-17", "2025-11-18", "2025-11-20", "2025-11-21", "2025-11-24"],
            [14.20, 14.40, 14.97, 14.61, 14.20],
            [14.59, 14.83, 15.42, 14.13, 14.52],
        )
        xdxr = make_xdxr(
            ["2025-11-20", "2025-11-20", "2025-12-31"],
            [9, 15, 5],
        )
        hits = check_symbol(daily, xdxr, symbol="300093")
        a_hits = [h for h in hits if h["rule"] == "A"]
        assert len(a_hits) == 1, f"应命中 1 条规则A，实际 {a_hits}"
        assert a_hits[0]["date"] == "2025-11-20"
        assert set(a_hits[0]["xdxr_cats"]) == {9, 15}
        # 300093 的 -8.37% 跳变低于主板上限 → 规则 B 不报（规则 A 已覆盖）
        assert not [h for h in hits if h["rule"] == "B"]

    def test_normal_xdxr_not_flagged(self):
        """正常除权：同日有 category=1（除权除息）→ 规则 A 不告警"""
        daily = make_daily(
            ["2025-06-02", "2025-06-03", "2025-06-04"],
            [10.0, 9.0, 9.2],
            [10.2, 9.1, 9.3],
        )
        xdxr = make_xdxr(["2025-06-03"], [1])   # 除权除息日
        hits = [h for h in check_symbol(daily, xdxr, symbol="000001") if h["rule"] == "A"]
        assert hits == []

    def test_share_change_snapshot_not_flagged(self):
        """category=5（股本变化定期快照，10 万+ 条）→ 规则 A 不告警（排除假阳性）"""
        daily = make_daily(
            ["2024-06-28", "2024-07-01", "2024-07-02"],
            [10.0, 10.1, 10.0],
            [10.2, 10.3, 10.1],
        )
        xdxr = make_xdxr(["2024-06-30"], [5])
        hits = [h for h in check_symbol(daily, xdxr, symbol="000001") if h["rule"] == "A"]
        assert hits == []

    def test_no_xdxr_records_rule_a_silent(self):
        """完全无 xdxr 记录（新股）→ 规则 A 无输出"""
        daily = make_daily(
            ["2026-01-05", "2026-01-06", "2026-01-07"],
            [20.0, 20.5, 20.3],
            [20.4, 20.8, 20.1],
        )
        hits = [h for h in check_symbol(daily, None, symbol="301655") if h["rule"] == "A"]
        assert hits == []


# ───────────────────────── 规则 B：跳变超涨跌停上限无记录 ─────────────────────────

class TestRuleB:
    """规则 B：跳变 > 板块涨跌停上限 且当日无任何 xdxr 记录 → 疑似漏记"""

    def test_big_xdxr_jump_detected(self):
        """10 送 10（跳变 -50%）且无任何记录 → 规则 B 命中"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02"],
            [10.0, 5.0],
            [10.0, 5.1],        # -49% 跳变
        )
        hits = [h for h in check_symbol(daily, None, symbol="600001") if h["rule"] == "B"]
        assert len(hits) == 1
        assert hits[0]["close_gap_pct"] == pytest.approx(-49.0, abs=0.1)

    def test_limit_up_not_flagged(self):
        """主板涨停（+10%）无记录 → 规则 B 不命中（降噪：正常交易行为）"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02", "2026-07-03"],
            [10.0, 11.0, 11.5],
            [10.0, 11.0, 11.4],
        )
        hits = [h for h in check_symbol(daily, None, symbol="600001") if h["rule"] == "B"]
        assert hits == []

    def test_chinext_limit_up_not_flagged(self):
        """创业板/科创板涨停（+20%）无记录 → 规则 B 不命中"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02"],
            [10.0, 12.0],
            [10.0, 12.0],       # +20% 涨停
        )
        assert check_symbol(daily, None, symbol="300001") == []
        assert check_symbol(daily, None, symbol="688001") == []

    def test_chinext_over_limit_detected(self):
        """创业板跳变 +25%（超过 20% 涨停上限）→ 规则 B 命中"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02"],
            [10.0, 12.5],
            [10.0, 12.5],       # +25%
        )
        hits = [h for h in check_symbol(daily, None, symbol="300001") if h["rule"] == "B"]
        assert len(hits) == 1

    def test_small_move_not_flagged(self):
        """小幅波动（<5%）→ 规则 B 不告警"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02", "2026-07-03"],
            [10.0, 10.2, 10.1],
            [10.0, 10.3, 10.2],
        )
        assert check_symbol(daily, None, symbol="600001") == []

    def test_threshold_tightens_limit(self):
        """threshold 可调严：判定线 = max(threshold, 板块上限)"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02"],
            [10.0, 7.5],
            [10.0, 7.6],        # -24%
        )
        # 主板默认：max(0.05, 0.105)=0.105 → -24% 命中
        assert len([h for h in check_symbol(daily, None, symbol="600001") if h["rule"] == "B"]) == 1
        # threshold=0.30 → max(0.30, 0.105)=0.30 → -24% 不命中
        assert check_symbol(daily, None, symbol="600001", threshold=0.30) == []


# ───────────────────────── 综合 ─────────────────────────

class TestCombined:
    def test_open_gap_jump_also_flagged(self):
        """开盘跳变超上限（除权后大幅高开）→ 规则 B 命中"""
        daily = make_daily(
            ["2026-07-01", "2026-07-02"],
            [10.0, 15.0],        # open 跳变 +50%
            [10.0, 14.8],
        )
        hits = [h for h in check_symbol(daily, None, symbol="600001") if h["rule"] == "B"]
        assert len(hits) == 1
        assert hits[0]["open_gap_pct"] == pytest.approx(50.0, abs=0.1)

    def test_empty_input_safe(self):
        """空输入不报错"""
        assert check_symbol(pd.DataFrame(), None) == []

    def test_limit_pct_by_board(self):
        """板块涨跌停上限判定（规则 B 基础）"""
        assert _limit_pct("600001") == pytest.approx(0.105)
        assert _limit_pct("300001") == pytest.approx(0.205)
        assert _limit_pct("301001") == pytest.approx(0.205)
        assert _limit_pct("688001") == pytest.approx(0.205)
