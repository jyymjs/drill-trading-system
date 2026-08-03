# -*- coding: utf-8 -*-
"""
知识库索引维护工具（学习部 · 子任务11）
- 按 index.md 标准行格式维护全局索引：`| [文件名](相对路径) | 类型 | 来源 | 时效性 |`
- 新增条目自动追加到对应主题表格；新主题自动建「### 主题名（一句话定位）」入口 + 空表格
- 只维护 知识库/index.md，不触碰任何其他文件
- 依据：知识库/系统/分类规范.md（七、索引维护规范）与 index.md 头部维护规范

用法：
  python index_maintain.py --add <文件路径> [--topic 主题] [--type 类型] [--source 来源]
                           [--timeliness 时效性] [--desc 一句话定位]
  python index_maintain.py --remove <文件名>
退出码：0 = 成功；1 = 未找到/校验失败；2 = 参数错误
"""
import sys, os, re, argparse

sys.stdout.reconfigure(encoding="utf-8")

REFINED_TYPES = ("学习笔记", "方法建议书")
TABLE_HEADER = "| 文件 | 类型 | 来源 | 时效性 |"
TABLE_SEP = "|------|------|------|--------|"


def kb_root():
    """知识库根自动定位：学习部/知识库（本系统私有知识库，阶段四私有化后唯一位置）"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "知识库")


def read_frontmatter_value(path, key):
    """读 frontmatter 指定键的值（限文件头 --- 块内）"""
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return None
    fm = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not fm:
        return None
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", fm.group(1), re.M)
    return m.group(1).strip() if m else None


def load_lines(idx_path):
    try:
        with open(idx_path, encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        print(f"错误：索引不存在 {idx_path}（知识库根判定异常？可用 --dir 指定）")
        sys.exit(2)


def find_topic_block(lines, topic):
    """返回主题入口行（### 主题名...）下标，或 None"""
    for i, ln in enumerate(lines):
        m = re.match(r"^###\s+(.+)$", ln.strip())
        if m and m.group(1).split("（")[0].strip() == topic:
            return i
    return None


def block_end(lines, start):
    """主题块结束：start 之后第一个 ### 或 ## 标题行"""
    for i in range(start + 1, len(lines)):
        if re.match(r"^#{1,3}\s", lines[i].strip()):
            return i
    return len(lines)


def is_row_line(ln):
    """是条目行（| [文件](路径) | ...）"""
    return bool(re.match(r"^\|\s*\[.+\]\(.+\)\s*\|", ln.strip()))


def add_entry(lines, topic, row_line, desc):
    """向 index 追加条目行；新主题自动建入口行 + 表格。返回 (新lines, 说明)"""
    idx = find_topic_block(lines, topic)
    if idx is not None:
        end = block_end(lines, idx)
        # 块内最后一个条目行（跳过表头/分隔线）
        last_row = None
        for i in range(idx, end):
            s = lines[i].strip()
            if is_row_line(lines[i]) and s != TABLE_HEADER.strip() and not s.startswith("|---"):
                last_row = i
        if last_row is not None:
            lines.insert(last_row + 1, row_line)
            return lines, f"已追加到「{topic}」表格"
        # 有入口但无表格 → 入口行后补表头
        ins = idx + 1
        lines[ins:ins] = ["", TABLE_HEADER, TABLE_SEP, row_line, ""]
        return lines, f"已为「{topic}」补建表格并追加"
    # 新主题：追加到「## 主题入口」节内（保持节顺序；无该节则文件尾）
    head = f"### {topic}（{desc}）" if desc else f"### {topic}"
    new_block = ["", head, "", TABLE_HEADER, TABLE_SEP, row_line, ""]
    sec = next((i for i, ln in enumerate(lines) if ln.strip() == "## 主题入口"), None)
    if sec is not None:
        end = next((i for i in range(sec + 1, len(lines)) if re.match(r"^##\s", lines[i].strip())), len(lines))
        lines[end:end] = new_block
        return lines, f"已新建主题「{topic}」入口并追加"
    lines += new_block
    return lines, f"已新建主题「{topic}」入口并追加（index 无「## 主题入口」节，追加在文件尾）"


def remove_entry(lines, fname):
    """删除文件名匹配的索引条目行。返回 (新lines, 删除行列表, 受影响主题列表)"""
    new_lines, removed, topics = [], [], set()
    cur = None
    for ln in lines:
        m = re.match(r"^###\s+(.+)$", ln.strip())
        if m:
            cur = m.group(1).split("（")[0].strip()
        if is_row_line(ln) and re.match(r"^\|\s*\[" + re.escape(fname) + r"\]\(", ln.strip()):
            removed.append(ln)
            if cur:
                topics.add(cur)
            continue
        new_lines.append(ln)
    return new_lines, removed, topics


