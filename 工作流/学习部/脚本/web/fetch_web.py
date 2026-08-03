# -*- coding: utf-8 -*-
"""
通用网页/论文文本抓取脚本（O2：补齐学术域名抓取能力，实测链路：2026-08-01）
- 任意 URL → 抓取 HTML → bs4 正文提取（标题/作者/来源/发布时间/正文文本，中文通用）
- 论文页（arxiv abs 页等）：提取标题/作者/摘要/发布时间，正文优先
- 输出：markdown 文本档案（知识库 raw 层，按 知识库/系统/templates/raw档案模板.md 结构）
- 分类/相关性命名/关键词/时效性由 AI 在技能环节最终判定，本脚本只落 raw（--topic 决定主题目录）

用法：
  python fetch_web.py --url <任意URL> --topic <主题>
  python fetch_web.py --url https://arxiv.org/abs/2508.15805 --topic AI学习方法
  python fetch_web.py --url <URL> --topic <主题> --force   # 已存在时强制重新抓取
默认输出：../../知识库/{主题}/raw/（本系统私有知识库 raw 层；脚本位于 学习部/scripts/web/）
"""
import sys, os, re, time, json, argparse
import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 来源简写判定：学术域名 → 论文，其余 → 网页
PAPER_DOMAINS = ("arxiv.org", "huggingface.co", "aclanthology.org", "paperswithcode.com",
                 "openreview.net", "acm.org", "ieee.org", "springer.com", "sciencedirect.com",
                 "nature.com", "semanticscholar.org")
# 时效性判定：新闻/资讯类域名 → 短期，论文 → 中期，其余 → 中期（AI 技能环节可再修正）
NEWS_DOMAINS = ("news", "cnbeta", "ithome", "36kr", "juejin", "sohu", "sina", "qq.com",
                "163.com", "theverge", "techcrunch", "arstechnica", "reuters")

# 正文容器里需要剔除的噪声节点（导航/脚本/装饰）
NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "button",
              "noscript", "iframe", "svg", "canvas", "dialog", "template"]


def extract_bvid(url):
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return m.group(1) if m else None


def is_paper_url(url):
    return any(d in url.lower() for d in PAPER_DOMAINS)


def guess_timeliness(url):
    """时效性合理默认：论文/博客 → 中期；新闻资讯类 → 短期（长期由 AI 技能环节判定）"""
    if is_paper_url(url):
        return "中期"
    if any(k in url.lower() for k in NEWS_DOMAINS):
        return "短期"
    return "中期"


def fetch_html(url, timeout, retries=1, retry_sleep=2.0):
    """抓取 HTML 并正确解码（headers charset → meta charset → 内容嗅探 → utf-8 兜底）

    加固（子任务13）：网络异常/HTTP 5xx 自动重试 1 次（间隔 2s）；
    风控 403/412 不重试直接提示（避免轰炸）。
    """
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
        except requests.RequestException as e:
            if attempt < retries:  # 网络抖动：重试
                time.sleep(retry_sleep)
                continue
            raise RuntimeError(f"网络请求失败 {type(e).__name__}: {e}（已自动重试 {retries} 次）")
        if r.status_code in (403, 412):  # 风控拦截：不重试，明确提示
            raise RuntimeError("风控拦截 HTTP 403/412——不重试（避免轰炸），请稍后手动重试，或浏览器手动打开保存正文")
        if r.status_code >= 500:  # 服务器错误：重试 1 次
            if attempt < retries:
                time.sleep(retry_sleep)
                continue
            raise RuntimeError(f"HTTP {r.status_code}（5xx，已自动重试 {retries} 次仍失败）")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}（非 200 视为失败，不落盘）")
        break  # 200 成功
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "html" not in ctype and ctype:  # 非 HTML（PDF/JSON/图片等）明确报错
        raise RuntimeError(f"非 HTML 内容（Content-Type: {ctype}），本脚本只处理网页/论文页")
    # 编码判定：headers charset → meta charset → chardet 嗅探 → utf-8 兜底（中文站点常见 gbk/utf-8）
    enc = requests.utils.get_encoding_from_headers(r.headers)
    # requests 对无 charset 的 text/* 默认回退 ISO-8859-1（会毁中文），忽略它继续判定
    if enc and enc.lower() in ("iso-8859-1", "iso8859-1", "latin-1", "latin1"):
        enc = None
    if not enc:
        m = re.search(rb'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9_\-]+)', r.content[:4096])
        enc = m.group(1).decode("ascii", errors="ignore") if m else None
    if not enc:
        enc = r.apparent_encoding or "utf-8"
    return r.content.decode(enc, errors="replace")


