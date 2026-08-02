# -*- coding: utf-8 -*-
"""
拆书工具（高效学习系统 · 子任务14）
- 书籍文件 → 分章分卷 → 存 raw 层（与 find_download.py 同目录 scripts/book/）
- TXT ：按章节标题正则切分（第X章/Chapter X/第X卷 等常见模式）；无标题则按 --chapter-len 字数额定分卷
- EPUB：zipfile 解包，按 html 文件切章，BeautifulSoup 提取正文文本
- PDF ：pdfplumber → PyPDF2 → pypdf 多库降级提取文本层；无文本层 → 明确提示「扫描版/加密 PDF 需文字版或 OCR，本工具不支持」
- 输出：知识库/{主题}/raw/ 下 书籍-{书名}-{第N章}-{标题}-{YYYYMMDD}.md（来源简写"书籍"，与分类规范 raw 命名一致）
  每文件 frontmatter（标题/主题/来源/日期/关键词/时效性=长期）+ 章节锚点信息（原文件路径/章节位置）
- 超长章（> --chapter-len）自动子分卷，百万字书可完整分章入库

用法：
  python prepare_book.py --file <书籍文件> --topic <主题> [--out 输出目录] [--chapter-len 8000]
依赖：EPUB 需要 beautifulsoup4（fetch_web.py 已安装）；PDF 需要 pdfplumber（缺失时给出安装提示）
"""
import sys, os, re, time, zipfile, argparse

sys.stdout.reconfigure(encoding="utf-8")

# 常见章节标题模式：第X章/节/回/卷/部/篇/集、Chapter N、Part N、卷X（行首）
CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千万零]+\s*[章节回卷部篇集][^\n]{0,40}"
    r"|Chapter\s+\d+[^\n]{0,60}"
    r"|CHAPTER\s+\d+[^\n]{0,60}"
    r"|Part\s+[0-9一二三四五六七八九十]+[^\n]{0,40}"
    r"|卷\s*[0-9一二三四五六七八九十]+[^\n]{0,40})")
# EPUB 中非正文文件（导航/封面等；[^/]*? 非贪婪避免吞掉扩展名）
EPUB_SKIP = re.compile(r"(^|/)(toc|nav|cover)[^/]*?\.(xhtml|html|htm)$", re.I)
BOOK_EXT = (".txt", ".epub", ".pdf")


