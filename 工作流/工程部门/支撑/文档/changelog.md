# 框架变更日志

> 优化部门/规划部对框架的每次变更记录于此。按时间倒序。

## 模板

```markdown
## YYYY-MM-DD：变更标题
- **触发**：自动回顾 / /优化 / /深度优化 / 规则提炼
- **变更**：改了什么（文件+要点）
- **原因**：为什么改（退回复盘/需求/用户指令）
```

---

## 2026-08-04：R-007 内审部升级为优化部门（一级部门）

- **触发**：老板拍板（2026-08-04）——内审存在感弱、问题散落无收口、缺升级动力
- **变更**：
  - 新建 `优化部门/`：CLAUDE.md（四职责：稳定/收口/告知/建议）+ 问题台账.md（分级收口，首条 001-004）+ 审计流程.md（原 optimizer.md 迁入改造）
  - 工程部门：四角色→三角色（规划/执行/测试），`流程/内审/` 目录移除；CLAUDE.md/README/组织架构 同步
  - 根导航表新增优化部门行；总理/组织架构.md 组织图+表格+协作流程同步（含 R-006 遗漏的"总理审阅"清理）
  - 老名联动：settings.json hook、task-counter.sh、gatekeeper.sh、project-init.sh、regression-log.md 全部"优化者/审查者/执行者"→新名
  - 台账职责生效：分级告知（紧急立即/普通收尾/长期周报）+ 问题附优化建议
- **原因**：R-007 确认书 v1.1 已签

## 2026-08-04：R-006 总理角色并入主对话（取消助理中转）

- **触发**：用户指令（2026-08-04 拍板：主对话合并业务+工程双职能，中转层无必要）
- **变更**：
  - 取消"助理中转"：框架产出直接呈报用户；`工程部门/CLAUDE.md` 新增「呈报对象」节
  - `总理/CLAUDE.md` 定位改写：独立助理角色 → 主对话直属业务模块（档案/工作区/技能不变）
  - 口径同步：工作流/CLAUDE.md 导航表、组织架构.md、README.md、planner.md（9 处）、optimizer.md、00-core.md、提案机制.md、助理规则.md
  - butler 技能：描述更新 + 修正过时路径（助理系统/ → 工作流/总理/）+ 开发需求不再经转交单
  - 记忆分工：AI 侧 user-profile-motivation 去重（身份细节以总理身份档案为权威源）
- **原因**：主对话已具备助理全部能力，中转层=自己审自己，且造成记忆双写不一致（R-006 确认书已签）

## 2026-08-04：功能域父文件夹分组 + 资源调动优化

- **触发**：用户指令（2026-08-04 拍板：文件夹按功能父组整合 + 角色资源按需调动）
- **变更**：
  - **交易部门四父组**：分析决策/（分析+风控+跟踪）、数据基础/（数据+配置）、工具链/（工具+脚本）、产出/（输出+临时）；策略/项目/测试保持；清理 data/output/__pycache__ 空壳；Python import 全部加父组前缀（from 分析决策.分析.scanner 等），实测全模块导入通过
  - **工程部门两组**：流程/（规划/执行/测试/内审）+ 支撑/（规则/脚本/文档/模板）；门禁脚本（validate-harness/task-counter）BASE 路径修正、settings.json hook 路径修正，自检通过
  - **公共资源部分组**：自有工具/（工具/B站下载/视频管线）+ 第三方引擎/（whisper 系列）
  - **资源调动优化**：四角色文件"自我提升（提案机制）"低频节提取为共享 `工程部门/提案机制.md`（单一来源，角色文件合计 14.5KB→11KB 省 24%）；`工作流/CLAUDE.md` 新增「资源按需原则」纪律（不主动全量读/指针式引用/技能按需/先查清单）
- **原因**：用户要求避免角色随便问点东西就输入一堆资源；部门内零散文件按功能父组整合

## 2026-08-04：全工作区功能重组（工作流/ 总根 + 部门中文命名 + 档案/资料分离）

