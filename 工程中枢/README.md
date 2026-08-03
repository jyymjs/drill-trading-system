# Harness Engineering 工程框架

> 通用 AI 工程化基础设施。所有 `deepseek/` 下的项目自动继承本框架。

## 四个角色

| 角色 | 位置 | 职责 |
|------|------|------|
| **规划部** | 主对话 | 入口 + 调度中枢。拷问需求、拆解任务、派发/调度工程部/质检部/内审部 |
| **工程部** | 子 agent | 编码实现。在独立 worktree 中完成一个子任务，自跑预检后提审 |
| **质检部** | 子 agent | 六轴审查：代码质量、业务验收、计划一致性、架构合规、元审查、环境验证 |
| **内审部** | 子 agent | 每 5 任务唤醒一次。审计规则、技能、角色定义，出模式分析报告 |

## 核心原则

- **Human steers, Agent executes** — 人掌舵，Agent 干活
- **规则来自失败** — Agent 犯错才加规则，不写预防性规则
- **脚本优于提示** — 能用代码检查就别用自然语言约束

## 快速开始

```bash
# 1. 在 Claude Code 中打开项目目录
cd 工程中枢

# 2. 直接说出你的需求
# 规划部会自动响应 → 拷问需求 → 拆解任务 → 派发执行
```

所有 `deepseek/` 下的新项目通过 `templates/project-init.sh` 完整继承本框架：CLAUDE.md 模板、全部规则（rules/）、角色定义（agents/）、门禁脚本（scripts/guard/）与技能（.claude/skills/）。

## 目录结构

```
.
├── .claude/             # Claude Code 配置
│   ├── settings.json    # 自定义命令（/优化、/深度优化）
│   ├── skills/          # 技能库（框架工作流技能 + 通用可复用技能）
│   └── hooks/           # hooks（预留）
├── .internal/           # 技术附录（规划部生成的实现细节）
├── agents/              # 四个角色的定义文件
│   ├── planner.md
│   ├── executor.md
│   ├── reviewer.md
│   └── optimizer.md
├── rules/               # 核心规则（00-core 到 05-architecture）
│   ├── reviewer-criteria.md  # 质检部六轴检查标准（内审部只读）
│   ├── archived/        # 已归档规则
│   └── optimization/
├── scripts/             # 自动化脚本
│   └── guard/           # 提审预检 + 框架自检
├── docs/                # 运行文档
│   ├── plans/           # 规划确认书
│   ├── tasks/           # 任务记录
│   ├── reports/         # 报告
│   ├── audit/           # 审计记录（regression-log.md 退回记录）
│   ├── preferences/     # 偏好积累
│   ├── changelog.md     # 框架变更日志
│   └── .task-count      # 任务计数（运行时状态，已 gitignore）
├── templates/           # 模板
├── CLAUDE.md            # 框架入口
└── README.md            # 本文件
```

## 使用流程

1. 在 Claude Code 中直接说出需求
2. 回答规划部的业务拷问
3. 确认规划确认书（纯业务语言，不含代码）
4. 规划部自动派发任务，等待完成

详细规则见 `rules/`，角色定义见 `agents/`。
