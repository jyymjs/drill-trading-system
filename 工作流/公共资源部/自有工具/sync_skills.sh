#!/bin/bash
# 技能部署同步（R-078 2026-08-13 · R-034 机制自动化）
# 用途: 技能库/（唯一源）→ 交易部门/.claude/skills/（scoped 部署副本）全量覆盖同步
# 用法: bash 公共资源部/自有工具/sync_skills.sh
# 说明: 改源后必须跑本脚本（否则两处漂移——R-034 教训：改源不同步曾致副本引用旧路径）
# 验证: 同步后 md5 一致性自动校验，失败退出码非 0

cd "$(dirname "$0")/../.."   # 公共资源部/自有工具 → 工作流/
SRC="技能库"
DST="交易部门/.claude/skills"

if [ ! -d "$SRC" ]; then
    echo "❌ 技能库/ 不存在（唯一源缺失）"
    exit 1
fi
mkdir -p "$DST"
# 全量覆盖同步（rsync 不可用则 cp）
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$SRC/" "$DST/"
else
    rm -rf "$DST" && cp -r "$SRC" "$DST"
fi
# md5 一致性校验
DIFF=$(diff -rq "$SRC" "$DST" 2>/dev/null | wc -l)
if [ "$DIFF" -eq 0 ]; then
    echo "✅ 技能同步完成：$SRC → $DST（$(ls "$SRC" | wc -l) 技能，md5 一致）"
else
    echo "⚠️ 同步后仍有 $DIFF 处差异——请检查"
    exit 2
fi