- **触发**：用户指令（2026-08-04 拍板：框架文件彻底重组，按功能归类、英文夹改中文、档案集中）
- **变更**：
  - **工作流/ 成为总根**：原 总理/工程中枢/总裁办/公共服务部/学习部 全部移入 工作流/；交易部留根下但整体迁入 工作流/交易部门/；根目录仅剩 .git/.claude/工作流
  - **部门更名**：工程中枢→工程部门、总裁办→总理（档案分离）、交易部→交易部门、公共服务部→公共资源部
  - **工程部门内部（方案A）**：agents→规划/执行/测试/内审（按角色分）+ rules→规则/、scripts→脚本/、docs→文档/、templates→模板/、组织架构.md 单一来源
  - **交易部门内部**：backtest→项目/回测系统/、strategy→策略/核心策略/、知识库→策略/知识库/、analysis→分析/ 等全中文命名；docs/journal/memory 档案归档案室
  - **公共资源部**：tools→工具/、bilibili_dl→B站下载/、video_pipeline→视频管线/、test_transcribe 清理删除（73MB）；whisper 系列不改名（第三方源码树）
  - **学习部**：agents→角色/、scripts→脚本/、docs→文档/、tmp_demo→临时演示/
  - **档案室/**：全部门档案集中——总理（原总裁办）档案/时间线/卡片/归档 + 交易部门 文档/日志/记忆，按部门子目录分类
  - **资料库/**：工程部门/文档/plans 确认书 3 份 + 技术附录按时间命名移入（2026-08-01/04-xxx.md），按需调用不自动加载
  - **技能库**：总理/技能库 → 工作流/技能库，8 处 junction 重指向（根级/工程部门/交易部门/公共资源部/学习部）
  - **引用修复**：butler 技能路径→工作流\总理；工作流/CLAUDE.md @import→./工程部门/CLAUDE.md；四角色文件/各部门 CLAUDE.md/组织架构段全部更新为新路径；门禁脚本（validate-harness/task-counter）改 SCRIPT_DIR 定位
  - **清理**：根目录 .playwright-mcp/、_screen_check.png、工程部门/脚本/bilibili 残留
- **原因**：用户彻底整理框架文件（除项目外一切按功能归类，英文夹改中文，档案统一集中防重复）

## 2026-08-04：总理/ 目录重组（配置与技能集中 + 删除两配置）

- **触发**：用户指令（框架文件彻底整理，2026-08-04 拍板）
- **变更**：
  - 新建 `总理/` 根级目录：集中根级配置与技能库
  - 根 `CLAUDE.md` 移至 `总理/CLAUDE.md`（import 链改 `@../工程中枢/CLAUDE.md`；根目录启动不再加载根级指令，总理/ 目录内对话加载）
  - 技能库迁移：`工程中枢/.claude/skills/` 全部 19 技能 + learn 链接 → `总理/技能库/`；根级/工程中枢/交易部/公共服务部 `.claude/skills` 与学习部 4 个技能链接改为 Windows junction → `总理/技能库`（单一来源，8 处可达）
  - 删除 `deepseek/.claude/settings.local.json`（权限配置——回到默认确认模式）
  - 删除 `deepseek/.gitignore`（git 杂物保护消失）
- **原因**：用户彻底整理框架文件，集中配置于"总理"目录

## 2026-08-04：框架优化建议书落地（token 减负 + 结构治理）

- **触发**：用户指令（建议书 `docs/plans/2026-08-04-框架优化建议书.md` 已确认 2026-08-04）
- **变更**：
  - 技能描述批量瘦身：9 项超长描述压至 ≤80 字（learn ~100 字、skill-creator/code-review 等），butler 描述修正"助理系统"→"总裁办"路径；技能清单注入从 6-8KB → 3.9KB
  - skill-creator 拆解：SKILL.md 33,168B → 12,112B（写作指南外移 references/writing-guide.md，内联 JSON 引用 schemas.md，平台段压缩）
  - 00-core.md：R5 失败案例移 rules/archived/2026-08-confirmation-lessons.md（规则正文全保留），删 R8 取消条款
  - agents 组织架构段单一来源化：新建 agents/_organization.md（修正"资料库共享"旧表述），planner/executor/reviewer/optimizer 四文件改引用
  - 助理规则.md：26,606B → 20,513B（决策分级/确认规则/黑名单保真；战略目标机制外移 工作流/战略目标机制.md；R8 取消同步标注）
  - 5 处空"资料库/"删除；_screen_check.png 出库；.playwright-mcp 清理
  - 技能单一来源化：git index 清除 144 项穿 symlink 的技能文件记录，根/交易部/公共服务部 .claude/skills 均为 symlink → 工程中枢；交易部 .agents/skills 旧库 25 项备份为 .agents/skills.bak-20260804 后移除；.gitignore 追加 symlink 目录保护
  - 公共服务部 whisper.cpp 两子模块注册清除（.gitmodules 缺失的孤儿 gitlink），转普通目录 + gitignore 不入库
  - 记忆保守归档：latest-session-changes.md / 2026-07-30-config-optimization.md / web-ui-upgrade.md 移入各自 memory/归档/，双索引同步瘦身
  - 根 CLAUDE.md 补公共服务部节；工程中枢 README.md 改入口索引式
  - 长期机制固化：技能描述 ≤80 字约定（skill-creator 模板）、记忆入库压缩摘要规则（助理规则）、季度固定开销体检（rules/optimization/README.md）
- **原因**：token 消耗过大 + 架构优化（部门/角色/文件夹/记忆多维治理）；2026-08-04 老板确认建议书后执行

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
