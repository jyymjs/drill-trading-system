# -*- coding: utf-8 -*-
"""
B 站视频文本获取脚本（实测链路：2026-08-01 验证通过）
- 视频信息：view 接口（游客即可）
- AI 字幕：wbi/v2 接口（需 WBI 签名 + SESSDATA 登录态）→ 明文 subtitle_url 直连下载
- 弹幕：dm/list.so（游客即可）
- 评论：x/v2/reply（游客即可，sort=2 按热度取 TOP-N）
输出：markdown 文本档案（raw 规范：文件名 B站-{BV号}-{标题压缩}-{日期}.md + frontmatter 元数据块）；
      档案已存在时不静默覆盖（按 BV 号扫描 raw 目录，提示 --force）

用法：
  python fetch.py --url <视频链接>
  python fetch.py --bvid BVxxxxx --out ../../知识库/{主题}/raw/
  python fetch.py --bvid BVxxxxx --topic 交易系统 --force   # 已存在时强制重新抓取
默认输出：../../知识库/{主题}/raw/（本系统私有知识库 raw 层；脚本位于 高效学习系统/scripts/bilibili/）
"""
import sys, os, re, time, json, hashlib, argparse, requests

sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Referer": "https://www.bilibili.com/"}

MIXIN_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]


def load_sessdata():
    """读取本地 SESSDATA 配置（无则返回 None，降级游客模式）"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("sessdata")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def extract_bvid(url):
    """从 URL 中提取 BV 号（纯字符串逻辑，避免为此创建客户端实例）"""
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return m.group(1) if m else None


def compress_title(title, n=8):
    """标题压缩为文件名内容要点（≤n 字符，去标点空格，中英文通用；对齐 fetch_web.py）"""
    t = re.sub(r"[\s\W_]+", "", title, flags=re.UNICODE)
    return t[:n] or "无题"


def title_keywords(title, n=5):
    """关键词兜底：标题分词（英文按空白/标点切词，中文连续标题取前 8 字；AI 技能环节可再补）"""
    parts = re.split(r"[\s,，。;；:：|/、\-_()（）\[\]【】?!？！""''「」《》]+", title)
    kw = [p for p in parts if len(p) >= 2][:n]
    if not kw and len(title) >= 2:
        kw = [title[:8]]
    return kw


def find_existing_raw(out_dir, bvid):
    """知识库去重：扫描 raw 目录下文件名包含该 BV 号的文件（规范命名 B站-{BV号}-{内容}-{日期}.md）

    返回第一个命中文件的完整路径；目录不存在或未命中返回 None。
    """
    if not os.path.isdir(out_dir):
        return None
    for fname in sorted(os.listdir(out_dir)):
        if bvid in fname and fname.lower().endswith(".md"):
            return os.path.join(out_dir, fname)
    return None


class BiliClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(UA)
        self.sessdata = load_sessdata()
        if self.sessdata:
            self.s.cookies.set("SESSDATA", self.sessdata, domain=".bilibili.com")
        self._wbi_key = None
        self._sessdata_invalid = False
        self._check_login()  # O3：SESSDATA 过期检测（只记录标志，提示由 main 统一打印一次）

    def _check_login(self):
        """O3：登录态检测——配置了 SESSDATA 时调 nav 接口验证 isLogin

        - isLogin == False → 标记过期（sessdata 置 None 降级游客），不静默
        - 顺带缓存 WBI key（nav 已返回 wbi_img，避免后续重复请求）
        - 网络异常时无法判定，按现状继续（不误报）
        """
        if not self.sessdata:
            return
        try:
            r = self.s.get("https://api.bilibili.com/x/web-interface/nav", timeout=15)
            j = r.json()
        except Exception:
            return  # 网络异常无法判定登录态
        # 实测：无效 SESSDATA 时 nav 返回 code=-101（账号未登录）；游客 code=0 且 isLogin=False
        if j.get("code") not in (0, -101):
            return
        data = j.get("data") or {}
        # 顺带缓存 WBI key（复用本次 nav 响应，省一次请求；-101 时 data 仍含 wbi_img）
        wbi = data.get("wbi_img") or {}
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if img and sub:
            self._wbi_key = "".join((img + sub)[i] for i in MIXIN_TAB)[:32]
        if j.get("code") == -101 or data.get("isLogin") is False:
            self._sessdata_invalid = True
            self.sessdata = None  # 降级为游客模式（字幕环节走未配置分支）

    def _wbi_mixin(self):
        """WBI 签名：img_key/sub_key 拼接后按混淆表取 32 字符（实测可用）"""
        if self._wbi_key:
            return self._wbi_key
        nav = self.s.get("https://api.bilibili.com/x/web-interface/nav", timeout=15).json()
        img = nav["data"]["wbi_img"]["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub = nav["data"]["wbi_img"]["sub_url"].rsplit("/", 1)[1].split(".")[0]
        self._wbi_key = "".join((img + sub)[i] for i in MIXIN_TAB)[:32]
        return self._wbi_key

    def _wbi_sign(self, params):
        params = {k: str(v) for k, v in params.items()}
        params["wts"] = str(int(time.time()))
        q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["w_rid"] = hashlib.md5((q + self._wbi_mixin()).encode()).hexdigest()
        return params

    def get_info(self, bvid):
        r = self.s.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=15)
        j = r.json()
        if j["code"] != 0:
            return None, f"view 接口失败 code={j['code']} message={j.get('message')}"
        return j["data"], None

    def get_subtitle(self, bvid, cid):
        """登录态 + WBI 签名 → 明文 subtitle_url → 直连下载全文"""
        if not self.sessdata:
            if self._sessdata_invalid:
                # O3：配置过 SESSDATA 但 nav 检测 isLogin=False → 明确提示过期，不静默降级
                return None, "SESSDATA 已过期或无效，字幕需登录（请更新 scripts/bilibili/config.local.json）"
            return None, "未配置 SESSDATA，字幕需登录（配置 scripts/bilibili/config.local.json）"
        r = self.s.get("https://api.bilibili.com/x/player/wbi/v2",
                       params=self._wbi_sign({"bvid": bvid, "cid": cid}), timeout=15)
        j = r.json()
        if j["code"] != 0:
            if j["code"] in (-412, -403):
                return None, f"风控拦截 code={j['code']}，已停止（请降低请求频率）"
            return None, f"字幕接口失败 code={j['code']}"
        subs = (j.get("data") or {}).get("subtitle", {}).get("subtitles", [])
        if not subs:
            return None, "该视频无字幕（UP主未开启/未上传）"
        # 取第一个可用字幕（优先中文）
        sub = next((x for x in subs if x.get("lan") in ("ai-zh", "zh-CN", "zh-Hans")), subs[0])
        url = sub["subtitle_url"]
        if not url.startswith("http"):
            url = "https:" + url
        jr = self.s.get(url, timeout=15)
        if jr.status_code != 200:
            return None, f"字幕下载失败 HTTP {jr.status_code}"
        body = jr.json().get("body", [])
        text = "\n".join(f"[{x['from']:07.1f}s] {x['content']}" for x in body)
        return text, None

    def get_danmaku(self, cid, top_n=50):
        r = self.s.get(f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}", timeout=15)
        if r.status_code != 200:
            return []
        # 显式按 UTF-8 解码（requests 对 XML 无 charset 时默认 ISO-8859-1 会乱码）
        xml = r.content.decode("utf-8", errors="replace")
        items = re.findall(r'<d p="([^"]*)">([^<]*)</d>', xml)
        # p 属性第 5 段为热度，取 TOP-N
        parsed = []
        for p, text in items:
            parts = p.split(",")
            hot = float(parts[4]) if len(parts) > 4 else 0
            parsed.append((hot, text))
        parsed.sort(key=lambda x: -x[0])
        return [t for _, t in parsed[:top_n]]

    def get_comments(self, aid, top_n=10):
        """按热度取 TOP-N 评论（游客即可，sort=2 按热度排序）"""
        r = self.s.get("https://api.bilibili.com/x/v2/reply",
                       params={"type": 1, "oid": aid, "sort": 2, "ps": top_n}, timeout=15)
        j = r.json()
        if j["code"] != 0:
            if j["code"] in (-412, -403):
                return None, f"风控拦截 code={j['code']}，已停止（请降低请求频率）"
            return [], f"评论接口失败 code={j['code']}"
        replies = (j.get("data") or {}).get("replies") or []
        return replies[:top_n], None


def main():
    ap = argparse.ArgumentParser(description="B站视频文本获取（信息+AI字幕+弹幕）")
    ap.add_argument("--url", help="视频链接")
    ap.add_argument("--bvid", help="BV号")
    ap.add_argument("--out", help="输出目录（覆盖 --topic）")
    ap.add_argument("--topic", default="未分类", help="主题分类（归档到 知识库/{主题}/raw/，AI 判定时可指定）")
    ap.add_argument("--no-danmaku", action="store_true", help="不取弹幕")
    ap.add_argument("--no-comment", action="store_true", help="不取评论")
    ap.add_argument("--comments-n", type=int, default=10, help="评论条数（按热度，默认10）")
    ap.add_argument("--force", action="store_true", help="档案已存在时强制覆盖（知识库去重）")
    ap.add_argument("--sleep", type=float, default=2.0, help="接口请求间隔秒数（风控保护，默认2.0）")
    args = ap.parse_args()

    bvid = args.bvid or (extract_bvid(args.url) if args.url else None)
    if not bvid:
        print("错误：请提供 --url 或 --bvid"); sys.exit(1)

    c = BiliClient()
    # O3：SESSDATA 过期提示（_check_login 已在初始化时检测，这里统一打印一次）
    if c._sessdata_invalid:
        print("⚠ SESSDATA 已过期或无效，请更新 高效学习系统/scripts/bilibili/config.local.json")
        print("  （字幕将降级为游客模式，视频信息/弹幕/评论不受影响）")
    info, err = c.get_info(bvid)
    if err:
        print("错误：", err); sys.exit(1)

    title, owner, aid, cid, desc = (info["title"], info["owner"]["name"],
                                    info["aid"], info["cid"], info["desc"])
    print(f"▶ {title} | UP: {owner} | aid={aid} cid={cid}")

    # 重复抓取检测：扫描 raw 目录下文件名含该 BV 号的文件（规范命名 B站-{BV号}-{内容}-{日期}.md）
    # 输出位置：本系统私有知识库 高效学习系统/知识库/{主题}/raw/（v1.3 分层：抓取原文进 raw 层）
    out_dir = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       "知识库", args.topic, "raw")
    existing = find_existing_raw(out_dir, bvid)
    if existing and not args.force:
        print(f"已存在: {existing}\n提示：已存在，使用 --force 覆盖")
        sys.exit(0)
    out_path = os.path.join(out_dir, f"B站-{bvid}-{compress_title(title)}-{time.strftime('%Y%m%d')}.md")

    subtitle, err = c.get_subtitle(bvid, cid)
    danmaku = [] if args.no_danmaku else c.get_danmaku(cid)
    time.sleep(args.sleep)  # 风控保护：请求间隔 ≥2 秒
    comments, cerr = [], None
    if not args.no_comment:
        comments, cerr = c.get_comments(aid, args.comments_n)
        if cerr:
            print("警告：", cerr)

    # 文件头 frontmatter（对齐 raw档案模板：标题/主题/来源/日期/关键词/时效性）
    lines = [
        "---",
        f"标题: {title}",
        f"主题: {args.topic}",
        f"来源: https://www.bilibili.com/video/{bvid}（UP主: {owner}）",
        f"日期: {time.strftime('%Y-%m-%d')}",
        f"关键词: [{', '.join(title_keywords(title))}]",
        "时效性: 短期",  # B站视频默认短期，AI 技能环节按内容校正（O7）
        "---",
        "",
        f"# {title}",
        "",
        "## 元数据",
        f"- 来源方: {owner}",
        f"- 链接: https://www.bilibili.com/video/{bvid}",
        f"- BV号: {bvid}",
        f"- 日期: {time.strftime('%Y-%m-%d')}",
        "",
        "## 简介",
        desc or "（无简介）",
        "",
        "## 字幕全文",
    ]
    if subtitle:
        lines.append(subtitle)
    else:
        lines.append(f"（{err}）")
    if danmaku:
        lines += ["", "## 热门弹幕", ""] + [f"- {d}" for d in danmaku]
    if comments:
        c_lines = []
        for rp in comments:
            member = rp.get("member") or {}
            name = member.get("uname", "匿名用户")
            msg = re.sub(r"\s+", " ", (rp.get("content") or {}).get("message", "")).strip()
            like = rp.get("like", 0)
            c_lines.append(f"- **{name}**（赞{like}）：{msg}")
        lines += ["", "## 热门评论", ""] + c_lines

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    sub_n = len(subtitle.splitlines()) if subtitle else 0
    print(f"✔ 已保存: {out_path}（字幕{sub_n}行，弹幕{len(danmaku)}条，评论{len(comments)}条）")


if __name__ == "__main__":
    main()
