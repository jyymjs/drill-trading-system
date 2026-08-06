#!/bin/bash
# 框架自检.sh — 框架完整性自检（2026-08-04 重组后 SCRIPT_DIR 定位）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="$SCRIPT_DIR/../../.."
echo "🔍 === 框架自检 ==="

FAIL=0
check() { if [ -e "$1" ]; then echo "  ✅ $1"; else echo "  ❌ 缺失: $1"; FAIL=1; fi }

echo "--- 核心文件 ---"
check "$BASE/CLAUDE.md"
check "$BASE/.claude/settings.json"
check "$BASE/.gitignore"

echo "--- 规则 ---"
check "$BASE/支撑/规则/00-核心规则.md"
check "$BASE/支撑/规则/01-语言规则.md"
check "$BASE/支撑/规则/02-Git规范.md"
check "$BASE/支撑/规则/03-安全红线.md"
check "$BASE/支撑/规则/04-质量准则.md"
check "$BASE/支撑/规则/05-架构规则.md"
check "$BASE/流程/测试/质检标准.md"

echo "--- 角色定义 ---"
check "$BASE/流程/规划/规划.md"
check "$BASE/流程/执行/执行.md"
check "$BASE/流程/测试/质检.md"

echo "--- 优化部门 ---"
check "$BASE/../优化部门/CLAUDE.md"
check "$BASE/../优化部门/问题台账.md"
check "$BASE/../优化部门/审计流程.md"

echo "--- 门禁脚本 ---"
check "$BASE/支撑/脚本/门禁/门禁预检.sh"
check "$BASE/支撑/脚本/门禁/框架自检.sh"
check "$BASE/支撑/脚本/门禁/任务计数.sh"

echo "--- 模板与运行文档 ---"
check "$BASE/支撑/模板/项目模板-CLAUDE.md"
check "$BASE/支撑/模板/技能模板-SKILL.md"
check "$BASE/支撑/模板/项目初始化.sh"
check "$BASE/支撑/文档/审计记录/regression-log.md"
check "$BASE/支撑/文档/审计记录/last-optimization.txt"
check "$BASE/支撑/文档/变更日志.md"

echo "--- 技能完整性 ---"
SKILL_DIR="$BASE/.claude/skills"
EXPECTED_COUNT=$(ls -d "$SKILL_DIR"/*/ 2>/dev/null | wc -l | tr -d ' ')
ACTUAL_COUNT=0
MISSING_ROLE_SKILLS=""
for d in "$SKILL_DIR"/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  if [ -f "$d/SKILL.md" ]; then
    ACTUAL_COUNT=$((ACTUAL_COUNT + 1))
  else
    echo "  ❌ $name 缺 SKILL.md"
    FAIL=1
  fi
done
if [ "$ACTUAL_COUNT" -eq "$EXPECTED_COUNT" ]; then
  echo "  ✅ 全部 $EXPECTED_COUNT 个技能就位"
else
  echo "  ❌ 技能数: $ACTUAL_COUNT / $EXPECTED_COUNT"
  FAIL=1
fi

echo "--- 角色引用技能验证 ---"
# 自动解析四个角色目录 md 的"核心：/辅助："行提取技能清单——角色文档为唯一事实源
ROLE_SKILLS=""
for f in "$BASE"/流程/规划/*.md "$BASE"/流程/执行/*.md "$BASE"/流程/测试/*.md "$BASE"/../优化部门/*.md; do
  [ -f "$f" ] || continue
  role_skills=$(grep -oE "^(核心|辅助)：.*" "$f" | sed 's/^[^：]*：//' | tr '、' '\n' | grep -oE '^[a-z][a-z-]*')
  ROLE_SKILLS="$ROLE_SKILLS $role_skills"
done
MISSING_COUNT=0
for skill in $ROLE_SKILLS; do
  if [ ! -d "$SKILL_DIR/$skill" ]; then
    echo "  ❌ 角色引用了不存在的技能: $skill"
    FAIL=1
    MISSING_COUNT=$((MISSING_COUNT + 1))
  fi
done
# 检查是否有技能未被任何角色引用
for d in "$SKILL_DIR"/*/; do
  [ -d "$d" ] || continue
  skill=$(basename "$d")
  found=0
  for rs in $ROLE_SKILLS; do
    [ "$rs" = "$skill" ] && found=1 && break
  done
  if [ "$found" -eq 0 ]; then
    echo "  ⚠ 技能未被任何角色引用: $skill"
  fi
done
if [ "$MISSING_COUNT" -eq 0 ]; then
  echo "  ✅ 所有角色引用技能均存在"
fi

echo "--- 空目录检查 ---"
EMPTY_DIRS=$(find "$BASE" -type d -empty 2>/dev/null | grep -v '.git' | head -10)
if [ -n "$EMPTY_DIRS" ]; then
  echo "  ⚠ 存在空目录（可能缺 .gitkeep）:"
  echo "$EMPTY_DIRS" | while read d; do echo "    $d"; done
else
  echo "  ✅ 无空目录"
fi

echo "--- 残留检测 ---"
OPENAI_COUNT=$(find "$BASE" -name "openai.yaml" 2>/dev/null | wc -l)
if [ "$OPENAI_COUNT" -gt 0 ]; then
  echo "  ❌ 残留 $OPENAI_COUNT 个 openai.yaml"
  FAIL=1
else
  echo "  ✅ 无 openai.yaml 残留"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "✅ 框架完整 ($ACTUAL_COUNT 技能, 3 角色+优化部门, 6 规则)"
else
  echo "❌ 存在 $FAIL 项问题，请修复"
  exit 1
fi
