# -*- coding: utf-8 -*-
"""
电子书找书/下载脚本（高效学习系统 · 子任务15 找书下载）
- 内置全部电子书渠道配置（2026-08 联网检索所得，共 38 条，见 CHANNELS）
- 分工：AI 对话层负责「找书候选 + 向用户确认」（同名书必确认书名/作者/版本），本脚本只执行下载
- 下载：给定确认后的 URL → 下载 → 文件类型校验（PDF/EPUB/TXT/MOBI/AZW3）→ 存 知识库/{主题}/raw/
- 风控：请求间隔 --sleep（默认 3.0）；失败重试 1 次；验证码/反爬类渠道提示浏览器手动下载，不轰炸

用法：
  python find_download.py --list                       # 列出内置渠道
  python find_download.py --url <下载直链> --topic <主题> [--name 书名]   # 下载入库
  python find_download.py --search <关键词>            # 按关键词筛出候选渠道

输出：raw 档案存 知识库/{主题}/raw/（分章预处理由拆书工具负责，本脚本只负责下载校验）
"""
import sys, os, re, time, json, argparse, requests

sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# ============================================================
# 全部电子书渠道（2026-08 联网检索所得；镜像域名变动频繁，
# 失效时 AI 层应重新搜索「最新镜像」后更新本表）
# 字段: name(名称) / domains(域名) / type(类型) / coverage(覆盖) /
#       format(格式) / priority(优先级) / note(备注)
# ============================================================
CHANNELS = [
    # ── 首选 · 聚合大库 ──
    {"name": "Anna's Archive（安娜的档案）", "type": "聚合元搜索",
     "domains": ["zh.annas-archive.gl", "zh.annas-archive.pk", "zh.annas-archive.gd",
                 "zh.annas-archive.li", "zh.annas-archive.pm", "zh.annas-archive.in", "annas-archive.org"],
     "coverage": "6441万+书/9568万+论文（整合 Z-Library+LibGen+Sci-Hub）", "format": "EPUB/PDF/MOBI/TXT",
     "priority": "首选-冷门/绝版/外文原版",
     "note": "原.org 于2026-01被查封，域名频繁变动需以维基百科最新URL为准；<50MB免费慢速下载；部分国家ISP屏蔽"},
    {"name": "Z-Library", "type": "综合数字图书馆",
     "domains": ["zh.z-library.sk", "z-library.sk", "z-lib.fm", "1lib.sk", "zh.mex101.ru", "zh.834101.ru"],
     "coverage": "1400万+书/8400万+学术文章", "format": "EPUB/PDF/MOBI",
     "priority": "首选-外文/学术/小说/漫画全覆盖",
     "note": "国内直连镜像 zh.mex101.ru/zh.834101.ru；部分功能需登录/付费；域名常变动"},
    {"name": "LibGen（创世纪图书馆）", "type": "学术资源库",
     "domains": ["libgen.is", "libgen.ad"],
     "coverage": "数百万非小说/八千多万科学文章/两百多万漫画", "format": "PDF/EPUB/MOBI",
     "priority": "学术教材/论文优先",
     "note": "界面古早但无广告、直链下载"},
    {"name": "Sci-Hub", "type": "学术论文库",
     "domains": ["sci-hub.se", "sci-hub.st"],
     "coverage": "学术论文全文（需 DOI）", "format": "PDF",
     "priority": "学术论文优先",
     "note": "中文社区 discuss.sci-hub.org.cn；域名常变动需复核"},

    # ── 中文 · 搜索聚合 ──
    {"name": "鸠摩搜书（Jiumo）", "type": "中文网盘聚合搜索",
     "domains": ["jiumodiary.com"],
     "coverage": "聚合百度/阿里/夸克网盘分享", "format": "EPUB/PDF/MOBI/AZW3/TXT",
     "priority": "中文热门书/考试真题",
     "note": "跳转网盘链接，需手动转存或网盘工具"},
    {"name": "熊猫搜书", "type": "中文聚合导航",
     "domains": ["xmsoushu.com"],
     "coverage": "聚合鸠摩/书单网/微盘/好读等", "format": "多格式",
     "priority": "中文一站式搜索",
     "note": "导航站，跳转各源"},
    {"name": "24h搜书", "type": "中文搜索",
     "domains": ["24hbook.store"],
     "coverage": "按书名/作者搜索", "format": "AZW3/MOBI/EPUB/PDF",
     "priority": "中文书",
     "note": "免费下载、分类清晰"},
    {"name": "TheFuture", "type": "中文聚合搜索",
     "domains": ["bks.thefuture.top"],
     "coverage": "多站点聚合", "format": "多格式",
     "priority": "冷门书籍",
     "note": "聚合搜索，跳转原站"},
    {"name": "时宜搜书", "type": "中文聚合检索",
     "domains": ["（域名待确认，搜索时复核）"],
     "coverage": "聚合检索站", "format": "多格式",
     "priority": "中文书",
     "note": "点击跳转原网站下载"},
    {"name": "SaltyLeo 的书架", "type": "中文聚合搜索",
     "domains": ["book.tstrs.me", "tstrs.me"],
     "coverage": "全网电子书查询", "format": "多格式",
     "priority": "中文书",
     "note": "搜索引擎式查询"},
    {"name": "虫部落·电子书搜索", "type": "中文聚合搜索",
     "domains": ["giffox.com"],
     "coverage": "多源聚合", "format": "多格式",
     "priority": "中文书",
     "note": "虫部落旗下聚合站"},
    {"name": "书享家", "type": "导航聚合站",
     "domains": ["（域名待确认，搜索时复核）"],
     "coverage": "收录数百个电子书资源站", "format": "导航",
     "priority": "找站导航",
     "note": "Kindle/PDF/书单/外文/杂志/古籍八大类"},

    # ── 中文 · 书库直存 ──
    {"name": "知海图书馆（ZhihaiLib）", "type": "中文电子书库",
     "domains": ["zhihailib.com"],
     "coverage": "17万+本，中图法12大类", "format": "EPUB/PDF/MOBI/AZW3/TXT",
     "priority": "中文书多格式",
     "note": "多网盘免费下载通道；GitHub开源书库同步（harryfighting/zhihailib）"},
    {"name": "SoBooks", "type": "中文电子书库",
     "domains": ["sobooks.cc"],
     "coverage": "小说/文学/历史/经典/心理/漫画", "format": "EPUB/MOBI",
     "priority": "中文书",
     "note": "分类详细"},
    {"name": "怀旧书库", "type": "中文老旧图书",
     "domains": ["huaijiushuku.top"],
     "coverage": "中文老旧图书", "format": "多格式",
     "priority": "老书/绝版中文",
     "note": "怀旧向"},
    {"name": "电子课本网", "type": "教材库",
     "domains": ["dzkbw.com"],
     "coverage": "小学/初中/高中电子课本", "format": "PDF",
     "priority": "课本教材",
     "note": "免费"},
    {"name": "微信读书", "type": "正版阅读平台",
     "domains": ["weread.qq.com"],
     "coverage": "国内出版书为主", "format": "在线阅读/导出受限",
     "priority": "日常阅读",
     "note": "任务/组队白嫖无限卡；排版佳；导出受限不适合拆书"},

    # ── 合法 · 公版书库 ──
    {"name": "Project Gutenberg（古登堡计划）", "type": "公版书库（合法）",
     "domains": ["gutenberg.org"],
     "coverage": "7.5万+公版书多语言", "format": "EPUB/Kindle/TXT/HTML",
     "priority": "公版书（版权过期）",
     "note": "完全合法、直链稳定"},
    {"name": "Standard Ebooks", "type": "公版精校书库（合法）",
     "domains": ["standardebooks.org"],
     "coverage": "公版英文书精校重排", "format": "EPUB/AZW3",
     "priority": "公版英文书质量最优",
     "note": "排版精良、直链下载"},
    {"name": "Obooko", "type": "免费书库（合法）",
     "domains": ["obooko.com"],
     "coverage": "1100万册免费图书（宣传口径）", "format": "PDF/EPUB/Kindle",
     "priority": "免费阅读",
     "note": "合法平台"},
    {"name": "Manybooks", "type": "免费书库（合法）",
     "domains": ["manybooks.net"],
     "coverage": "50000+免费电子书", "format": "多格式",
     "priority": "免费阅读",
     "note": "合法平台"},
    {"name": "Free-ebooks 系（含 GetFreeEbooks/Planetebook/DigiLibraries）", "type": "免费书库（合法）",
     "domains": ["free-ebooks.net", "getfreeebooks.com", "planetebook.com", "digilibraries.com"],
     "coverage": "公版英文电子书", "format": "PDF/EPUB",
     "priority": "公版英文",
     "note": "多个合法免费平台，直链"},
    {"name": "维基文库", "type": "公版文学库（合法）",
     "domains": ["zh.wikisource.org"],
     "coverage": "最大公版文学库", "format": "HTML/TXT",
     "priority": "公版中文",
     "note": "完全合法"},
    {"name": "Loyal Books", "type": "公版书库（合法）",
     "domains": ["loyalbooks.com"],
     "coverage": "7000+公版书/有声书，35种语言", "format": "EPUB/有声",
     "priority": "公版书/有声",
     "note": "合法免费"},
    {"name": "书格", "type": "古籍/公共版权图书馆",
     "domains": ["shuge.org"],
     "coverage": "古籍高清PDF", "format": "PDF",
     "priority": "古籍/历史文献",
     "note": "高清扫描版，拆书需OCR"},
    {"name": "中华古籍智慧化服务平台", "type": "古籍库（国图）",
     "domains": ["guji.nlc.cn"],
     "coverage": "13881种/147899册古籍", "format": "PDF",
     "priority": "古籍",
     "note": "国家图书馆牵头"},
    {"name": "台湾华文电子书库", "type": "公版书库",
     "domains": ["taiwanebook.ncl.edu.tw"],
     "coverage": "38488册电子书", "format": "PDF",
     "priority": "公版中文",
     "note": "台湾图书馆"},
    {"name": "香港大学电子书库", "type": "学术典藏",
     "domains": ["digitalrepository.lib.hku.hk"],
     "coverage": "历史文献与数码典藏", "format": "PDF",
     "priority": "学术文献",
     "note": "学术机构开放库"},
    {"name": "香港中文大学电子书库", "type": "学术典藏",
     "domains": ["repository.lib.cuhk.edu.hk"],
     "coverage": "历史文献与数码典藏", "format": "PDF",
     "priority": "学术文献",
     "note": "学术机构开放库"},

    # ── 垂直 · 专项 ──
    {"name": "科学文库", "type": "学术专著平台",
     "domains": ["book.scienceReading.cn"],
     "coverage": "科学出版社7万+专著教材", "format": "PDF",
     "priority": "科技学术专著",
     "note": "部分付费/机构订阅"},
    {"name": "码农书籍", "type": "编程书库",
     "domains": ["manongbook.com"],
     "coverage": "编程技术类电子书", "format": "多格式",
     "priority": "编程书",
     "note": "技术向"},
    {"name": "半本书匠", "type": "编程书库",
     "domains": ["banshujiang.cn"],
     "coverage": "编程技术类电子书", "format": "多格式",
     "priority": "编程书",
     "note": "技术向"},
    {"name": "幻梦轻小说", "type": "轻小说库",
     "domains": ["huanmengacg.com"],
     "coverage": "日漫/轻小说", "format": "TXT/在线",
     "priority": "轻小说",
     "note": "免费TXT下载"},
    {"name": "Mox.moe / 漫画迷", "type": "漫画库",
     "domains": ["mox.moe", "kox.moe"],
     "coverage": "漫画资源", "format": "EPUB/MOBI",
     "priority": "漫画",
     "note": "适合Kindle阅读"},
    {"name": "Magazinelib / 读者阁", "type": "杂志库",
     "domains": ["magazinelib.com", "duzhege.cn"],
     "coverage": "英文杂志/流行杂志", "format": "PDF",
     "priority": "杂志",
     "note": "外刊/流行杂志"},

    # ── 其他 ──
    {"name": "Olib 开放图书馆", "type": "开源书库",
     "domains": ["olib.wwwnav.com"],
     "coverage": "开源书籍下载", "format": "多格式",
     "priority": "开源书",
     "note": "B站UP主开发"},
    {"name": "鹿鸣川", "type": "快速下载站",
     "domains": ["lunarora.com"],
     "coverage": "免登录快速下载", "format": "多格式",
     "priority": "免登录快速",
     "note": "下载体验好"},
    {"name": "GitHub 开源书库", "type": "代码仓库书库",
     "domains": ["github.com/0voice/expert_readed_books",
                 "github.com/jbiaojerry/ebook-treasure-chest",
                 "github.com/harryfighting/zhihailib"],
     "coverage": "哲学/数学/历史/计算机等开源书", "format": "多格式",
     "priority": "开源书",
     "note": "git clone 或直链下载"},
]

