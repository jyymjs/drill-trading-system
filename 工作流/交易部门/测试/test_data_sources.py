"""data_sources 单测（T-017 P4 · 公告/预约披露/快讯）

覆盖（全部 mock 网络，不打真实接口）：
- cninfo：orgId 映射/回退、公告行解析（时间戳/PDF 链接/类型码）、预约披露解析、
  报告期推算
- cls：本地签名快照（防算法回归）、快讯行解析
- store：三表建表、PK 幂等 upsert、财报日避让查询
"""
import os
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 数据基础.duckdb.data_sources import cninfo as CI
from 数据基础.duckdb.data_sources import cls as CLS
from 数据基础.duckdb.data_sources import store as DS

# 巨潮公告接口样例响应（2026-08-05 实测字段结构）
ANN_ITEM = {
    "secCode": "600519", "secName": "贵州茅台", "orgId": "gssh0600519",
    "announcementId": "12345", "announcementTitle": "贵州茅台重大事项公告",
    "announcementTime": 1784304000000,  # 2026-07-18
    "announcementTypeName": None,        # 实测恒为 null
    "announcementType": "01010503||010113||012399",
    "adjunctUrl": "finalpage/2026-07-18/1225431263.PDF",
    "adjunctSize": 123456.0,
}
ANN_RESP = {"totalAnnouncement": 100, "announcements": [ANN_ITEM]}

# 巨潮预约披露接口样例响应（2026-08-05 实测字段结构）
PRBOOK_ITEM = {
    "seccode": "000651", "secname": "格力电器", "orgId": "gssz0000651",
    "f001d_0102": "2026-06-30", "f002d_0102": "2026-08-27",
    "f003d_0102": "", "f004d_0102": "", "f005d_0102": "", "f006d_0102": "",
}
PRBOOK_RESP = {"totalRows": 5540, "totalPages": 2, "prbookinfos": [PRBOOK_ITEM]}

# 财联社快讯样例响应（2026-08-05 实测字段结构）
CLS_RESP = {"errno": 0, "data": {"roll_data": [
    {"ctime": 1785927036, "title": "外交部：敦促日方深刻反省历史罪责",
     "content": "外交部发言人表示……", "brief": "外交部：敦促日方深刻反省"},
]}}
CLS_RESP_ERR = {"errno": 1, "msg": "sign error", "data": None}


# ───────────────────────── cninfo ─────────────────────────

def test_orgid_from_map(monkeypatch):
    """orgId 动态映射：映射表命中 → 返回真实 orgId"""
    class FakeResp:
        def json(self):
            return {"stockList": [{"code": "601318", "orgId": "9900002221"}]}
    monkeypatch.setattr(CI.requests, "get", lambda *a, **k: FakeResp())
    CI._CNINFO_ORGID_MAP.clear()
    assert CI._cninfo_orgid("601318") == "9900002221"


def test_orgid_fallback(monkeypatch):
    """orgId 映射表拉取失败 → 回退硬编码规则（6→gssh、0/3→gssz、8/4→gsbj）"""
    def boom(*a, **k):
        raise ConnectionError("network down")
    monkeypatch.setattr(CI.requests, "get", boom)
    CI._CNINFO_ORGID_MAP.clear()
    assert CI._cninfo_orgid("600519") == "gssh0600519"
    assert CI._cninfo_orgid("000651") == "gssz0000651"
    assert CI._cninfo_orgid("830001") == "gsbj0830001"


def test_announcements_parse(monkeypatch):
    """公告行解析：时间戳→日期、PDF 相对链接补全、类型存原始码链"""
    class FakeResp:
        def json(self):
            return ANN_RESP
        def raise_for_status(self):
            pass
    monkeypatch.setattr(CI.requests, "post", lambda *a, **k: FakeResp())
    CI._CNINFO_ORGID_MAP.clear()
    rows = CI.fetch_announcements("600519")
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "600519"
    assert r["date"] == "2026-07-18"
    assert r["title"] == "贵州茅台重大事项公告"
    assert r["ann_type"] == "01010503||010113||012399"  # 类型名缺失 → 原始码链
    assert r["adjunct_url"].startswith("http://static.cninfo.com.cn/")
    assert "annoId=12345" in r["url"]
    assert r["adj_size"] == 123456.0


def test_default_section_time():
    """报告期推算：8 月中报 / 4 月一季报 / 2 月去年年报"""
    assert CI.default_section_time(datetime(2026, 8, 5).date()) == "2026-06-30"
    assert CI.default_section_time(datetime(2026, 4, 15).date()) == "2026-03-31"
    assert CI.default_section_time(datetime(2026, 2, 10).date()) == "2025-12-31"
    assert CI.default_section_time(datetime(2026, 11, 1).date()) == "2026-09-30"


def test_prbook_parse_paginated(monkeypatch):
    """预约披露：全市场分页（totalPages=2）→ 按页翻完并解析字段映射"""
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            # 第 1 页有数据；第 2 页空列表模拟翻完
            if not FakeResp.called:
                FakeResp.called = True
                return PRBOOK_RESP
            return {"totalRows": 5540, "totalPages": 2, "prbookinfos": []}
    FakeResp.called = False
    monkeypatch.setattr(CI.requests, "post", lambda *a, **k: FakeResp())
    rows = CI.fetch_prbook(section_time="2026-06-30")
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "000651" and r["secname"] == "格力电器"
    assert r["report_period"] == "2026-06-30"
    assert r["first_appoint"] == "2026-08-27"   # 首次预约 = 财报日避让核心
    assert r["change1"] == "" and r["actual_date"] == ""


