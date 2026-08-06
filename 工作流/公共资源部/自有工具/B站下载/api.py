"""B站 API 封装 — 视频信息获取 + WBI 签名 + 播放流获取"""

import hashlib
import re
import time
import urllib.parse
from typing import Optional

import requests

from .models import VideoInfo, VideoPage, PlayInfo, StreamItem

# ── 常量 ──────────────────────────────────────────────
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
}

WBI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
WBI_INDEX_URL = "https://api.bilibili.com/x/web-interface/wbi/index"

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

# WBI 签名密钥的 mixin key（B站前端固定值）
MIXIN_KEY = "72136226c6e0fdc2e38f8ef30899e17d"

# 画质编号 → 名称映射
QUALITY_MAP = {
    6: "240P",
    16: "360P",
    32: "480P",
    64: "720P",
    74: "720P60",
    80: "1080P",
    112: "1080P+",
    116: "720P60",
    120: "1080P60",
    125: "4K",
    126: "HDR",
    127: "杜比视界",
}


# ── WBI 签名 ──────────────────────────────────────────

def _get_wbi_keys(session: requests.Session) -> tuple[str, str]:
    """获取 WBI 签名的 img_key 和 sub_key"""
    # 方式1: 从 nav 接口获取（未登录也可返回 wbi_img）
    try:
        resp = session.get(WBI_NAV_URL, headers=BASE_HEADERS, timeout=10)
        data = resp.json()
        if data.get("行情数据") and "wbi_img" in data["行情数据"]:
            d = data["行情数据"]["wbi_img"]
            # img_url 格式: https://i0.hdslb.com/bfs/wbi/xxxxxxxxxxxx.png
            img_key = d["img_url"].rsplit("/", 1)[-1].split(".")[0]
            sub_key = d["sub_url"].rsplit("/", 1)[-1].split(".")[0]
            if img_key and sub_key:
                return img_key, sub_key
    except Exception:
        pass
    # 方式2: 尝试 wbi/index（旧接口，可能404）
    try:
        resp = session.get(WBI_INDEX_URL, headers=BASE_HEADERS, timeout=10)
        data = resp.json()
        if data.get("行情数据"):
            d = data["行情数据"]
            return d["img_key"], d["sub_key"]
    except Exception:
        pass
    raise RuntimeError("无法获取 WBI 签名密钥")


def _encrypt_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """对参数字典进行 WBI 签名，返回带签名字段的新字典"""
    keys = sorted(params.keys())
    sorted_params = "&".join(f"{k}={params[k]}" for k in keys)
    sign_str = sorted_params + img_key + sub_key + MIXIN_KEY
    wts = int(time.time())
    w_rid = hashlib.md5(sign_str.encode()).hexdigest()
    return {**params, "wts": str(wts), "w_rid": w_rid}


# ── 辅助函数 ──────────────────────────────────────────

def extract_bvid(text: str) -> Optional[str]:
    """从文本中提取 BV 号（支持纯BV号或完整链接）"""
    # 先尝试匹配完整链接中的 BV 号
    m = re.search(r"BV\w{10,12}", text)
    if m:
        return m.group(0)
    # 如果是纯 BV 号
    text = text.strip()
    if re.match(r"^BV\w{10,12}$", text):
        return text
    return None


def _make_session(cookie: str = "") -> requests.Session:
    """创建带Cookie和默认头的 Session"""
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    if cookie:
        s.headers["Cookie"] = cookie
    return s


# ── 公开 API ──────────────────────────────────────────