# 可识别的电子书文件类型
BOOK_EXT = (".pdf", ".epub", ".txt", ".mobi", ".azw3", ".azw", ".djvu")
# 各格式是否可直接文本提取（供拆书工具判断；扫描 PDF/EPUB 需额外处理）
TEXT_EXTRACTABLE = {".txt": True, ".epub": True, ".pdf": "需文本层", ".mobi": "需转换", ".azw3": "需转换", ".djvu": "需转换"}

# ============================================================
# 渠道健康度台账与淘汰规则（2026-08 定，阈值可调）
# 台账文件: scripts/book/channels_health.json（运行时自动创建/更新）
# 淘汰规则：
#   A 连续失败 ≥5 次（含从未成功过）            → 自动淘汰（流程不可见）
#   B 曾有成功，但距最后成功 >180 天且期间又有失败 → 自动淘汰
#   C DNS/403 连续 3 次                        → 不淘汰，标记「疑似镜像变动」，提示 AI 搜索新镜像
# 淘汰 = 对找书/下载流程不可见（--list 不显示），--status 可查留痕；
#        --cleanup 输出建议彻底移除清单（供复盘时确认后手动删除脚本条目）
# ============================================================
HEALTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels_health.json")
KILL_STREAK = 5          # 规则A：连续失败阈值
KILL_DAYS = 180          # 规则B：最后成功距今阈值（天）
MIRROR_HINT_STREAK = 3   # 规则C：疑似域名变动提示阈值
DISABLED_REASONS = {"streak": "连续失败≥5次", "stale": f"超{KILL_DAYS}天无成功且近期失败", "manual": "人工禁用"}