def kb_root():
    """知识库根自动定位：高效学习系统/知识库（本系统私有知识库，阶段四私有化后唯一位置）"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "知识库")


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


def read_txt(path):
    """TXT 编码探测：utf-8 → gbk → utf-16 → 兜底 replace"""
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------- 分章逻辑 ----------
def cut_long_lines(lines, chapter_len):
    """单行超长（整段无换行）按字符切分，保证每行 ≤ chapter_len"""
    cut = []
    for ln in lines:
        while len(ln) > chapter_len:
            cut.append(ln[:chapter_len])
            ln = ln[chapter_len:]
        cut.append(ln)
    return cut


def pack_chapters(segs, chapter_len):
    """segs: [(标题, 正文, 锚点)] → 超长章按 chapter_len 子分卷。返回最终 [(标题, 正文, 锚点)]"""
    out = []
    for title, body, anchor in segs:
        if len(body) <= chapter_len:
            out.append((title, body, anchor))
            continue
        vol, buf, buf_n = 1, [], 0
        for ln in cut_long_lines(body.splitlines(), chapter_len):
            buf.append(ln)
            buf_n += len(ln)
            if buf_n >= chapter_len:
                out.append((f"{title}（分卷{vol}）", "\n".join(buf), f"{anchor} · 分卷{vol}"))
                vol += 1
                buf, buf_n = [], 0
        if buf:
            out.append((f"{title}（分卷{vol}）", "\n".join(buf), f"{anchor} · 分卷{vol}"))
    return out


def is_chapter_title(ln):
    """章节标题判定：正则命中 + 正文启发式排除。
    正文行常以「第一章内容…」「第X章 提到…」开头——若行以句末标点结尾
    （。！？；，、）或「第X章」后紧跟常见正文虚词且整行较长 → 判为正文而非标题。"""
    if not CHAPTER_RE.match(ln):
        return False
    s = ln.strip()
    if re.search(r"[。！？；，、]$", s):
        return False  # 以句末标点结尾 → 正文行
    if len(s) > 60:
        return False  # 标题行一般较短
    # 词级虚词判定（单字符黑名单会误伤合法标题首字，如「第二章 提取练习」的"提"命中"提到"）
    if re.search(r"[章节回卷部篇集]\s*(?:中|里|内|的|话|提到|讲述|内容|本书|我们|这里|那里|这|那|都)", s) and len(s) > 20:
        return False  # 「第X章+虚词」长行 → 正文引用
    return True


def split_txt(text, chapter_len):
    """TXT 分章：章节标题正则切分；无标题 → 字数额定分卷。返回 [(标题, 正文, 锚点)]"""
    lines = text.splitlines()
    if not lines:
        return []
    segs = []  # (标题 or None, [行], 起始行号)
    cur_t, cur_l, cur_s = None, [], 0
    for i, ln in enumerate(lines):
        if is_chapter_title(ln):
            if cur_l:
                segs.append((cur_t, cur_l, cur_s))
            cur_t, cur_l, cur_s = ln.strip()[:60], [ln], i
        else:
            cur_l.append(ln)
    if cur_l:
        segs.append((cur_t, cur_l, cur_s))
    if len(segs) == 1 and segs[0][0] is None:  # 全文无章节标题 → 定额分卷
        return fixed_volumes(lines, chapter_len)
    chs = [(t or "前言", "\n".join(cls), f"第 {s + 1}-{s + len(cls)} 行") for t, cls, s in segs]
    return pack_chapters(chs, chapter_len)


def fixed_volumes(lines, chapter_len):
    """无章节标题时的定额分卷（TXT/PDF 共用思路）"""
    out, vol, buf, buf_n, st = [], 1, [], 0, 0
    for ln in cut_long_lines(lines, chapter_len):
        buf.append(ln)
        buf_n += len(ln)
        if buf_n >= chapter_len:
            out.append((f"第{vol}卷", "\n".join(buf), f"第 {st + 1} 行起"))
            vol += 1
            st += len(buf)
            buf, buf_n = [], 0
    if buf:
        out.append((f"第{vol}卷", "\n".join(buf), f"第 {st + 1} 行起"))
    return out


def split_epub(path, chapter_len):
    """EPUB 分章：zipfile 解包，按 html 文件切章，bs4 提取正文"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("依赖缺失：EPUB 需要 beautifulsoup4 —— pip install beautifulsoup4")
        sys.exit(1)
    z = zipfile.ZipFile(path)
    html_files = [n for n in z.namelist() if re.search(r"\.(xhtml|html|htm)$", n, re.I) and not EPUB_SKIP.search(n)]
    if not html_files:
        raise RuntimeError("EPUB 中未找到正文 html 文件（可能为异常 EPUB）")
    html_files.sort()
    segs = []
    for i, name in enumerate(html_files, 1):
        raw = z.read(name)
        soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
        for tag in soup.find_all(["script", "style", "nav"]):
            tag.decompose()
        h = soup.find("h1") or soup.find("h2") or soup.find("title")
        title = (h.get_text(strip=True)[:60] if h else "") or f"第{i}章（{name}）"
        body = clean_text(soup.get_text("\n"))
        segs.append((title, body, f"EPUB 内文件 {name}"))
    return pack_chapters(segs, chapter_len)


def extract_pdf_text(path):
    """PDF 文本层提取（多库降级：pdfplumber → PyPDF2 → pypdf）。
    返回 [(页号, 文本)]；失败抛 RuntimeError（扫描版/加密提示）。"""
    errs = []
    pages = None
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(pdf.pages)]
    except ImportError:
        errs.append("pdfplumber 未安装")
    except Exception as e:
        errs.append(f"pdfplumber: {e}")
    if pages and any(t.strip() for _, t in pages):
        return pages
    for libname in ("PyPDF2", "pypdf"):
        try:
            mod = __import__(libname)
            reader = mod.PdfReader(path)
            pages = [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]
            if any(t.strip() for _, t in pages):
                return pages
            errs.append(f"{libname}: 提取为空（无文本层）")
        except ImportError:
            errs.append(f"{libname} 未安装")
        except Exception as e:
            errs.append(f"{libname}: {e}")
    raise RuntimeError(
        "PDF 文本层提取失败——扫描版/加密 PDF 需文字版或 OCR，本工具不支持。\n"
        f"  （尝试：pip install pdfplumber；各库情况：{'；'.join(errs) or '无可用库'}）")


