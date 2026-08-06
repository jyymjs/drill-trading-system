"""财联社快讯数据源（T-017 P4）：全市场实时电报（分钟级情绪数据）

接口（2026-08-05 实测 errno=0 正常返回）：
- GET https://www.cls.cn/v1/roll/get_roll_list
- 旧接口 cls.cn/nodeapi/telegraphList 已于 2026-05 下线（#14），新版强制校验
  `sign`，但签名纯本地计算、无需任何 key：sign = md5(sha1(按 key 字典序拼接的
  query 串))。财联社偏 A 股财经、时效强；与东财 7×24 快讯互为独立备份。

用途：环境闸门 C4（分钟级情绪）的数据基础；快讯按时间戳落库，可回溯当日舆情。
"""
import hashlib
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from 数据基础.duckdb.data_sources.config import (
    CLS_PAGE,
    CLS_TIMEOUT,
    CLS_URL,
    REQUEST_INTERVAL,
    UA,
)

# 全局限速（与 cninfo 共用同一间隔参数）
_last_request_ts = [0.0]
_lock = threading.Lock()


def _throttle():
    """等待距上次请求至少 REQUEST_INTERVAL 秒"""
    with _lock:
        wait = REQUEST_INTERVAL - (time.time() - _last_request_ts[0])
        if wait > 0:
            time.sleep(wait)
        _last_request_ts[0] = time.time()


def _cls_sign(params: dict) -> str:
    """财联社 v1 API 本地签名：md5(sha1(按 key 字典序拼接的 query 串))"""
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


def fetch_telegraph(page_size: int = CLS_PAGE) -> list[dict]:
    """财联社电报（全市场实时快讯，最新 page_size 条）

    返回行：{ts, title, content, source}
    - ts 已转 'YYYY-MM-DD HH:MM:SS'（接口 ctime 为 Unix 秒；键名与 news_flash 表列一致）
    - source 固定 'cls.cn'（落库区分备用源东财 7×24）
    """
    _throttle()
    params = {
        "appName": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "last_time": "",
        "refresh_type": "1",
        "rn": str(page_size),
    }
    url = f"{CLS_URL}?{urlencode(params)}&sign={_cls_sign(params)}"
    r = requests.get(
        url,
        headers={"User-Agent": UA, "Referer": "https://www.cls.cn/"},
        timeout=CLS_TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("errno") not in (0, None):
        raise RuntimeError(f"财联社接口返回异常 errno={d.get('errno')} msg={d.get('msg')}")

    rows = []
    for it in d.get("行情数据", {}).get("roll_data", []) or []:
        ts = it.get("ctime")
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        rows.append({
            "ts": t,
            "title": it.get("title", "") or it.get("brief", ""),
            "content": it.get("content", "") or it.get("brief", ""),
            "source": "cls.cn",
        })
    return rows