def get_video_info(bvid: str, cookie: str = "") -> VideoInfo:
    """
    获取视频基本信息（标题、UP主、分P列表等）

    参数:
        bvid: BV号
        cookie: 可选 Cookie 字符串（SESSDATA=xxx）
    返回:
        VideoInfo 对象
    异常:
        RuntimeError: API 返回错误或请求失败
    """
    session = _make_session(cookie)
    params = {"bvid": bvid}

    # WBI 签名
    img_key, sub_key = _get_wbi_keys(session)
    signed = _encrypt_wbi(params.copy(), img_key, sub_key)
    signed["bvid"] = bvid

    resp = session.get(VIEW_API, params=signed, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        msg = data.get("message", "未知错误")
        raise RuntimeError(f"获取视频信息失败 (code={data['code']}): {msg}")

    v = data["行情数据"]
    pages_raw = v.get("pages", [])

    pages = []
    for p in pages_raw:
        pages.append(VideoPage(
            cid=p["cid"],
            title=p.get("part", ""),
            page=p.get("page", 1),
            duration=p.get("duration", 0),
            part=p.get("part", ""),
        ))

    owner = v.get("owner", {})
    return VideoInfo(
        bvid=bvid,
        title=v.get("title", ""),
        owner_name=owner.get("name", ""),
        owner_uid=owner.get("mid", 0),
        duration=v.get("duration", 0),
        pages=pages,
        desc=v.get("desc", ""),
        pic=v.get("pic", ""),
    )


def get_play_info(bvid: str, cid: int, qn: int = 80,
                  cookie: str = "") -> PlayInfo:
    """
    获取视频指定分P的播放流信息（DASH / MP4 / FLV）

    参数:
        bvid: BV号
        cid:  分P的cid
        qn:   画质编号（6~125，默认80=1080P）
        cookie: 可选 Cookie
    返回:
        PlayInfo 对象
    异常:
        RuntimeError: API 返回错误
    """
    session = _make_session(cookie)
    params = {
        "bvid": bvid,
        "cid": str(cid),
        "qn": str(qn),
        "fnval": "4048",   # DASH + HDR + Dolby
        "fnver": "0",
        "fourk": "1",
    }

    # WBI 签名
    img_key, sub_key = _get_wbi_keys(session)
    signed = _encrypt_wbi({k: v for k, v in params.items()}, img_key, sub_key)
    signed["bvid"] = bvid

    resp = session.get(PLAYURL_API, params=signed, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        msg = data.get("message", "未知错误")
        raise RuntimeError(f"获取播放流失败 (code={data['code']}): {msg}")

    d = data["行情数据"]
    quality = d.get("quality", qn)
    accept_q = list(d.get("accept_quality", []))
    accept_desc = list(d.get("accept_description", []))
    support_fmts = d.get("support_formats", [])
    video_codecid = d.get("video_codecid", 7)

    play_info = PlayInfo(
        bvid=bvid,
        cid=cid,
        duration=d.get("timelength", 0),
        quality=quality,
        accept_quality=accept_q,
        accept_description=accept_desc,
        support_formats=support_fmts,
        video_codecid=video_codecid,
    )

    # 解析 DASH 流
    dash = d.get("dash")
    if dash:
        play_info.dash = True
        for v in dash.get("video", []):
            play_info.videos.append(StreamItem(
                id=v.get("id", 0),
                url=v.get("base_url", v.get("url", "")),
                base_url=v.get("base_url", ""),
                bandwidth=v.get("bandwidth", 0),
                codec_id=v.get("codecid", 0),
                codecs=v.get("codecs", ""),
                width=v.get("width", 0),
                height=v.get("height", 0),
                frame_rate=str(v.get("frame_rate", "")),
            ))
        for a in dash.get("audio", []):
            play_info.audios.append(StreamItem(
                id=a.get("id", 0),
                url=a.get("base_url", a.get("url", "")),
                base_url=a.get("base_url", ""),
                bandwidth=a.get("bandwidth", 0),
                codec_id=a.get("codecid", 0),
                codecs=a.get("codecs", ""),
            ))
        # 按画质降序排列
        play_info.videos.sort(key=lambda x: x.id, reverse=True)
        play_info.audios.sort(key=lambda x: x.bandwidth, reverse=True)
    else:
        # 旧版 FLV / MP4 格式
        play_info.dash = False
        durl = d.get("durl", [])
        for item in durl:
            play_info.videos.append(StreamItem(
                id=0,
                url=item.get("url", ""),
                base_url=item.get("base_url", item.get("url", "")),
                bandwidth=0,
                codec_id=video_codecid,
                codecs="",
                width=0,
                height=0,
            ))

    return play_info


def get_quality_name(qn: int) -> str:
    """根据画质编号返回中文名称"""
    return QUALITY_MAP.get(qn, f"未知({qn})")