def find_jsonld_article(soup):
    """从 JSON-LD 里找文章类节点（ScholarlyArticle/Article/NewsArticle/WebPage 等）"""
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        def walk(obj):
            if isinstance(obj, dict):
                t = obj.get("@type")
                if isinstance(t, str) and t.lower() in ("scholarlyarticle", "article", "newsarticle",
                                                        "techarticle", "webpage", "blogposting", "report"):
                    return obj
                for v in obj.values():
                    r = walk(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = walk(v)
                    if r:
                        return r
            return None
        art = walk(data)
        if art:
            return art
    return None


def extract_meta(soup, props, names):
    """按顺序取第一个命中的 meta 标签值（property 优先，其次 name，其次 itemprop）"""
    for p in props:
        tag = soup.find("meta", attrs={"property": p}) or soup.find("meta", attrs={"itemprop": p})
        if tag and tag.get("content"):
            return tag["content"].strip()
    for n in names:
        tag = soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def clean_text(text):
    """压缩空白：每行 strip、连续空行合并为一行"""
    lines = [ln.strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def extract_content(soup, url):
    """提取（标题/作者/发布时间/摘要/正文文本）。论文页与通用站共用：arxiv meta → JSON-LD → og/meta 兜底"""
    paper = is_paper_url(url)
    meta = {}

    # ① 标题
    t = extract_meta(soup, ["og:title", "twitter:title"], ["citation_title", "title"])
    if not t:
        h1 = soup.find("h1")
        t = h1.get_text(strip=True) if h1 else None
    if not t:
        tag = soup.find("title")
        t = tag.get_text(strip=True) if tag else None
    meta["title"] = t or "（无标题）"

    # ② 作者/来源（arxiv 的 citation_author 有多个同名 meta，需单独处理）
    authors = [a["content"].strip() for a in soup.find_all("meta", attrs={"name": "citation_author"}) if a.get("content")]
    if not authors:
        authors = [a["content"].strip() for a in soup.find_all("meta", attrs={"property": "article:author"}) if a.get("content")]
    meta["authors"] = authors  # 空列表表示未知

    # ③ 发布时间（arxiv citation_date 格式 YYYY/MM/DD，其余多 ISO 8601）
    d = extract_meta(soup, ["article:published_time", "datePublished"], ["citation_date", "date", "dc.date"])
    if d:
        m = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", d)
        meta["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else d[:10]
    else:
        meta["date"] = None

    # ④ 摘要/简介
    abstract = None
    if paper:
        ab = soup.find("blockquote", class_=lambda c: c and "abstract" in c)
        if ab:
            abstract = ab.get_text(" ", strip=True)
    if not abstract:
        jd = find_jsonld_article(soup)
        if jd:
            abstract = jd.get("abstract") or jd.get("description") or ""
            abstract = abstract.strip() or None
    if not abstract:
        abstract = extract_meta(soup, ["og:description"], ["citation_abstract", "description"])
    meta["abstract"] = abstract

    # ⑤ 关键词（meta keywords，空则标题分词兜底）
    kw = extract_meta(soup, ["article:tag", "og:tag"], ["keywords", "news_keywords"])
    meta["keywords"] = [k.strip() for k in re.split(r"[,，;；/|]+", kw)] if kw else []

    # ⑥ 正文：arxiv 特判 div.leftcolumn（正文区）→ <article> → <main> → <body>（去噪后取文本）
    container = None
    if paper:
        container = soup.select_one("div.leftcolumn")  # arxiv 正文区（含摘要），实测结构
    container = container or soup.find("article") or soup.find("main") or soup.find("body")
    if container:
        for tag in container.find_all(NOISE_TAGS):
            tag.decompose()
        if paper:  # arxiv 导航/提交历史等噪声区块
            for sel in (".submission-history", ".extra-services", ".full-text", ".abs-actions", ".dropdown-actions"):
                for tag in container.select(sel):
                    tag.decompose()
        text = clean_text(container.get_text("\n"))
    else:
        text = ""
    meta["body"] = text

    return meta, paper


def compress_title(title, n=8):
    """标题压缩为文件名内容要点（≤n 字符，去标点空格，中英文通用）"""
    t = re.sub(r"[\s\W_]+", "", title, flags=re.UNICODE)
    return t[:n] or "无题"


def slug_last(url):
    """URL 最后一段路径作为链接名（清理非法字符；arxiv 保留论文 ID）"""
    path = re.sub(r"[?#].*$", "", url).rstrip("/")
    seg = path.rsplit("/", 1)[-1] if path.rsplit("/", 1)[-1] else path.rsplit("/", 1)[-2]
    seg = re.sub(r"[\\/:*?\"<>|\s]+", "-", seg)[:40]
    return seg or "index"


def title_keywords(title, n=5):
    """标题分词兜底：英文按空白/标点切词，中文连续标题取前 8 字"""
    parts = re.split(r"[\s,，。;；:：|/、\-_()（）\[\]【】?!？！""''「」《》]+", title)
    kw = [p for p in parts if len(p) >= 2][:n]
    if not kw and len(title) >= 2:
        kw = [title[:8]]
    return kw


def build_output(meta, url, topic, paper):
    """按 raw档案模板生成 markdown 档案"""
    author_str = ", ".join(meta["authors"]) if meta["authors"] else "（未知）"
    keywords = meta["keywords"] or title_keywords(meta["title"])
    timeliness = guess_timeliness(url)

    lines = [
        "---",
        f"标题: {meta['title']}",
        f"主题: {topic}",
        f"来源: {url}（{author_str}）" if meta["authors"] else f"来源: {url}",
        f"日期: {time.strftime('%Y-%m-%d')}",
        f"关键词: [{', '.join(keywords)}]",
        f"时效性: {timeliness}",
        "---",
        "",
        f"# {meta['title']}",
        "",
        "## 元数据",
        f"- 来源方: {author_str}",
        f"- 链接: {url}",
        f"- 来源类型: {'论文' if paper else '网页'}",
        f"- 发布时间: {meta['date'] or '（未提取到）'}",
        f"- 抓取日期: {time.strftime('%Y-%m-%d')}",
        "",
        "## 摘要 / 简介",
        meta["abstract"] or "（未提取到摘要）",
        "",
        "## 正文",
    ]
    if meta["body"]:
        lines.append(meta["body"])
    else:
        lines.append("（正文提取为空——页面可能为 JS 动态渲染或反爬。"
                     "替代路径建议：① 浏览器手动打开原文保存正文，来源标记 manual；"
                     "② 改用浏览器自动化抓取）")
    lines += [
        "",
        "## 备注",
        f"> 本档案由 fetch_web.py 自动抓取（{time.strftime('%Y-%m-%d')}）；",
        "> 分类/相关性命名/关键词/时效性由 AI 在技能环节最终校正，本脚本只落 raw 层。",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="通用网页/论文抓取（正文提取 → 知识库 raw 层）")
    ap.add_argument("--url", required=True, help="任意网页/论文 URL")
    ap.add_argument("--topic", default="未分类", help="主题分类（归档到 知识库/{主题}/raw/，AI 判定时可指定）")
    ap.add_argument("--out", help="输出目录（覆盖 --topic）")
    ap.add_argument("--force", action="store_true", help="档案已存在时强制覆盖（知识库去重）")
    ap.add_argument("--timeout", type=int, default=15, help="请求超时秒数（默认15）")
    args = ap.parse_args()

    url = args.url
    try:
        html = fetch_html(url, args.timeout)
    except Exception as e:
        print(f"错误：抓取失败 {url} → {type(e).__name__}: {e}"); sys.exit(1)

    try:
        soup = BeautifulSoup(html, "html.parser")
        meta, paper = extract_content(soup, url)
    except Exception as e:
        print(f"错误：正文解析失败 → {type(e).__name__}: {e}"); sys.exit(1)

    # 输出位置：本系统私有知识库 ../知识库/{主题}/raw/（v1.3 分层：抓取原文进 raw 层）
    out_dir = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       "知识库", args.topic, "raw")
    link = extract_bvid(url) or slug_last(url)
    fname = f"{'论文' if paper else '网页'}-{link}-{compress_title(meta['title'])}-{time.strftime('%Y%m%d')}.md"
    out_path = os.path.join(out_dir, fname)
    if os.path.exists(out_path) and not args.force:
        print(f"已存在: {out_path}\n提示：已存在，使用 --force 覆盖")
        sys.exit(0)

    text = build_output(meta, url, args.topic, paper)
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)

    body_n = len(meta["body"].splitlines()) if meta["body"] else 0
    if not meta["body"]:
        print("⚠ 警告：正文提取为空（页面可能 JS 渲染/反爬）——替代路径："
              "① 浏览器手动保存正文（来源标记 manual）② 改用浏览器抓取；已保存元数据档案并在正文处标注")
    print(f"✔ 已保存: {out_path}（标题: {meta['title']}，正文{body_n}行，发布: {meta['date'] or '未知'}）")


if __name__ == "__main__":
    main()
