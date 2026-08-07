#!/usr/bin/env python3
"""知识差距扫描（R-036④ · 2026-08-09）

对照规格书第 11 章出处索引：检查每类规则是否有知识库出处。
输出「有出处 / 无出处」清单——无出处的规则 = 知识差距（规则无理论源头，
回溯时断链），提示补知识卡或标注"工程定案"。

使用：python 工具链/脚本/knowledge_gap_scan.py [--spec 规格书路径] [--json 输出]
"""
import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SPEC = ROOT / "策略" / "核心策略" / "策略规格书.md"
KNOWLEDGE_DIR = ROOT / "策略" / "知识库"

# 出处索引表（规格书第 11 章）：规则名 → 出处（知识库路径）
SOURCE_INDEX_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")


def scan_gaps(spec_path: str | Path = DEFAULT_SPEC) -> dict:
    spec = Path(spec_path).read_text(encoding="utf-8")
    # 取第 11 章（出处索引）
    idx = spec.find("## 11")
    if idx == -1:
        return {"error": "规格书未找到第 11 章（出处索引）"}
    chapter = spec[idx:]
    rules = []
    for line in chapter.splitlines():
        m = SOURCE_INDEX_RE.match(line.strip())
        if m and m.group(1) not in ("规则",) and "---" not in m.group(1):
            rule, source = m.group(1).strip(), m.group(2).strip()
            rules.append({"rule": rule, "source": source})

    # 知识库目录结构（出处是否可定位）
    knowledge_dirs = {p.name for p in KNOWLEDGE_DIR.iterdir() if p.is_dir()}
    gaps = []
    for r in rules:
        src = r["source"]
        located = (any(k in src for k in knowledge_dirs) or "知识卡" in src
                   or "周会" in src or "内训" in src or "课程" in src or "录屏" in src)
        r["located"] = located
        if not located:
            gaps.append(r)

    return {"total_rules": len(rules), "located": sum(1 for r in rules if r["located"]),
            "gaps": gaps, "rules": rules}


def render(stats: dict) -> str:
    out = ["知识差距扫描（规格书出处可追溯性 · R-036④）", "-" * 60]
    out.append(f"规格书出处索引规则: {stats.get('total_rules', 0)} 条 | "
               f"有出处: {stats.get('located', 0)} | 无出处(差距): {len(stats.get('gaps', []))}")
    for g in stats.get("gaps", []):
        out.append(f"  ⚠️ {g['rule']} ← 出处「{g['source']}」在知识库无法定位")
    if not stats.get("gaps"):
        out.append("  ✅ 全部规则有知识库出处（可追溯）")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    stats = scan_gaps(args.spec)
    print(render(stats))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(stats, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
