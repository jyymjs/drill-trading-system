#!/bin/bash
# 助理大脑一键备份（借鉴 HanaAgent "Agent 就是文件夹" 理念）
# 用法: bash scripts/backup.sh
# 输出: 归档/备份/助理大脑-YYYYMMDD.tar.gz
# 恢复: tar -xzf 归档/备份/助理大脑-YYYYMMDD.tar.gz （解压回助理系统根目录即可）

cd "$(dirname "$0")/.."
mkdir -p 归档/备份
stamp=$(date +%Y%m%d)
tar -czf "归档/备份/助理大脑-$stamp.tar.gz" \
  助理规则.md MEMORY.md 档案 时间线 卡片 工作流 工作区/需求台账.md 2>/dev/null
echo "✅ 备份完成: 归档/备份/助理大脑-$stamp.tar.gz"
echo "   （含：规则/索引/全部档案/时间线/卡片/工作流/需求台账）"
