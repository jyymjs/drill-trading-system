"""巨潮 cninfo 数据源（T-017 P4）：公告全文检索 + 定期报告预约披露

接口（2026-08-05 实测）：
- 公告：POST /new/hisAnnouncement/query
    - orgId 非统一 `gssx0{code}` 格式（如 601318→9900002221），硬编码导致大量
      601xxx 股票 totalAnnouncement=0 → 优先查官方映射表 szse_stock.json（模块级
      缓存），查不到再回退硬编码（a-stock-data #19 修复）。
    - 实测 announcementTypeName 恒为 null（接口已不再返回类型名）→ 类型用
      announcementType 数值码链（'||' 分隔层级，如 "01010503||010113||012399"）。
    - adjunctUrl 为相对路径（finalpage/.../xxx.PDF），补全为
      http://static.cninfo.com.cn/ 开头完整 PDF 下载链接。
- 预约披露：POST /new/information/getPrbookInfo（巨潮官方"预约披露"页面接口）
    - 字段：f001d_0102=报告期、f002d_0102=首次预约、f003d-f005d=初次/二次/三次
      变更、f006d_0102=实际披露。
    - 单股查询（stockCode 参数）或全市场分页（pagesize 最大 50，totalRows≈5540）。
    - 预约披露日 = 财报日避让的核心数据：买入前查 f002d 是否临近，避让财报风险。
"""
import threading
import time
from datetime import datetime

import requests
from 数据基础.duckdb.data_sources.config import (
    CNINFO_ANN_PAGE,
    CNINFO_ANN_URL,
    CNINFO_ORGID_URL,
    CNINFO_PRBOOK_URL,
    CNINFO_TIMEOUT,
    PRBOOK_MARKET,
    PRBOOK_PAGE,
    REQUEST_INTERVAL,
    UA,
)

# 巨潮 股票→orgId 映射（模块级缓存，首次调用拉取一次，全程复用）
_CNINFO_ORGID_MAP: dict[str, str] = {}
_orgid_lock = threading.Lock()

# 全局限速：相邻两次请求最小间隔（对源站友好）
# 注意：独立锁——orgid 缓存锁内也会调 _throttle，threading.Lock 不可重入，混用会死锁
_throttle_lock = threading.Lock()
_last_request_ts = [0.0]


def _throttle():
    """等待距上次请求至少 REQUEST_INTERVAL 秒"""
    with _throttle_lock:
        wait = REQUEST_INTERVAL - (time.time() - _last_request_ts[0])
        if wait > 0:
            time.sleep(wait)
        _last_request_ts[0] = time.time()


def _cninfo_ts_to_date(ts) -> str:
    """巨潮 announcementTime 返回 Unix 毫秒整数 → 'YYYY-MM-DD'"""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


def _cninfo_orgid(code: str) -> str:
    """查股票真实 orgId（动态查官方映射表，查不到回退硬编码）

    硬编码仅部分老股票适用（600519/600036 等），新股票（尤其 601xxx 段）需动态映射。
    """
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        with _orgid_lock:
            if not _CNINFO_ORGID_MAP:
                try:
                    _throttle()
                    r = requests.get(
                        CNINFO_ORGID_URL,
                        headers={"User-Agent": UA},
                        timeout=CNINFO_TIMEOUT,
                    )
                    _CNINFO_ORGID_MAP = {
                        s["code"]: s["orgId"]
                        for s in r.json().get("stockList", [])
                    }
                except Exception as e:  # 映射失败 → 回退硬编码规则
                    print(f"[WARN] 巨潮 orgId 映射表拉取失败，回退硬编码规则: {e}")
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def fetch_announcements(code: str, page_size: int = CNINFO_ANN_PAGE) -> list[dict]:
    """巨潮公告全文检索（单只股票最近 page_size 条）

    返回行：{symbol, date, title, ann_type, url, adjunct_url, adj_size, org_id}
    - ann_type：announcementType 数值码链（接口不再返回类型名，如实保留原始码）
    - url：巨潮详情页；adjunct_url：PDF 下载完整链接（相对路径已补全）
    """
    _throttle()
    payload = {
        "stock": f"{code},{_cninfo_orgid(code)}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    r = requests.post(CNINFO_ANN_URL, data=payload, headers=headers, timeout=CNINFO_TIMEOUT)
    r.raise_for_status()
    d = r.json()

    rows = []
    for it in d.get("announcements", []) or []:
        adj = it.get("adjunctUrl") or ""
        if adj and not adj.startswith("http"):
            adj = "http://static.cninfo.com.cn/" + adj
        rows.append({
            "symbol": it.get("secCode") or code,
            "date": _cninfo_ts_to_date(it.get("announcementTime")),
            "title": it.get("announcementTitle", ""),
            "ann_type": it.get("announcementType", ""),
            "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={it.get('announcementId', '')}",
            "adjunct_url": adj,
            "adj_size": it.get("adjunctSize"),
            "org_id": it.get("orgId", ""),
        })
    return rows


def default_section_time(today=None) -> str:
    """当前日期 → 最近一个已结束季度的最后一天（财报报告期）

    例：2026-08-05 → 2026-06-30（中报预约披露期）；2026-02-10 → 2025-12-31（年报期）
    """
    t = today or datetime.now().date()
    q = (t.month - 1) // 3          # 当前所处季度 0-3
    if q == 0:
        return f"{t.year - 1}-12-31"
    if q == 1:
        return f"{t.year}-03-31"
    if q == 2:
        return f"{t.year}-06-30"
    return f"{t.year}-09-30"


def fetch_prbook(symbol: str | None = None, section_time: str | None = None,
                 market: str = PRBOOK_MARKET) -> list[dict]:
    """定期报告预约披露查询（巨潮官方"预约披露"页面接口）

    - symbol 为空 → 全市场分页拉取（一个报告期 ≈5540 只，每页 50 需翻页）
    - symbol 给单只 → 单股查询（返回该股该报告期一行）
    - section_time 为财报截止日（如 2026-06-30），默认按当前日期推算

    返回行：{symbol, secname, report_period, first_appoint, change1, change2,
             change3, actual_date}
    - first_appoint = 首次预约披露日（财报日避让用）
    - change1/2/3 = 初次/二次/三次变更；actual_date = 实际披露日（未披露为空）
    """
    st = section_time or default_section_time()
    page = 1
    rows = []
    while True:
        _throttle()
        data = {
            "sectionTime": st,
            "firstTime": "",
            "lastTime": "",
            "market": market,
            "stockCode": symbol or "",
            "orderClos": "",
            "isDesc": "",
            "pagesize": str(PRBOOK_PAGE),
            "pagenum": str(page),
        }
        r = requests.post(
            CNINFO_PRBOOK_URL,
            data=data,
            headers={"User-Agent": UA, "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=data/yypl"},
            timeout=CNINFO_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()

        for it in d.get("prbookinfos", []) or []:
            rows.append({
                "symbol": it.get("seccode", ""),
                "secname": it.get("secname", ""),
                "report_period": it.get("f001d_0102", ""),
                "first_appoint": it.get("f002d_0102", ""),
                "change1": it.get("f003d_0102", ""),
                "change2": it.get("f004d_0102", ""),
                "change3": it.get("f005d_0102", ""),
                "actual_date": it.get("f006d_0102", ""),
            })
        total_pages = d.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1
    return rows
