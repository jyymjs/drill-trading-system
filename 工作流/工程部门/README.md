# Harness Engineering 工程框架

> 通用 AI 工程化基础设施。所有 `deepseek/` 下的项目自动继承本框架。

**框架入口**：[`CLAUDE.md`](CLAUDE.md)（角色/核心原则/使用方式——以它为准）
**角色定义**：`流程/规划/执行/测试/`（planner/executor/reviewer）；优化部门职能已并入主对话（2026-08-05）
**规则**：`支撑/规则/`（00-core 核心规则，含 R5 确认闸门；archived/ 存历史案例）
**技能**：`.claude/skills/`（各部门 symlink 链接到 `技能库/`）
**审核标准**：`流程/测试/reviewer-criteria.md`（质检部六轴唯一事实源）

## 快速开始

```bash
cd 工作流/工程部门
# 直接说出需求，主对话（助理）自动响应 → 拷问需求 → 拆解任务 → 派发执行
```

新项目通过 `支撑/模板/project-init.sh` 完整继承本框架：CLAUDE.md 模板、全部规则、角色定义、门禁脚本与技能。

## 目录结构

```
.
├── .claude/             # Claude Code 配置（settings、hooks）
├── .internal/           # 运行期占位（snapshots/tasks）
├── 流程/                # 角色定义
│   ├── 规划/planner.md      # 规划（并入主对话助理）
│   ├── 执行/executor.md     # 工程部（编码施工）
│   └── 测试/reviewer.md     # 质检部（六轴验收）
│       └── reviewer-criteria.md  # 六轴检查标准
├── 支撑/                # 支撑资产
│   ├── 规则/00-core~05-architecture.md  # 核心规则
│   ├── 脚本/guard/      # 提审预检 + 框架自检脚本
│   ├── 文档/            # changelog/audit/plans/tasks/reports
│   └── 模板/            # project-CLAUDE.md 等
├── CLAUDE.md            # 框架入口（权威）
└── README.md            # 本文件（入口索引）
```

## 使用流程

1. 直接说需求 → 2. 回答业务拷问 → 3. 确认规划确认书 → 4. 等待完成

详细规则见 `支撑/规则/`，角色定义见 `流程/`，用法以 `CLAUDE.md` 为准。