def test_prbook_parse_single(monkeypatch):
    """预约披露：单股查询（stockCode 参数透传）"""
    captured = {}
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return {"totalRows": 1, "totalPages": 1, "prbookinfos": [PRBOOK_ITEM]}
    def fake_post(url, data=None, **k):
        captured["stockCode"] = data.get("stockCode")
        return FakeResp()
    monkeypatch.setattr(CI.requests, "post", fake_post)
    rows = CI.fetch_prbook(symbol="000651", section_time="2026-06-30")
    assert len(rows) == 1 and captured["stockCode"] == "000651"


# ───────────────────────── cls ─────────────────────────

def test_cls_sign_snapshot():
    """财联社本地签名：固定输入 → 固定输出（防签名算法回归，接口强校验）"""
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": "50"}
    assert CLS._cls_sign(params) == "b849fe86598f3ceca205eda7b33a49a1"


def test_cls_parse(monkeypatch):
    """快讯行解析：ctime 秒 → 'YYYY-MM-DD HH:MM:SS'，title/content 兜底"""
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return CLS_RESP
    monkeypatch.setattr(CLS.requests, "get", lambda *a, **k: FakeResp())
    rows = CLS.fetch_telegraph()
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == "2026-08-05 18:50:36"
    assert r["title"].startswith("外交部")
    assert r["source"] == "cls.cn"


def test_cls_error_raise(monkeypatch):
    """财联社 errno != 0 → 抛错（如实暴露接口异常，不静默）"""
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return CLS_RESP_ERR
    monkeypatch.setattr(CLS.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError):
        CLS.fetch_telegraph()


# ───────────────────────── store ─────────────────────────

@pytest.fixture()
def con(tmp_path):
    """临时库连接（每测独立）"""
    c = DS.open_db(tmp_path / "test_ds.duckdb")
    yield c
    c.close()


def test_schema_created(con):
    """建表：announcements/prbook/news_flash 三表均有 PK"""
    got = set(r[0] for r in con.execute(
        "SELECT DISTINCT table_name FROM duckdb_constraints() "
        "WHERE constraint_type='PRIMARY KEY'").fetchall())
    assert {"announcements", "prbook", "news_flash"} <= got


def test_upsert_announcements_idempotent(con):
    """公告 upsert 幂等：同 (symbol,date,title) 重复插入 → 行数不变"""
    rows = [{"symbol": "600519", "date": "2026-07-18", "title": "贵州茅台重大事项公告",
             "ann_type": "01010503", "url": "http://x/1", "adjunct_url": "http://x/a.pdf",
             "adj_size": 1.0, "org_id": "gssh0600519"}]
    assert DS.upsert_announcements(con, rows) == 1
    rows[0]["url"] = "http://x/2"          # 同 PK 不同内容 → 覆盖
    assert DS.upsert_announcements(con, rows) == 1
    assert con.execute("SELECT count(*) FROM announcements").fetchone()[0] == 1


def test_upsert_prbook_idempotent(con):
    """预约披露 upsert 幂等：同 (symbol,report_period) 覆盖更新"""
    r1 = {"symbol": "000651", "secname": "格力电器", "report_period": "2026-06-30",
          "first_appoint": "2026-08-27", "change1": "", "change2": "",
          "change3": "", "actual_date": ""}
    assert DS.upsert_prbook(con, [r1]) == 1
    r1["first_appoint"] = "2026-08-29"     # 预约日期变更 → 覆盖
    assert DS.upsert_prbook(con, [r1]) == 1
    assert con.execute("SELECT count(*) FROM prbook").fetchone()[0] == 1
    assert con.execute("SELECT first_appoint FROM prbook").fetchone()[0] == datetime(2026, 8, 29).date()


def test_upsert_news_idempotent(con):
    """快讯 upsert：同 (ts,title,source) 幂等；level 缺省补 'normal'"""
    r = {"ts": "2026-08-05 18:50:36", "title": "外交部：敦促日方",
         "content": "…", "source": "cls.cn"}
    assert DS.upsert_news(con, [r]) == 1
    assert DS.upsert_news(con, [r]) == 1
    assert con.execute("SELECT count(*) FROM news_flash").fetchone()[0] == 1
    assert con.execute("SELECT level FROM news_flash").fetchone()[0] == "normal"


def test_next_prbook_dates(con):
    """财报日避让查询：只返回未披露（actual_date 为空）的预约披露，按日期升序"""
    rows = [
        {"symbol": "000651", "secname": "格力电器", "report_period": "2026-06-30",
         "first_appoint": "2026-08-27", "change1": "", "change2": "",
         "change3": "", "actual_date": ""},
        {"symbol": "600519", "secname": "贵州茅台", "report_period": "2026-06-30",
         "first_appoint": "2026-08-15", "change1": "", "change2": "",
         "change3": "", "actual_date": ""},
        {"symbol": "000001", "secname": "平安银行", "report_period": "2026-06-30",
         "first_appoint": "2026-08-15", "change1": "", "change2": "",
         "change3": "", "actual_date": "2026-08-15"},  # 已披露 → 排除
    ]
    DS.upsert_prbook(con, rows)
    out = DS.next_prbook_dates(con, ["000651", "600519", "000001"])
    assert len(out) == 2
    # fetch_df 返回 pandas Timestamp
    assert out[0]["symbol"] == "600519" and out[0]["first_appoint"] == pd.Timestamp("2026-08-15")
    assert out[1]["symbol"] == "000651"
