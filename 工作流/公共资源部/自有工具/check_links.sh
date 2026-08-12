#!/bin/bash
# 索引链接断链检查（R-078 2026-08-13 · 验收工具）
# 用途: 扫 CLAUDE.md/MEMORY/注册表/README 中的相对链接，验证目标存在
# 用法: bash 公共资源部/自有工具/check_links.sh [文件...]（缺省 = 自动扫核心索引）
# 输出: 断链列表 + 统计；退出码 0 = 无断链

cd "$(dirname "$0")/../.."   # 工作流/
FILES="${@:-CLAUDE.md 总理/CLAUDE.md 交易部门/CLAUDE.md 公共资源部/CLAUDE.md 总理/MEMORY.md 总理/机制/注册表.md 交易部门/README.md 总理/助理规则.md}"

total=0; broken=0
for f in $FILES; do
    [ -f "$f" ] || { echo "⚠️ 索引文件本身缺失: $f"; broken=$((broken+1)); continue; }
    # 提取 markdown 链接 [text](path) 与反引号路径 `path`
    while IFS= read -r ln; do
        [ -z "$ln" ] && continue
        # 跳过 http/绝对路径/锚点
        case "$ln" in http*|https*|/*|C:*) continue;; esac
        # 去掉锚点和参数
        target="${ln%%#*}"
        # 相对路径解析（相对索引文件所在目录）
        dir=$(dirname "$f")
        if [ -e "$dir/$target" ] || [ -e "$target" ]; then :; else
            echo "🔴 断链: $f → $target"
            broken=$((broken+1))
        fi
        total=$((total+1))
    done < <(grep -oE '\]\([^)]+\)|`[^`]+\.(md|py|sh|csv|json|png|html)`' "$f" 2>/dev/null | sed -E 's/^\]\(//; s/\)$//; s/^`//; s/`$//' | grep -v '^bash ') || true
done
echo "📊 链接总数 $total ｜ 断链 $broken"
[ "$broken" -eq 0 ]