def load_health():
    """加载渠道健康台账（无则返回空结构）"""
    try:
        with open(HEALTH_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_health(health):
    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=2)


def record_channel(name, ok, reason=""):
    """记录一次渠道访问结果并执行淘汰判定。name 取 CHANNELS 中的渠道名。"""
    health = load_health()
    h = health.setdefault(name, {"fail_streak": 0, "last_success": None, "last_fail": None, "disabled": None, "hint": False, "hint_streak": 0})
    if ok:
        h["fail_streak"] = 0
        h["disabled"] = None  # 成功即复活（曾被淘汰的渠道恢复可见）
        h["hint"] = False     # 成功证明镜像可用，清除疑似变动标记
        h["hint_streak"] = 0  # 规则C：成功清零连续 403/DNS 计数
        h["last_success"] = time.strftime("%Y-%m-%d")
    else:
        h["fail_streak"] = h.get("fail_streak", 0) + 1
        h["last_fail"] = time.strftime("%Y-%m-%d")
        # 规则C：疑似域名变动提示（403/DNS 类）——连续 MIRROR_HINT_STREAK 次才标记，
        # 防单次抖动误报；非 403/DNS 类失败不增减计数；成功清零
        if reason and any(k in reason for k in ("403", "域名", "DNS", "无法解析", "404")):
            h["hint_streak"] = h.get("hint_streak", 0) + 1
            if h["hint_streak"] >= MIRROR_HINT_STREAK:
                h["hint"] = True
        # 规则A：连续失败 ≥5 次 → 淘汰
        if h["fail_streak"] >= KILL_STREAK:
            h["disabled"] = "streak"
        # 规则B：曾有成功但超期且近期失败 → 淘汰
        if h.get("last_success"):
            try:
                from datetime import datetime
                days = (datetime.now() - datetime.strptime(h["last_success"], "%Y-%m-%d")).days
            except Exception:
                days = 0
            if days > KILL_DAYS and h["fail_streak"] >= 1:
                h["disabled"] = "stale"
    save_health(health)
    return health[name]


