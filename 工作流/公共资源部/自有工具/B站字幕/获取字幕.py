# -*- coding: utf-8 -*-
"""B站 AI 字幕抓取工具。

输入 B站链接或 BV 号，输出视频的 AI 字幕全文（stdout 或保存到文件）。
需要 B站登录 Cookie（SESSDATA）才能拿到 AI 字幕，配置见 config.local.json。

用法:
    python get_subtitle.py <链接或BV号>              # 字幕打印到终端
    python get_subtitle.py <链接或BV号> --out 字幕.txt # 保存到文件
    python get_subtitle.py <链接或BV号> --p 2         # 多P视频选第2P
"""
import argparse
import hashlib
import json
import os
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.local.json")
API_BASE = "https://api.bilibili.com/x/web-interface"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

# wbi 签名：img_key+sub_key 按混淆表重排前 32 位作为 mixin key
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
             27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
             37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
             22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]


def load_sessdata():
    """读取 config.local.json 中的 SESSDATA，缺失/为空时给出明确指引。"""
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            "缺少配置文件 config.local.json。\n"
            "请复制 config.local.example.json 为 config.local.json，"
            "填入你的 SESSDATA（B站登录 Cookie）。\n"
            "获取方式：浏览器登录 bilibili.com → F12 → Application → "
            "Cookies → https://www.bilibili.com → 复制 SESSDATA 的值。")
    try:
        cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit("config.local.json 解析失败：不是合法 JSON，请检查格式。")
    sess = (cfg.get("sessdata") or "").strip()
    if not sess:
        sys.exit("config.local.json 中 sessdata 为空，请填入 B站登录 Cookie 后重试。")
    return sess


def extract_bvid(url_or_bv):
    """从链接或纯 BV 号中提取 BV 号。"""
    if url_or_bv.startswith("BV") or url_or_bv.startswith("bv"):
        return url_or_bv
    m = __import__("re").search(r"BV[0-9A-Za-z]{10}", url_or_bv)
    if not m:
        sys.exit(f"无法从输入中识别 BV 号：{url_or_bv}")
    return m.group(0)


def new_session(sessdata):
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
    return s


def get_mixin_key(s):
    try:
        nav = s.get(f"{API_BASE}/nav", timeout=15).json()
    except requests.RequestException as e:
        sys.exit(f"网络请求失败：{e}")
    if nav.get("code") != 0:
        sys.exit(f"SESSDATA 无效或已过期（{nav.get('code')}：{nav.get('message')}）。\n"
                 "请用浏览器重新登录 bilibili.com，将新的 SESSDATA 更新到 config.local.json。")
    img = nav["行情数据"]["wbi_img"]["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub = nav["行情数据"]["wbi_img"]["sub_url"].rsplit("/", 1)[1].split(".")[0]
    return "".join((img + sub)[i] for i in MIXIN_TAB)[:32]


def sign(params, mixin_key):
    """wbi 签名：排序 k=v 拼接 + mixin key，md5 得 w_rid，附 wts。"""
    p = {k: str(v) for k, v in params.items()}
    p["wts"] = str(int(time.time()))
    q = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    p["w_rid"] = hashlib.md5((q + mixin_key).encode()).hexdigest()
    return p


def get_video_info(s, bvid):
    r = s.get(f"{API_BASE}/view", params={"bvid": bvid}, timeout=15).json()
    if r.get("code") != 0:
        sys.exit(f"视频信息获取失败（{r.get('code')}）：{r.get('message')}")
    d = r["行情数据"]
    pages = d.get("pages") or []
    if not pages:
        sys.exit("该视频无分P信息，可能已被删除或设限。")
    return d


def get_subtitles(s, bvid, cid, mixin_key):
    """带 wbi 签名请求字幕接口，未登录/无字幕返回空列表。"""
    # 注意：player 接口不在 /x/web-interface/ 下，路径是 /x/player/wbi/v2
    r = s.get("https://api.bilibili.com/x/player/wbi/v2",
              params=sign({"bvid": bvid, "cid": cid}, mixin_key), timeout=15).json()
    if r.get("code") != 0:
        # -101 未登录 / -352 风控（多为 SESSDATA 过期）
        sys.exit(f"字幕接口返回错误（{r.get('code')}）：{r.get('message')}。"
                 "请检查 config.local.json 中的 SESSDATA 是否已过期。")
    return (r.get("行情数据") or {}).get("subtitle", {}).get("subtitles", [])


def fetch_subtitle_text(s, sub_url):
    url = sub_url if sub_url.startswith("http") else "https:" + sub_url
    r = s.get(url, timeout=15)
    r.raise_for_status()
    try:
        body = r.json().get("body") or []
    except json.JSONDecodeError:
        sys.exit("字幕文件解析失败，请重试。")
    if not body:
        sys.exit("字幕内容为空。")
    lines = sorted(body, key=lambda x: x.get("from", 0))
    return "\n".join(item.get("content", "").strip() for item in lines if item.get("content", "").strip())


def main():
    ap = argparse.ArgumentParser(description="B站 AI 字幕抓取（需 SESSDATA 登录）")
    ap.add_argument("target", nargs="?", help="B站视频链接或 BV 号（与 --url 二选一）")
    ap.add_argument("--url", help="B站视频链接（与位置参数二选一）")
    ap.add_argument("--out", help="保存字幕到文件（省略则打印到终端）")
    ap.add_argument("--p", type=int, default=1, help="多P视频选第几P，默认 1")
    args = ap.parse_args()

    target = args.url or args.target
    if not target:
        sys.exit("请提供 B站视频链接或 BV 号，例如：\n"
                 "  get_subtitle.py --url https://www.bilibili.com/video/BVxxxxxxxxxx/\n"
                 "  get_subtitle.py BVxxxxxxxxxx")
    bvid = extract_bvid(target)
    sessdata = load_sessdata()
    s = new_session(sessdata)
    mixin_key = get_mixin_key(s)

    info = get_video_info(s, bvid)
    pages = info["pages"]
    if not 1 <= args.p <= len(pages):
        sys.exit(f"第 {args.p}P 不存在，该视频共 {len(pages)}P。")
    cid = pages[args.p - 1]["cid"]
    title = f"（第{args.p}P）" + pages[args.p - 1].get("part", "") if len(pages) > 1 else info.get("title", "")

    subs = get_subtitles(s, bvid, cid, mixin_key)
    ai_subs = [x for x in subs if x.get("lan") in ("ai-zh", "ai")] or subs
    if not ai_subs:
        sys.exit(f"视频《{info.get('title', '')}》无可用 AI 字幕。\n"
                 "可能原因：SESSDATA 已过期 / 该视频本身没有 AI 字幕。"
                 "请更新 config.local.json 后重试。")

    text = fetch_subtitle_text(s, ai_subs[0]["subtitle_url"])

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"已保存 {len(text)} 字字幕 → {args.out}")
    else:
        print(f"# {title}（{bvid}）")
        print(text)


if __name__ == "__main__":
    main()
