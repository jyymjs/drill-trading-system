# Harness Engineering 工程框架

> 通用 AI 工程化基础设施。所有 `deepseek/` 下的项目自动继承本框架。

**框架入口**：[`CLAUDE.md`](CLAUDE.md)（四个角色/核心原则/使用方式/呈报对象——以它为准）
**角色定义**：`规划/执行/测试/`（planner/executor/reviewer + 共享的 `_organization.md`）；优化部门见 `../优化部门/`（同级一级部门）
**规则**：`规则/`（00-core 核心规则，含 R5 确认闸门；archived/ 存历史案例）
**技能**：`.claude/skills/`（根级与各部门 symlink 链接到此）
**审核标准**：`规则/reviewer-criteria.md`（质检部六轴唯一事实源）

## 快速开始

```bash
cd 工作流/工程部门
# 直接说出需求，规划部自动响应 → 拷问需求 → 拆解任务 → 派发执行
```

新项目通过 `模板/project-init.sh` 完整继承本框架：CLAUDE.md 模板、全部规则、角色定义、门禁脚本与技能。

## 目录结构

```
.
├── .claude/             # Claude Code 配置（settings、skills、hooks 预留）
├── .internal/           # 技术附录（规划部生成的实现细节）
├── 规划/执行/测试/              # 三个角色的定义文件 + _organization.md（组织架构单一来源）
├── 规则/               # 核心规则（00-core 到 05-architecture）
│   ├── reviewer-criteria.md  # 质检部六轴检查标准（优化部门只读）
│   ├── archived/        # 已归档规则/历史案例
│   └── optimization/
├── 脚本/             # 自动化脚本（guard/ 提审预检 + 框架自检）
├── docs/                # 运行文档（plans/tasks/reports/audit/preferences/changelog）
├── 模板/           # 模板
├── CLAUDE.md            # 框架入口（权威）
└── README.md            # 本文件（入口索引）
```

## 使用流程

1. 直接说需求 → 2. 回答业务拷问 → 3. 确认规划确认书 → 4. 等待完成

详细规则见 `规则/`，角色定义见 `规划/执行/测试/内审/`，用法以 `CLAUDE.md` 为准。