def channel_status(ch, health):
    """汇总单渠道状态：正常 / 淘汰 / 疑似镜像变动"""
    h = health.get(ch["name"], {})
    if h.get("disabled"):
        return f"淘汰（{DISABLED_REASONS.get(h['disabled'], h['disabled'])}）"
    if h.get("hint"):
        return "⚠疑似镜像变动（建议搜索新镜像）"
    return "正常"


def active_channels(health):
    """对找书/下载流程可见的渠道（淘汰者不可见）"""
    return [c for c in CHANNELS if not health.get(c["name"], {}).get("disabled")]


def list_channels(keyword=None):
    """输出渠道配置表（AI 层选渠道时参考；keyword 可选筛选；已淘汰渠道不显示）"""
    health = load_health()
    items = active_channels(health)
    if keyword:
        kw = keyword.lower()
        items = [c for c in items if kw in c["name"].lower() or kw in c["type"].lower()
                 or kw in c["priority"].lower() or kw in c["coverage"].lower()]
    dead = [c["name"] for c in CHANNELS if health.get(c["name"], {}).get("disabled")]
    print(f"可用电子书渠道 {len(items)}/{len(CHANNELS)} 条" + (f"，筛选「{keyword}」命中 {len(items)} 条" if keyword else "") + "：\n")
    for i, ch in enumerate(items, 1):
        st = channel_status(ch, health)
        print(f"[{i}] {ch['name']}（{ch['type']}）[{st}]")
        print(f"    域名: {' / '.join(ch['domains'])}")
        print(f"    覆盖: {ch['coverage']} ｜ 格式: {ch['format']}")
        print(f"    优先级: {ch['priority']} ｜ 备注: {ch['note']}\n")
    if dead:
        print(f"已淘汰（流程不可见）: {'、'.join(dead)}")
    else:
        print("已淘汰: 无")


