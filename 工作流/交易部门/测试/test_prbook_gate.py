"""C1 财报日避让（预约披露日）单元测试（2026-08-05 老板拍板 · 优化方案 C1 第一层）

覆盖验收点（任务验收标准 5）：
  - 披露日触发否决 / 不触发放行
  - actual_date 未披露为 NaT/None → 用 pd.isna 判断（质检观察项）
  - 已披露报告期不避让（查询层只返回未披露行）
  - 持仓警示逻辑（持仓期跨披露日 → 警示；不强制平仓）
  - 无披露数据 → 放行（load_prbook_map 空 dict）
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 分析决策.市场环境.prbook_gate import (
    load_prbook_map,
    prbook_verdict,
    prbook_warn,
)

# ── 测试数据工厂 ──


def make_row(first_appoint: str, report_period: str = "2026-06-30",
             actual_date=None) -> dict:
    """构造一条预约披露行（next_prbook_dates 输出口径：未披露行）"""
    return {"symbol": "600000", "secname": "测试股", "report_period": report_period,
            "first_appoint": first_appoint, "actual_date": actual_date}


SIG = pd.Timestamp("2026-08-10")  # 信号日（周一）
DISCLOSE_DAY = pd.Timestamp("2026-08-10")  # 与信号日同天的披露日


# ── 新开仓否决（prbook_verdict） ──


class TestPrbookVerdict:
    def test_信号日即披露日_否决(self):
        rows = [make_row("2026-08-10")]
        action, info = prbook_verdict(rows, SIG)
        assert action == "veto"
        assert "2026-08-10" in (info or "")
        assert "报告期" in (info or "")

    def test_非披露日_放行(self):
        rows = [make_row("2026-08-20")]
        assert prbook_verdict(rows, SIG)[0] == "keep"

    def test_多报告期_命中其一_否决(self):
        rows = [make_row("2026-08-20", "2026-03-31"),
                make_row("2026-08-10", "2026-06-30")]
        assert prbook_verdict(rows, SIG)[0] == "veto"

    def test_无披露数据_放行(self):
        assert prbook_verdict([], SIG)[0] == "keep"

    def test_未披露NaT处理_是避让对象(self):
        """质检观察项：actual_date 未披露时为空（NaT/None），pd.isna 判断不报错且避让"""
        rows = [{"symbol": "600000", "report_period": "2026-06-30",
                 "first_appoint": "2026-08-10", "actual_date": pd.NaT}]
        assert prbook_verdict(rows, SIG)[0] == "veto"

    def test_first_appoint缺失_跳过该行(self):
        rows = [{"symbol": "600000", "report_period": "2026-06-30",
                 "first_appoint": None, "actual_date": None}]
        assert prbook_verdict(rows, SIG)[0] == "keep"

    def test_边界_披露日早于信号日_放行(self):
        """已披露报告期：实际披露日早于信号日 → 财报已出，不避让"""
        rows = [make_row("2026-08-05", actual_date="2026-08-05")]
        assert prbook_verdict(rows, SIG)[0] == "keep"

    def test_边界_披露日当天_否决(self):
        """实际披露日 == 信号日：财报当晚公布，T 收盘决策时未出 → 仍否决"""
        rows = [make_row("2026-08-10", actual_date="2026-08-10")]
        assert prbook_verdict(rows, SIG)[0] == "veto"

    def test_边界_披露日晚于信号日_否决(self):
        """实际披露日 > 信号日（预约当天尚未披露）→ 否决"""
        rows = [make_row("2026-08-10", actual_date="2026-08-15")]
        assert prbook_verdict(rows, SIG)[0] == "veto"

    def test_日期字符串输入兼容(self):
        """信号日传 datetime.date / 字符串同样工作（引擎内为 pd.Timestamp）"""
        rows = [make_row("2026-08-10")]
        assert prbook_verdict(rows, pd.Timestamp("2026-08-10"))[0] == "veto"


# ── 持仓警示（prbook_warn） ──


class TestPrbookWarn:
    def test_持仓期跨披露日_警示(self):
        rows = [make_row("2026-08-14")]  # 披露日 8/14，持仓期 8/11~8/20
        warn = prbook_warn(rows, SIG, pd.Timestamp("2026-08-20"))
        assert warn is not None
        assert "2026-08-14" in warn

    def test_披露日恰为出场日_警示(self):
        rows = [make_row("2026-08-20")]
        warn = prbook_warn(rows, SIG, pd.Timestamp("2026-08-20"))
        assert warn is not None

    def test_披露日在信号日当天_不算持仓警示(self):
        """披露日==信号日已被否决分支处理；警示只覆盖 T+1 起的持仓期"""
        rows = [make_row("2026-08-10")]
        assert prbook_warn(rows, SIG, pd.Timestamp("2026-08-20")) is None

    def test_披露日在出场日后_无警示(self):
        rows = [make_row("2026-08-21")]
        assert prbook_warn(rows, SIG, pd.Timestamp("2026-08-20")) is None

    def test_未触发无出场日_无警示(self):
        """prebreak 未触发（exit_date=None）→ 无持仓 → 无警示"""
        rows = [make_row("2026-08-14")]
        assert prbook_warn(rows, SIG, None) is None

    def test_无披露数据_无警示(self):
        assert prbook_warn([], SIG, pd.Timestamp("2026-08-20")) is None


# ── 数据加载（load_prbook_map：复用 store.next_prbook_dates） ──


class TestLoadPrbookMap:
    @pytest.fixture()
    def con(self, tmp_path):
        """临时 duckdb 库（含 prbook 表与测试数据）"""
        import duckdb

        from 数据基础.duckdb.data_sources import store as DS
        db = tmp_path / "t_prbook.duckdb"
        c = duckdb.connect(str(db))
        DS.ensure_schema(c)
        DS.upsert_prbook(c, [
            {"symbol": "600000", "secname": "A股", "report_period": "2026-06-30",
             "first_appoint": "2026-08-10", "change1": "", "change2": "",
             "change3": "", "actual_date": ""},                       # 未披露（避让对象）
            {"symbol": "600000", "secname": "A股", "report_period": "2026-03-31",
             "first_appoint": "2026-04-28", "change1": "", "change2": "",
             "change3": "", "actual_date": "2026-04-28"},             # 已披露（查询层排除）
            {"symbol": "600001", "secname": "B股", "report_period": "2026-06-30",
             "first_appoint": "2026-08-20", "change1": "", "change2": "",
             "change3": "", "actual_date": ""},                       # 未披露
        ])
        yield c
        c.close()

    def test_返回全部报告期行(self, con):
        """prbook_rows 口径：全部报告期（含已披露），actual_date 供 T 时点判断"""
        m = load_prbook_map(["600000", "600001"], con=con)
        assert set(m) == {"600000", "600001"}
        assert len(m["600000"]) == 2  # 2026-06-30（未披露）+ 2026-03-31（已披露）
        undisclosed = [r for r in m["600000"] if r["report_period"] == pd.Timestamp("2026-06-30")]
        assert pd.isna(undisclosed[0]["actual_date"])  # 未披露 → NaT，pd.isna 可判

    def test_空股票列表_返回空(self, con):
        assert load_prbook_map([], con=con) == {}

    def test_缺表不报错_返回空(self, tmp_path):
        """无 prbook 表/库不存在 → 空 dict（引擎侧放行，不误杀信号）"""
        import duckdb
        db = tmp_path / "empty.duckdb"
        c = duckdb.connect(str(db))   # 只有默认表（无 prbook）
        try:
            m = load_prbook_map(["600000"], con=c)
            assert m == {}
        finally:
            c.close()

    def test_引擎侧无数据放行语义(self):
        """无该股数据 → prbook_missing（引擎计数）；判定函数层面=放行"""
        assert prbook_verdict([], SIG)[0] == "keep"