def split_pdf(pages, chapter_len):
    """PDF 无章节结构 → 按 chapter_len 累积页码分卷。返回 [(标题, 正文, 锚点)]"""
    out, vol, buf, buf_n, pstart = [], 1, [], 0, None
    for pno, text in pages:
        if not text.strip():
            continue
        if pstart is None:
            pstart = pno
        buf.append(text)
        buf_n += len(text)
        if buf_n >= chapter_len:
            span = f"{pstart}-{pno}"
            out.append((f"第{vol}卷（第{span}页）", "\n".join(buf), f"原书第 {span} 页"))
            vol += 1
            buf, buf_n, pstart = [], 0, None
    if buf:
        span = f"{pstart}-{pno}"
        out.append((f"第{vol}卷（第{span}页）", "\n".join(buf), f"原书第 {span} 页"))
    return out


# ---------- 输出 ----------
def write_chapter(out_dir, topic, book_name, idx, title, body, anchor, src_path):
    date = time.strftime("%Y%m%d")
    safe = re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:30] or "无题"
    fname = f"书籍-{book_name}-第{idx}章-{safe}-{date}.md"
    lines = [
        "---",
        f"标题: {book_name} · {title}",
        f"主题: {topic}",
        f"来源: {src_path}",
        f"日期: {time.strftime('%Y-%m-%d')}",
        f"关键词: [拆书, {book_name}]",
        "时效性: 长期",  # 书籍内容为长期知识（拆书场景），AI 技能环节可按内容校正
        "---",
        "",
        f"# {book_name} · {title}",
        "",
        f"> 拆书工具生成（原文件：{src_path}）——任何压缩可回原文核对（raw 保真 + 章节锚点）",
        f"> 章节锚点：{anchor}",
        "",
        "## 正文",
        "",
        body,
        "",
    ]
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def main():
    ap = argparse.ArgumentParser(description="拆书工具（书籍分章分卷 → 知识库 raw 层，含章节锚点）")
    ap.add_argument("--file", required=True, help="书籍文件（TXT/EPUB/PDF）")
    ap.add_argument("--topic", default="未分类", help="主题（归档到 知识库/{主题}/raw/）")
    ap.add_argument("--out", help="输出目录（默认 知识库/{主题}/raw/）")
    ap.add_argument("--chapter-len", type=int, default=8000, help="每章最大字数（默认8000；超长章自动分卷）")
    args = ap.parse_args()

    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        print(f"错误：文件不存在 {path}"); sys.exit(1)
    ext = os.path.splitext(path)[1].lower()
    if ext not in BOOK_EXT:
        print(f"错误：不支持的格式「{ext}」（支持 TXT/EPUB/PDF）"); sys.exit(1)

    book_name = re.sub(r'[\\/:*?"<>|\s]+', "-", os.path.splitext(os.path.basename(path))[0])[:40] or "书"
    print(f"▶ 拆书: {os.path.basename(path)}（格式 {ext[1:].upper()}，每章 ≤{args.chapter_len} 字）")

    try:
        if ext == ".txt":
            chapters = split_txt(read_txt(path), args.chapter_len)
        elif ext == ".epub":
            chapters = split_epub(path, args.chapter_len)
        else:  # .pdf
            pages = extract_pdf_text(path)
            chapters = split_pdf(pages, args.chapter_len)
    except RuntimeError as e:
        print("错误：", e); sys.exit(1)
    if not chapters:
        print("错误：未提取到任何正文内容"); sys.exit(1)

    out_dir = args.out or os.path.join(kb_root(), args.topic, "raw")
    os.makedirs(out_dir, exist_ok=True)
    for idx, (title, body, anchor) in enumerate(chapters, 1):
        out_path = write_chapter(out_dir, args.topic, book_name, idx, title, body, anchor, path)
        print(f"✔ 第{idx}章 {title}（{len(body)}字，{len(body.splitlines())}行）→ {out_path}")
    total = sum(len(b) for _, b, _ in chapters)
    print(f"\n完成：共 {len(chapters)} 章 / {total} 字 → {out_dir}")


if __name__ == "__main__":
    main()