def show_status():
    """健康度台账表（含淘汰留痕与疑似镜像变动提示）"""
    health = load_health()
    print("渠道健康度台账（scripts/book/channels_health.json）：\n")
    print(f"{'渠道':<28}{'连续失败':<8}{'最后成功':<12}{'最后失败':<12}{'状态':<24}")
    print("-" * 84)
    for ch in CHANNELS:
        h = health.get(ch["name"], {})
        st = channel_status(ch, health)
        print(f"{ch['name'][:26]:<28}{str(h.get('fail_streak', 0)):<8}"
              f"{str(h.get('last_success', '-')):<12}{str(h.get('last_fail', '-')):<12}{st:<24}")
    if not health:
        print("\n（台账为空——尚无访问记录；每次找书尝试后调用 --record 记录）")


def cleanup_suggest():
    """输出建议彻底移除的渠道清单（供复盘确认后手动删脚本条目）"""
    health = load_health()
    dead = [c for c in CHANNELS if health.get(c["name"], {}).get("disabled")]
    if not dead:
        print("无已淘汰渠道，无需清理。")
        return
    print("以下渠道已淘汰，建议从 CHANNELS 中移除（人工确认后删脚本条目）：\n")
    for i, ch in enumerate(dead, 1):
        reason = DISABLED_REASONS.get(health[ch["name"]]["disabled"], "未知")
        print(f"[{i}] {ch['name']}（{reason}）")
        print(f"    域名: {' / '.join(ch['domains'])}\n")


def download(url, out_dir, name=None, sleep=3.0):
    """下载书籍文件 → 校验类型 → 存 raw。返回 (路径, 错误) 二元组。

    设计要点：
    - UA 模拟浏览器；超时 30s；重定向跟随
    - 风控：一次重试（仅网络/5xx），-403/验证码类直接提示浏览器手动下载
    - 不绕过验证码/反爬，不轰炸
    """
    print(f"▶ 下载: {url}")
    try:
        r = requests.get(url, headers=UA, timeout=30, stream=True)
    except requests.RequestException as e:
        return None, f"请求失败 {type(e).__name__}: {e}"

    if r.status_code == 403:
        return None, "HTTP 403（反爬/验证码类）——请用浏览器手动下载后放入目录，脚本不绕过"
    if r.status_code != 200:
        # 网络/5xx 类错误：重试 1 次
        if r.status_code >= 500:
            time.sleep(sleep)
            try:
                r = requests.get(url, headers=UA, timeout=30, stream=True)
            except requests.RequestException as e:
                return None, f"重试失败 {type(e).__name__}: {e}"
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}（非 200 视为失败，不落盘）"

    # 文件名：优先 --name，否则取 URL 末段
    if name:
        fname = re.sub(r'[\\/:*?"<>|\s]+', "-", name)[:60] + ".pdf" if not re.search(r"\.\w{2,4}$", name) else name
    else:
        seg = url.split("/")[-1].split("?")[0]
        fname = seg if seg and "." in seg else f"book-{time.strftime('%Y%m%d%H%M%S')}"
    fname = os.path.basename(fname)

    # 扩展名校验
    ext = os.path.splitext(fname)[1].lower()
    if ext not in BOOK_EXT:
        # 以 Content-Type / URL 猜测
        ctype = (r.headers.get("Content-Type") or "").lower()
        guess = {"application/pdf": ".pdf", "application/epub+zip": ".epub",
                 "text/plain": ".txt", "application/octet-stream": ".bin"}.get(ctype)
        if guess and guess != ".bin":
            fname = os.path.splitext(fname)[0] + guess
            ext = guess
        else:
            return None, f"无法识别文件类型（扩展名 {ext or '无'}，Content-Type: {ctype or '未知'}）——不支持的书格式"

    # 流式落盘（大文件分块写）
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, fname)
    try:
        total = 0
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    except Exception as e:
        return None, f"写入失败 {type(e).__name__}: {e}"

    # 空文件/极小文件校验（<1KB 视为异常）
    if total < 1024:
        os.remove(out_path)
        return None, f"文件过小（{total}B），疑似失败页/反爬页，已删除不落盘"

    print(f"✔ 已下载: {out_path}（{total/1024/1024:.1f}MB，格式 {ext}）")
    print(f"  文本可提取性: {TEXT_EXTRACTABLE.get(ext, '未知')}（拆书预处理见拆书工具）")
    return out_path, None


