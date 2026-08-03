#!/bin/bash
# gatekeeper.sh — 提审前预检
# 执行者在提交审查前自跑，失败不浪费审查者时间
# 自适应项目类型

set -e
echo "🔍 === 提审预检 ==="

# 1. Lint — 根据项目类型自适应
if [ -f "package.json" ]; then
  echo "1/2 Lint (Node.js)..."
  HAS_ESLINT=false
  if compgen -G ".eslintrc*" >/dev/null; then HAS_ESLINT=true; fi
  if compgen -G "eslint.config.*" >/dev/null; then HAS_ESLINT=true; fi
  if node -e "try{process.exit(require('./package.json').eslintConfig?0:1)}catch(e){process.exit(1)}" 2>/dev/null; then HAS_ESLINT=true; fi
  if [ "$HAS_ESLINT" = "true" ] && command -v npx &>/dev/null; then
    npx eslint --quiet . || { echo "❌ Lint"; exit 1; }
  else
    echo "⚠ 跳过（eslint 未配置）"
  fi
elif [ -f "pyproject.toml" ] || [ -f "setup.py" ] || compgen -G "*.py" >/dev/null; then
  echo "1/2 Lint (Python)..."
  if command -v ruff &>/dev/null; then
    ruff check . --quiet || { echo "❌ Lint"; exit 1; }
  else
    echo "⚠ 跳过（ruff 未安装）"
  fi
else
  echo "1/2 跳过 Lint（未检测到已知项目类型）"
fi

# 2. 测试
if [ -f "package.json" ]; then
  HAS_TEST=$(node -e "try{process.exit(require('./package.json').scripts.test?0:1)}catch(e){process.exit(1)}" 2>/dev/null && echo yes || echo no)
  if [ "$HAS_TEST" = "yes" ]; then
    echo "2/2 测试 (Node.js)..."
    npm test || { echo "❌ 测试"; exit 1; }
  else
    echo "2/2 跳过（无 npm test 脚本）"
  fi
elif [ -d "tests" ] || compgen -G "test_*.py" >/dev/null; then
  echo "2/2 测试 (Python)..."
  if command -v pytest &>/dev/null; then
    pytest --quiet --tb=short || { echo "❌ 测试"; exit 1; }
  else
    echo "⚠ 跳过（pytest 未安装）"
  fi
else
  echo "2/2 跳过（无测试目录/文件）"
fi

echo "✅ 预检通过"
