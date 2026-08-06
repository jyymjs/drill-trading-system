# -*- coding: utf-8 -*-
"""探测：登录态下 wbi/v2 字幕 + 浏览器真实 URL 直连"""
import sys; sys.stdout.reconfigure(encoding="utf-8")
import requests, hashlib, time, json
SESS = "880d4959%2C1801080875%2C3816f%2A82"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Referer": "https://www.bilibili.com/video/BV1BEyzBUEu3/"}
s = requests.Session(); s.headers.update(UA)
s.cookies.set("SESSDATA", SESS, domain=".bilibili.com")
MIXIN_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
nav = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=15).json()
img = nav["行情数据"]["wbi_img"]["img_url"].rsplit("/",1)[1].split(".")[0]
sub = nav["行情数据"]["wbi_img"]["sub_url"].rsplit("/",1)[1].split(".")[0]
key = "".join((img+sub)[i] for i in MIXIN_TAB)[:32]
def sign(p):
    p = {k:str(v) for k,v in p.items()}; p["wts"] = str(int(time.time()))
    q = "&".join(f"{k}={v}" for k,v in sorted(p.items()))
    p["w_rid"] = hashlib.md5((q+key).encode()).hexdigest(); return p

bvid = "BV1BEyzBUEu3"
v = s.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=15).json()
aid, cid = v["行情数据"]["aid"], v["行情数据"]["cid"]
# 路径1: wbi/v2 登录态（JSON接口）
r = s.get("https://api.bilibili.com/x/player/wbi/v2", params=sign({"bvid":bvid,"cid":cid}), timeout=15).json()
subs = (r.get("行情数据") or {}).get("subtitle", {}).get("subtitles", [])
print("路径1 wbi/v2 登录态 subtitles:", json.dumps(subs, ensure_ascii=False)[:400] if subs else "空")
# 路径2: 浏览器抓到的真实URL直连（测是否要cookie）
u = "https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/1154504825295623346320883666168a157aeea3d5123af2b65478b592?auth_key=1785529088-0c7b52d8dfae440db47c18d591efcf47-0-44fac2b1a07ce2807e5d7964cdef1f30"
r2 = requests.get(u, headers={"User-Agent": UA["User-Agent"], "Referer": "https://www.bilibili.com/"}, timeout=15)
print("路径2 直连状态:", r2.status_code, "| 长度:", len(r2.content))
try:
    j = r2.json()
    body = j.get("body", [])
    print(f"   ✅ 字幕{len(body)}条 | 开头: {body[0]['content'][:40]} | 结尾: {body[-1]['content'][:40]}")
except Exception:
    print("   内容:", r2.text[:200])