def main():
    ap = argparse.ArgumentParser(description="电子书找书/下载（全渠道内置）")
    ap.add_argument("--list", nargs="?", const="", help="列出渠道（可带关键词筛选，如 --list 中文）")
    ap.add_argument("--search", help="按关键词筛出候选渠道")
    ap.add_argument("--url", help="下载直链（用户确认后的 URL）")
    ap.add_argument("--topic", default="未分类", help="主题（入库到 知识库/{主题}/raw/）")
    ap.add_argument("--name", help="自定义文件名（书名）")
    ap.add_argument("--out", help="输出目录（覆盖 --topic）")
    ap.add_argument("--sleep", type=float, default=3.0, help="请求间隔秒数（风控保护）")
    ap.add_argument("--channel", help="渠道名（下载后自动记录健康度；与 CHANNELS 中名称一致）")
    ap.add_argument("--record", nargs=2, metavar=("渠道名", "ok|fail[:原因]"), help="记录渠道访问结果（如 'ok' / 'fail' / 'fail:HTTP 403'）")
    ap.add_argument("--status", action="store_true", help="健康度台账表")
    ap.add_argument("--cleanup", action="store_true", help="输出建议彻底移除的淘汰渠道清单")
    args = ap.parse_args()

    if args.list is not None:
        list_channels(args.list or None)
        return
    if args.search:
        list_channels(args.search)
        return
    if args.status:
        show_status()
        return
    if args.cleanup:
        cleanup_suggest()
        return
    if args.record:
        name, res = args.record
        ok = res.lower().startswith("ok")
        reason = res.split(":", 1)[1] if ":" in res else ""
        h = record_channel(name, ok, reason)
        state = "正常" if not h.get("disabled") else f"已淘汰（{DISABLED_REASONS.get(h['disabled'])}）"
        hint = " ⚠疑似镜像变动，建议搜索新镜像" if h.get("hint") else ""
        print(f"✔ 已记录 {name}: {'成功' if ok else '失败'}（连续失败 {h['fail_streak']} 次）→ {state}{hint}")
        return
    if not args.url:
        ap.print_help()
        sys.exit(1)

    # 默认输出：学习系统私有知识库 高效学习系统/知识库/{主题}/raw/
    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "知识库", args.topic, "raw")
    path, err = download(args.url, out_dir, args.name, args.sleep)
    # 健康度自动记账（AI 层带 --channel 渠道名调用时）
    if args.channel:
        h = record_channel(args.channel, ok=not err, reason=err or "")
        state = "正常" if not h.get("disabled") else f"已淘汰（{DISABLED_REASONS.get(h['disabled'])}）"
        hint = " ⚠疑似镜像变动，建议搜索新镜像" if h.get("hint") else ""
        print(f"📊 渠道记账: {args.channel} → {'成功' if not err else '失败'}（连续失败 {h['fail_streak']} 次）{state}{hint}")
    if err:
        print("错误：", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
