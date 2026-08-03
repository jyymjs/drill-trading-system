# 框架变更日志

> 优化者/规划者对框架的每次变更记录于此。按时间倒序。

## 模板

```markdown
## YYYY-MM-DD：变更标题
- **触发**：自动回顾 / /优化 / /深度优化 / 规则提炼
- **变更**：改了什么（文件+要点）
- **原因**：为什么改（退回复盘/需求/用户指令）
```

---

## 2026-08-01：优化者机制优化（自动唤醒修复 + 提案确认流程）

- **触发**：用户指令（规划确认书 2026-08-01-优化者机制优化 v1.0，已签字）
- **变更**：
  - 自动唤醒机器兜底：新增 scripts/guard/task-counter.sh 并挂 Stop hook——任务计数到 5 且冷却期到期自动提醒，不再依赖口头 +1
  - 退回登记链路打通：reviewer.md 补退回登记职责；planner 退回通知时登记 regression-log.md
  - 冷却期时间戳落盘 docs/audit/last-optimization.txt；健康分三档绑定行动（🔴 间隔 3+强制深审 / 🟡 优先清退化）
  - 新功能「提案确认」：优化者高风险变更（规则/角色/技能/脚本）先推方案、用户确认后才执行；低风险直做标注可回滚（按风险分级）
  - validate-harness.sh 技能数动态计算 + 角色技能解析修复（技能名后括号注释被切碎误报，GNU sed 多字节字符类不匹配全角括号，改按行首 ASCII 技能名提取）；rules/optimization/ 补用途 README
- **原因**：优化者执行链路三处断裂修复 + 用户要求的确认环节（确认书签字后执行）

---

## 2026-08-01：Agent 技能设定打磨（四角色 × 技能归属重排 + 官方技能引入）

- **触发**：用户指令（联网调研后决策）
- **变更**：
  - 技能-角色匹配度全量分析：删除 4 个无职责落点技能（qa、edit-article、prototype、git-guardrails-claude-code），20→16
  - 引入 2 个官方技能（anthropics/skills，Apache-2.0）：skill-creator（技能创建+evals 评测，挂优化者）、webapp-testing（with_server.py 服务器管理+自动化回归，挂审查者）→ 16+2=18
  - 角色归属重排：planner +teach/-codebase-design、executor -凑数技能 +codebase-design、reviewer -qa/grilling/git-guardrails +webapp-testing、optimizer +writing-great-skills/+skill-creator/-grilling/-research
  - executor 补职责步骤：合并回主分支、业务语言文档
  - 技能重写：planner（执行细则+模板，消除与角色文档重复）、playwright（扩展环境验证三分支+Reconnaissance 模式）、research（框架集成+输出模板+grilling 边界）
  - validate-harness.sh：ROLE_SKILLS 改为从 agents/*.md 自动解析（agents 文档为唯一事实源），技能数 18
- **原因**：技能设定打磨；联网调研确认无值得替换现有技能的外部方案（superpowers 与本框架同构，不引入）

---

## 2026-08-01：语言规则收窄 + 5 项低风险打磨

- **触发**：用户指令
- **变更**：
  - 全局与框架语言规则收窄：仅「与用户交流」和「模型思考」用中文；代码注释、文档、技能文件不再强制中文
  - edit-article/SKILL.md 补完（原截断于 2a：新增去重、依赖顺序、全文通读、输出标准）
  - 三处 ≥3 阈值语义区分（executor 执行失败 / planner 方案修订 / reviewer 同类偏差）
  - resolving-merge-conflicts 验证改用 `git grep`（不误报 node_modules）
  - git-guardrails 补 jq 前置依赖说明
- **原因**：用户要求；打磨项执行

---

## 2026-08-01：框架审查修复批 1-3

- **触发**：用户发起框架审查（2 个分身审查 + 计划批准）
- **变更**：
  - CLAUDE.md 审查者五轴→六轴（补环境验证轴）
  - gatekeeper.sh 修复 eslint 失败被放行、Python/测试检测恒真两个 bug
  - code-review/parallel-agent 删除死链与虚构能力
  - 新建 rules/reviewer-criteria.md（六轴标准独立存放）、docs/changelog.md
  - 废弃 ubiquitous-language（统一到 domain-modeling 的 CONTEXT.md）、grill-with-docs（合并入 grilling）
  - rules/01-language.md 补充技能文件英文豁免；05-architecture.md 通用化
  - project-init.sh 完整交付 rules/agents/scripts；README/模板目录名修正
- **原因**：框架首次全面审查，修复死链、矛盾与门禁 bug