def infer_type(fname):
    m = re.search(r"-(学习笔记|方法建议书)\.md$", fname)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description="知识库索引维护（标准格式增删条目，只改 index.md）")
    ap.add_argument("--dir", help="知识库根目录（默认自动定位）")
    ap.add_argument("--add", metavar="文件路径", help="新增索引条目（精炼层文件；raw 层不列索引）")
    ap.add_argument("--remove", metavar="文件名", help="删除索引条目（按文件名匹配，跨主题）")
    ap.add_argument("--topic", help="主题（默认从文件路径推断第一级目录）")
    ap.add_argument("--type", help="类型（默认从文件名推断；缺省 学习笔记）")
    ap.add_argument("--source", help="来源（默认读文件 frontmatter 来源字段）")
    ap.add_argument("--timeliness", help="时效性 长期|中期|短期（默认读 frontmatter）")
    ap.add_argument("--desc", help="新建主题入口的一句话定位（如 编程类学习路线）")
    args = ap.parse_args()
    if not args.add and not args.remove:
        ap.print_help()
        sys.exit(2)

    root = os.path.abspath(args.dir) if args.dir else kb_root()
    idx_path = os.path.join(root, "index.md")
    lines = load_lines(idx_path)

    if args.remove:
        new_lines, removed, topics = remove_entry(lines, args.remove)
        if not removed:
            print(f"错误：索引中未找到条目「{args.remove}」（跨全部主题）"); sys.exit(1)
        text = "\n".join(new_lines).rstrip("\n") + "\n"
        with open(idx_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✔ 已删除 {len(removed)} 条索引条目：{args.remove}")
        for t in sorted(topics):
            # 检查该主题表格是否已空（仅提示，不自动删入口行——保留入口防主题分裂）
            print(f"  主题「{t}」表格已更新（若该主题已无精炼产出，入口行建议保留或加注「仅 raw 层」）")
        return

    # ---- --add ----
    full = os.path.abspath(args.add)
    if not os.path.isfile(full):
        print(f"错误：文件不存在 {full}"); sys.exit(1)
    root_norm = os.path.normcase(os.path.normpath(root))
    if os.path.normcase(os.path.normpath(full)) != root_norm and not os.path.normcase(os.path.normpath(full)).startswith(root_norm + os.sep):
        print(f"错误：文件不在知识库内（{full}）"); sys.exit(1)
    rel = os.path.relpath(full, root).replace("\\", "/")
    if rel.startswith("raw/") or "/raw/" in rel or rel == "raw":
        print(f"错误：raw 层不列索引（分类规范：索引只列精炼层）——{rel}"); sys.exit(1)
    if rel.count("/") == 0:
        print(f"错误：文件无主题归属（应位于 {root}/{{主题}}/ 下）"); sys.exit(1)

    fname = os.path.basename(full)
    # 防重复：跨主题查同名条目
    for ln in lines:
        if is_row_line(ln) and re.match(r"^\|\s*\[" + re.escape(fname) + r"\]\(", ln.strip()):
            print(f"提示：索引已存在该文件条目：{ln.strip()}\n如需更新路径请先 --remove 再 --add")
            sys.exit(0)

    topic = args.topic or rel.split("/")[0]
    type_ = args.type or infer_type(fname) or "学习笔记"
    source = args.source or read_frontmatter_value(full, "来源") or "（未标注）"
    timeliness = args.timeliness or read_frontmatter_value(full, "时效性") or "（未标注）"
    if type_ not in REFINED_TYPES:
        print(f"警告：类型「{type_}」不在白名单 {REFINED_TYPES}（按规范应为 学习笔记/方法建议书）")
    if timeliness not in ("长期", "中期", "短期", "（未标注）"):
        print(f"警告：时效性「{timeliness}」应在 长期/中期/短期 之一")

    row = f"| [{fname}]({rel}) | {type_} | {source} | {timeliness} |"
    new_lines, msg = add_entry(lines, topic, row, args.desc)
    text = "\n".join(new_lines).rstrip("\n") + "\n"
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✔ {msg}")
    print(f"  条目: {row}")
    print(f"  （文件: {full}）")


if __name__ == "__main__":
    main()
