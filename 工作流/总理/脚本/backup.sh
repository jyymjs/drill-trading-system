#!/bin/bash
# 助理大脑一键备份（借鉴 HanaAgent "Agent 就是文件夹" 理念）
# 用法: bash scripts/backup.sh
# 输出: 档案室/总理/归档/备份/助理大脑-YYYYMMDD.tar.gz + ai-memory-YYYYMMDD.tar.gz
# 恢复: tar -xzf 档案室/总理/归档/备份/助理大脑-YYYYMMDD.tar.gz （解压回 工作流/ 根目录即可）
# 2026-08-05 更新：档案资产已迁至 档案室/总理/（R-010 重组），备份源/输出目录同步

cd "$(dirname "$0")/.."
mkdir -p ../档案室/总理/归档/备份
stamp=$(date +%Y%m%d)
tar -czf "../档案室/总理/归档/备份/助理大脑-$stamp.tar.gz" \
  助理规则.md MEMORY.md ../档案室/总理/档案 ../档案室/总理/时间线 ../档案室/总理/卡片 工作流 工作区/需求台账.md 2>/dev/null
echo "✅ 备份完成: 档案室/总理/归档/备份/助理大脑-$stamp.tar.gz"
echo "   （含：规则/索引/全部档案/时间线/卡片/工作流/需求台账）"

# AI 侧记忆备份（2026-08-04 新增：记忆清理机制兜底）
tar -czf "../档案室/总理/归档/备份/ai-memory-$stamp.tar.gz" \
  -C "$HOME/.claude/projects/c--Users-32032-Desktop-deepseek" memory 2>/dev/null \
  && echo "✅ AI 记忆备份: 档案室/总理/归档/备份/ai-memory-$stamp.tar.gz"
