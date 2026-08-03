# -*- coding: utf-8 -*-
"""给所有 agent 卡追加"公司组织架构"认知段（2026-08-03 老板拍板）"""
import io, os

ORG_SECTION = """

## 公司组织架构（2026-08-03 老板拍板·全员知晓）

- **总裁办**（助理系统/）：老板助理——需求确认/记忆中枢/部门协调/通俗翻译
- **工程中枢**（项目工程框架/）：规划部/工程部/质检部/内审部——方案设计/编码实现/技术验收/规则审计
- **交易部**（量化交易系统/）：策略研究/扫描/复盘/交易知识库（经理整理学习）
- **学习部**（高效学习系统/）：私人老师——老板个人学习/考问复盘/扫库查重（查交易部知识库矛盾→找交易部经理讨论）
- **公共服务部**：工具中枢——一切工具归此（README+工具清单），各部门搜索调用
- **协作方式**：需要部门支持 → 找对应部门经理；资料库全公司共享（各部门 `资料库/`）
"""

FILES = [
    r"量化交易系统\.claude\agents\trader.md",
    r"高效学习系统\agents\learner.md",
    r"项目工程框架\agents\planner.md",
    r"项目工程框架\agents\executor.md",
    r"项目工程框架\agents\reviewer.md",
    r"项目工程框架\agents\optimizer.md",
    r"公共服务部\.claude\agents\公共服务部经理.md",
]

for f in FILES:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", f)
    p = os.path.normpath(p)
    if not os.path.exists(p):
        print(f"缺失: {p}")
        continue
    with io.open(p, "r", encoding="utf-8") as fh:
        content = fh.read()
    if "公司组织架构" in content:
        print(f"已有: {os.path.basename(p)}")
        continue
    with io.open(p, "a", encoding="utf-8") as fh:
        fh.write(ORG_SECTION)
    print(f"已追加: {os.path.basename(p)}")
