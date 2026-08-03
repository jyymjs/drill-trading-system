# -*- coding: utf-8 -*-
"""
知识库合规检查器（学习部 · 子任务10）
- 一键扫描知识库 7 类问题，输出问题清单 + 修复建议（只读扫描，不自动修复）：
    1. 文件名规范    —— 精炼层 {日期}-{主题}-{内容要点}-{类型}.md（要点≤8字）、raw 层 {来源简写}-{BV号或链接名}-{内容}-{日期}.md
    2. 元数据完整性  —— frontmatter 六字段（标题/主题/来源/日期/关键词/时效性）是否齐全
    3. 索引一致性    —— 全局 index.md（及主题局部 index.md）条目与实际文件对照：缺条目/多余条目/相对路径失效
    4. 近似主题检测  —— 主题目录名之间的包含/前缀关系（如"编程" vs "AI编程"）
    5. 同主题冲突扫描—— 同主题内精炼文件元数据主题字段不一致 / 标题含对立词的"疑似矛盾"（启发式，需人工核查）
    6. 分层混放      —— raw 文件不在 raw/ 目录、精炼命名文件误入 raw/
    7. 关联字段完整性—— 精炼产出元数据缺 `关联:` 字段（v2.4 网状结构）
- 依据：知识库/系统/分类规范.md（命名/分层/索引）、调用协议.md（四级检索）、规划确认书 v2.4 机制⑧（关联字段）

用法：
  python check.py                        # 自动定位知识库根（学习部/知识库）
  python check.py --dir <知识库根>       # 显式指定
  python check.py --fix-suggest          # 每条问题附修复建议
退出码：0 = 无问题；1 = 有问题；2 = 参数/路径错误
"""
import sys, os, re, argparse

sys.stdout.reconfigure(encoding="utf-8")

# 分类规范：raw 层来源简写（含 书籍——拆书分卷，命名 书籍-<书名>-第X章-<标题>-<日期>.md）
RAW_SOURCE_SHORT = ("B站", "论文", "博客", "网页", "manual", "书籍")
# 分类规范：精炼层类型白名单
REFINED_TYPES = ("学习笔记", "方法建议书")
# frontmatter 六字段
META_FIELDS = ("标题", "主题", "来源", "日期", "关键词", "时效性")
# v2.4 网状结构：精炼产出必备关联字段
LINK_FIELD = "关联"
# 疑似矛盾启发式：标题含这些词 → 标注"疑似矛盾，需人工核查"（冲突处理四步：识别→分析→互标→裁决）
CONFLICT_WORDS = ("过时", "勘误", "矛盾", "冲突", "推翻", "纠正", "取代", "替代",
                  "废弃", "淘汰", "错误", "更正", "修订", "新版", "改版", "已被替代", "不再适用")
# 精炼层文件名正则：{日期}-{主题}-{内容要点}-{类型}.md
REFINED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)-(.+)-(.+)\.md$")
# raw 层文件名正则：{来源简写}-{BV号或链接名}-{内容}-{日期}.md
RAW_RE = re.compile(r"^(B站|论文|博客|网页|manual|书籍)-(.+)-(\d{8})\.md$")
# 主题知识卡：{主题}-知识卡.md（豁免精炼层命名检查）
CARD_RE = re.compile(r"^(.+)-知识卡\.md$")
# 错题卡：每主题一张，命名固定为 错题卡.md（分类规范「八」；豁免精炼层命名检查）
WRONG_CARD_NAME = "错题卡.md"


