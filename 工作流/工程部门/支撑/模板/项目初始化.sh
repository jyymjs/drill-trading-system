#!/bin/bash
# 项目初始化.sh — 新项目初始化
# 用法：在新项目目录下运行此脚本

FRAMEWORK="$(dirname "$0")/../.."
PROJECT="$(pwd)"

echo "🚀 初始化项目 Harness..."

# 创建目录骨架
mkdir -p .claude/skills .claude/hooks docs/tasks docs/plans docs/preferences

# 复制项目模板
cp "$FRAMEWORK/templates/项目模板-CLAUDE.md" "./CLAUDE.md"
echo "✅ CLAUDE.md"

# 复制规则与角色定义（新项目完整继承框架工作流）
cp -r "$FRAMEWORK/rules" "./rules"
echo "✅ rules/"
cp -r "$FRAMEWORK/agents" "./agents"
echo "✅ agents/"

# 复制门禁脚本（工程部提审预检 + 框架自检）
mkdir -p scripts/guard
cp "$FRAMEWORK/scripts/门禁/门禁预检.sh" "./scripts/门禁/门禁预检.sh"
echo "✅ 门禁预检.sh"
cp "$FRAMEWORK/scripts/门禁/框架自检.sh" "./scripts/门禁/框架自检.sh"
echo "✅ 框架自检.sh"

# 创建技能 symlink（优先 mklink /J，fallback 到复制）
for skill in "$FRAMEWORK/.claude/skills/"*/; do
    name=$(basename "$skill")
    target=".claude/skills/$name"
    if [ -d "$target" ]; then continue; fi
    if cmd.exe /c "mklink /J \"$(cygpath -w "$target")\" \"$(cygpath -w "$skill")\"" 2>/dev/null; then
        echo "✅ symlink: $name"
    else
        cp -r "$skill" "$target"
        echo "⚠ 已复制（无 symlink 权限）: $name"
    fi
done

echo "✅ 初始化完成。编辑 CLAUDE.md 填写项目信息。"
