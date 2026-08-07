#!/bin/bash
# 总助一键备份（R-033 改版 2026-08-07：输出移仓库外，覆盖保留资产）
# 用法: bash 总理/脚本/备份.sh
# 输出: Desktop/deepseek/备份/工作流全量-YYYYMMDD.tar（含全部资产）
# 历史: git tag 2026-08-07-before-restructure + git bundle 已存（20260807）
# 恢复: tar -xf 备份/工作流全量-YYYYMMDD.tar -C 工作流/ 根目录

cd "$(dirname "$0")/../.."   # 工作流/
STAMP=$(date +%Y%m%d)
OUT="../备份"                # Desktop/deepseek/备份/（仓库外，不入库）
mkdir -p "$OUT"

# 全量打包（排除 .git 与不入库大目录：第三方引擎 901M 可重下）
tar --exclude='.git' \
    --exclude='公共资源部/第三方引擎' \
    --exclude='临时' \
    -cf "$OUT/工作流全量-$STAMP.tar" .
echo "✅ 全量备份: $OUT/工作流全量-$STAMP.tar"

# AI 侧记忆备份
tar -czf "$OUT/ai-memory-$STAMP.tar.gz" \
  -C "$HOME/.claude/projects/c--Users-32032-Desktop-deepseek" memory 2>/dev/null \
  && echo "✅ AI 记忆备份: $OUT/ai-memory-$STAMP.tar.gz"