def kb_root():
    """知识库根自动定位：学习部/知识库（本系统私有知识库，阶段四私有化后唯一位置）"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "知识库")


def parse_frontmatter(text):
    """解析 frontmatter → (dict, 错误信息)。无 frontmatter 或格式错误时返回 (None, 原因)"""
    if not text.startswith("---"):
        return None, "无 frontmatter（文件不以 --- 开头）"
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return None, "frontmatter 未闭合（找不到结尾 ---）"
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k:
            meta[k] = v
    if not meta:
        return None, "frontmatter 为空"
    return meta, None


def check_filename(rel_path, in_raw):
    """文件名规范检查 → 返回 (是否合规, 问题描述 or None, 目标命名建议 or None)"""
    fname = os.path.basename(rel_path)
    if in_raw:
        if RAW_RE.match(fname):
            return True, None, None
        return False, f"raw 层命名不规范（应 来源简写（{'/'.join(RAW_SOURCE_SHORT)}）-链接名-内容-YYYYMMDD.md）", None
    # 精炼层：知识卡 / 错题卡豁免（分类规范特殊命名）
    if CARD_RE.match(fname) or fname == WRONG_CARD_NAME:
        return True, None, None
    m = REFINED_RE.match(fname)
    if not m:
        # 宽松诊断：指出缺哪一段，便于修复
        detail = []
        parts = fname[:-3].split("-") if fname.lower().endswith(".md") else fname.split("-")
        if len(parts) < 6:
            detail.append("缺少主题段或要点段（完整结构：日期-主题-要点-类型，共 4 段）")
        else:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", "-".join(parts[:3])):
                detail.append("日期段格式不对（应 YYYY-MM-DD）")
            if parts[-1] not in REFINED_TYPES:
                detail.append(f"类型段「{parts[-1]}」不在白名单 {REFINED_TYPES}")
        return False, "精炼层命名不规范（应 {日期YYYY-MM-DD}-{主题}-{内容要点}-{类型}.md）" + \
               ("；" + "；".join(detail) if detail else ""), None
    date, topic, point, ftype = m.groups()
    if ftype not in REFINED_TYPES:
        return False, f"类型字段「{ftype}」不在白名单 {REFINED_TYPES}", None
    if len(point) > 8:
        return False, f"内容要点「{point}」超 8 字（{len(point)} 字）", \
               f"{date}-{topic}-{point[:8]}-{ftype}.md"
    return True, None, None


class Checker:
    def __init__(self, root, fix_suggest=False):
        self.root = root
        self.fix_suggest = fix_suggest
        self.problems = []  # (类别号, 位置, 描述, 修复建议 or None)
        self.topics = []    # 主题目录名（第一级，除 系统/）
        self.scanned = {"精炼": 0, "raw": 0}

    def add(self, cat, where, desc, fix=None):
        self.problems.append((cat, where, desc, fix if self.fix_suggest else None))

    def scan(self):
        for name in sorted(os.listdir(self.root)):
            full = os.path.join(self.root, name)
            if not os.path.isdir(full):
                continue
            if name in ("系统", "未分类", ".git"):
                continue
            self.topics.append(name)
            self.scan_topic(name, full)
        self.check_index_consistency()
        self.check_similar_topics()
        return self.problems

    # ---------- 逐主题扫描：命名 / 元数据 / 混放 / 冲突 / 关联 ----------
    def scan_topic(self, topic, tdir):
        raw_dir = os.path.join(tdir, "raw")
        has_raw = os.path.isdir(raw_dir)
        card_names = set()
        # 主题根下文件（精炼层；含知识卡）
        for fname in sorted(os.listdir(tdir)):
            full = os.path.join(tdir, fname)
            if not os.path.isfile(full) or not fname.lower().endswith(".md"):
                continue
            rel = self.rel(full)
            if fname == "index.md":  # 主题局部索引，不参与精炼命名/关联检查
                continue
            if CARD_RE.match(fname):
                card_names.add(fname)
            # ① 文件名规范（精炼层）
            ok, desc, fix = check_filename(rel, in_raw=False)
            if not ok:
                self.add(1, rel, desc, fix and f"改名：{fix}" or "参考分类规范·三、文件名规范")
            self.scanned["精炼"] += 1
            self.check_meta(rel, full, in_raw=False)
            # ⑦ 关联字段完整性（v2.4 网状结构）
            meta, err = parse_frontmatter(open(full, encoding="utf-8").read())
            if meta is not None and LINK_FIELD not in meta:
                self.add(7, rel, f"元数据缺「{LINK_FIELD}:」字段（网状结构要求）", "补 `关联: [相关主题或文件路径]`（无则写「关联: 无」？建议如实标注）")
        # raw/ 目录下文件
        if has_raw:
            for fname in sorted(os.listdir(raw_dir)):
                full = os.path.join(raw_dir, fname)
                if not os.path.isfile(full) or not fname.lower().endswith(".md"):
                    continue
                rel = self.rel(full)
                ok, desc, fix = check_filename(rel, in_raw=True)
                if not ok:
                    self.add(1, rel, desc, "参考分类规范·三、raw 层命名（来源简写-链接名-内容-YYYYMMDD）")
                self.scanned["raw"] += 1
                self.check_meta(rel, full, in_raw=True)
        # ⑥ 分层混放：raw 命名文件散落在主题根（未入 raw/）
        if has_raw:
            for fname in sorted(os.listdir(tdir)):
                full = os.path.join(tdir, fname)
                if not os.path.isfile(full) or not fname.lower().endswith(".md"):
                    continue
                if fname.startswith(RAW_SOURCE_SHORT):
                    self.add(6, self.rel(full), "raw 层文件未放入 raw/ 目录（分层铁律）",
                             f"移动至 {topic}/raw/（索引不列 raw，无需改 index）")
        # ⑥ 分层混放：精炼命名文件误入 raw/
        if has_raw:
            for fname in sorted(os.listdir(raw_dir)):
                if REFINED_RE.match(fname) and fname.lower().endswith(".md"):
                    self.add(6, self.rel(os.path.join(raw_dir, fname)),
                             "精炼命名文件误入 raw/（raw 层只放抓取原文）",
                             "移出至主题根；若实为原文请按 raw 命名规范改名")
        # ⑤ 同主题冲突扫描：标题对立词启发式（"疑似"级别，标注人工核查）
        if has_raw:
            self.scan_conflict_words(tdir, skip_raw=True)

    def scan_conflict_words(self, tdir, skip_raw):
        for fname in sorted(os.listdir(tdir)):
            full = os.path.join(tdir, fname)
            if not os.path.isfile(full) or not fname.lower().endswith(".md"):
                continue
            for w in CONFLICT_WORDS:
                if w in fname:
                    self.add(5, self.rel(full),
                             f"疑似矛盾（标题含对立词「{w}」）——需按冲突四步人工核查：分析适用条件、双方保留互标，或裁决一方过时/错误",
                             "按冲突处理四步：①识别 ②分析适用条件 ③双方互标'冲突，适用条件：…' ④过时标'已被替代'/错误标勘误/判定不了留冲突区")
                    break  # 一文件报一次即可

    def check_meta(self, rel, full, in_raw):
        """② 元数据完整性：frontmatter 六字段"""
        text = open(full, encoding="utf-8").read()
        meta, err = parse_frontmatter(text)
        if err:
            self.add(2, rel, f"元数据检查失败：{err}", "文件头补标准 frontmatter（--- 开头，--- 结束）")
            return
        missing = [f for f in META_FIELDS if f not in meta]
        if missing:
            self.add(2, rel, f"缺元数据字段：{'、'.join(missing)}",
                     f"补齐：{'、'.join(missing)}（模板见 系统/templates/raw档案模板.md 或 学习笔记模板.md）")
        date = meta.get("日期", "")
        if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            self.add(2, rel, f"日期字段格式不规范「{date}」（应 YYYY-MM-DD）", "改日期为 YYYY-MM-DD")
        # ⑤ 同主题冲突扫描：frontmatter 主题字段与所在主题目录不一致
        # （raw 文件的父目录是 raw/，需向上取主题目录对比）
        parent = os.path.basename(os.path.dirname(full))
        topic_expected = parent if parent != "raw" else os.path.basename(os.path.dirname(os.path.dirname(full)))
        if meta.get("主题") and meta["主题"].strip("/") != topic_expected:
            self.add(5, rel, f"元数据「主题」字段「{meta['主题']}」与所在主题目录「{topic_expected}」不一致",
                     "二选一修正：改 frontmatter 主题字段，或移动文件到对应主题目录（移动后需同步索引）")

    # ---------- ③ 索引一致性 ----------
    def parse_index(self, path, base):
        """解析 index.md → {主题: [(文件名, 相对路径)]} + 仅 raw 层主题集合"""
        table = {}
        raw_only = set()
        cur = None
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except FileNotFoundError:
            return None, None, set()
        for ln in lines:
            s = ln.strip()
            m = re.match(r"^### (.+)$", s)
            if m:
                cur = m.group(1).split("（")[0].strip()
                table.setdefault(cur, [])
                if "仅 raw" in s:
                    raw_only.add(cur)
                continue
            if cur and s.startswith("|"):
                # 表格行：| [文件名](相对路径) | ... （跳过表头/分隔线）
                tm = re.match(r"^\|\s*\[(.+)\]\((.+)\)\s*\|", s)
                if tm:
                    table[cur].append((tm.group(1), tm.group(2).strip()))
        return table, raw_only, None if lines else None

    def check_index_consistency(self):
        """全局 index.md + 主题局部 index.md：缺条目 / 多余条目 / 相对路径失效"""
        # 实际精炼文件（应列索引）：主题根下 md（不含 index.md/系统/archive/raw）
        actual = {}  # 主题 → {相对路径集合}
        for topic in self.topics:
            tdir = os.path.join(self.root, topic)
            actual[topic] = set()
            for fname in sorted(os.listdir(tdir)):
                full = os.path.join(tdir, fname)
                if not os.path.isfile(full) or not fname.lower().endswith(".md"):
                    continue
                if fname == "index.md":
                    continue
                actual[topic].add(self.rel(full).replace("\\", "/"))
        idx_path = os.path.join(self.root, "index.md")
        table, raw_only, _ = self.parse_index(idx_path, self.root)
        if table is None:
            self.add(3, "index.md", "全局 index.md 不存在（分类规范·七：索引必须存在）", "按分类规范建 index.md")
        else:
            indexed = set()
            for topic, items in table.items():
                for fname, rel in items:
                    target = os.path.join(self.root, *rel.split("/"))
                    indexed.add(self.rel(target).replace("\\", "/"))
                    if not os.path.isfile(target):
                        self.add(3, f"index.md · {topic}", f"索引条目路径失效/文件不存在：{rel}",
                                 "删除该条目，或恢复文件（检查相对路径层级）")
            for topic, files in actual.items():
                if files and topic in raw_only:
                    self.add(3, f"index.md · {topic}",
                             "索引入口标注「仅 raw 层」但主题实际存在精炼文件（索引与文件不一致）",
                             "更新入口行：去掉「仅 raw 层」标注，并补精炼条目")
                for rel in sorted(files):
                    if rel not in indexed and topic not in raw_only:
                        self.add(3, f"index.md · {topic}", f"缺索引条目：{rel}",
                                 "追加行 `| [文件名](相对路径) | 类型 | 来源 | 时效性 |`（可用 index_maintain.py --add 维护）")
            # 多余条目：索引里有、文件系统没有 → 已在路径失效分支覆盖（target 不存在即报）
        # 主题局部 index.md（O5，存在时检查）
        for topic in self.topics:
            li = os.path.join(self.root, topic, "index.md")
            if os.path.isfile(li):
                t, ro, _ = self.parse_index(li, os.path.join(self.root, topic))
                if t is None:
                    continue
                for topic2, items in t.items():
                    for fname, rel in items:
                        target = os.path.join(self.root, topic, *rel.split("/"))
                        if not os.path.isfile(target):
                            self.add(3, f"{topic}/index.md", f"局部索引条目路径失效：{rel}", "删除或修正该条目")

    # ---------- ④ 近似主题检测 ----------
    def check_similar_topics(self):
        topics = sorted(self.topics)
        for i, a in enumerate(topics):
            for b in topics[i + 1:]:
                if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
                    self.add(4, f"主题「{a}」vs「{b}」",
                             f"近似主题：{'「'+a+'」是「'+b+'」的子串' if a in b else '「'+b+'」是「'+a+'」的子串'}（易造成主题分裂）",
                             "建议整合：评估相关性后合并目录（移 raw/、更新索引、删空壳，参考阶段五存量修复做法）")

    def rel(self, full):
        return os.path.relpath(full, self.root).replace("\\", "/")


def main():
    ap = argparse.ArgumentParser(description="知识库合规检查器（7 类问题，只读扫描，不自动修复）")
    ap.add_argument("--dir", help="知识库根目录（默认自动定位：学习部/知识库）")
    ap.add_argument("--fix-suggest", action="store_true", help="每条问题附带修复建议")
    ap.add_argument("--only", type=int, choices=range(1, 8), help="只检查某一类（1-7）")
    args = ap.parse_args()

    root = os.path.abspath(args.dir) if args.dir else kb_root()
    if not os.path.isdir(root):
        print(f"错误：知识库根不存在：{root}（可用 --dir 指定）"); sys.exit(2)
    if not os.path.isfile(os.path.join(root, "index.md")):
        print(f"错误：{root} 下未找到 index.md（知识库根判定异常？）"); sys.exit(2)

    c = Checker(root, args.fix_suggest)
    problems = c.scan()
    if args.only:
        problems = [p for p in problems if p[0] == args.only]

    cat_names = {1: "文件名规范", 2: "元数据完整性", 3: "索引一致性", 4: "近似主题",
                 5: "同主题冲突", 6: "分层混放", 7: "关联字段"}
    print(f"知识库合规检查 · 根: {root}")
    print(f"主题 {len(c.topics)} 个 · 精炼文件 {c.scanned['精炼']} 个 · raw 文件 {c.scanned['raw']} 个\n")
    for cat in range(1, 8):
        items = [p for p in problems if p[0] == cat]
        if not items:
            continue
        print(f"【{cat}】{cat_names[cat]}（{len(items)} 处）")
        for _, where, desc, fix in items:
            print(f"  • {where} — {desc}")
            if fix:
                print(f"    → 修复：{fix}")
        print()
    total = len(problems)
    if total == 0:
        print("✔ 未发现问题，全库合规。")
        sys.exit(0)
    print(f"汇总：发现 {total} 处问题（{len(set(p[0] for p in problems))} 类）。" +
          ("\n提示：--fix-suggest 可查看逐条修复建议。" if not args.fix_suggest else ""))
    sys.exit(1)


if __name__ == "__main__":
    main()
