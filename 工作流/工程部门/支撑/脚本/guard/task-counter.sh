#!/bin/bash
# task-counter.sh — 优化者唤醒提醒（Stop hook 触发）
# 条件：任务计数 >= 5 且距上次优化 >= 24h → 输出提醒文本；否则静默退出（无输出）
COUNT_FILE="$(cd "$(dirname "$0")" && pwd)/../../../支撑/文档/.task-count"
LAST_OPT="$(cd "$(dirname "$0")" && pwd)/../../../支撑/文档/audit/last-optimization.txt"
COOLDOWN_HOURS=24

[ -f "$COUNT_FILE" ] || exit 0
COUNT=$(cat "$COUNT_FILE")
[ "$COUNT" -ge 5 ] || exit 0

# 冷却期检查：距上次优化不足 24h 则静默（文件不存在视为到期）
if [ -f "$LAST_OPT" ]; then
  LAST=$(cat "$LAST_OPT" 2>/dev/null | tr -d ' ')
  NOW=$(date +%s)
  DIFF=$(( (NOW - LAST) / 3600 ))
  [ "$DIFF" -ge "$COOLDOWN_HOURS" ] || exit 0
fi

echo ""
echo "⚠️  优化者待唤醒：任务计数已达 $COUNT/5 且冷却期已过。"
echo "    回复「唤醒优化者」或输入 /优化 启动审计。"
