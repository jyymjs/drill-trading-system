---
name: skill-creator
description: 创建/修改/评测技能，含基准测试与描述优化。建新技能或优化现有技能时使用。
---

# Skill Creator

创建新技能并迭代改进。核心循环：确定目标 → 起草 → 造测试用例 → 并行跑 with-skill/baseline → 用户评估（定性+定量）→ 按反馈重写 → 循环到满意 → 扩大测试集再来。始终判断用户处于哪个阶段，直接跳入帮忙推进。用户说"不用评估，直接来"也可以。

## Communicating with the user

用户技术水平差异大：默认可用 "evaluation/benchmark"，"JSON/assertion" 须见用户有相应认知才用。不确定就简短解释。

---

## Creating a skill

### Capture Intent

先从当前对话提取用户意图（可能已含想固化的流程：工具、步骤、修正、输入输出格式），缺的让用户补，下一步前须用户确认：

1. 这个技能让 Claude 能做什么？
2. 何时触发？（用户什么话/场景）
3. 期望输出格式？
4. 要不要设测试用例？——客观可验证输出（文件转换/数据提取/代码生成/固定流程）受益于测试；主观输出（写作风格/艺术）通常不需要。按技能类型给建议默认，用户决定。

### Interview and Research

主动追问边界情况、输入输出格式、示例文件、成功标准、依赖。写完测试提示词之前先敲定这些。若 MCP 可用于调研（查文档/相似技能/最佳实践），优先并行调研（有子 agent 用子 agent），带着准备来减轻用户负担。

### Write the SKILL.md

按访谈结果填充：

- **name**: 技能标识
- **description**: 何时触发+做什么。这是主要触发机制——"何时使用"全放这里不放正文。注意模型倾向"低触发"（有用也不调），描述要写得"主动一点"：比如不只写"做一个 dashboard"，要写"只要用户提到 dashboard/数据可视化/内部指标/展示公司数据，即使没明说 'dashboard' 也要用本技能"。**长度约定（框架优化 2026-08-04）：≤80 字（中文）——描述每次会话注入，过长烧 token；触发关键词保留即可**
- **compatibility**: 必需工具/依赖（可选，少用）
- 正文其余部分

> **写作指南**（技能解剖/渐进披露/写作模式/写作风格）见 `references/writing-guide.md`——起草前必读。

### Test Cases

起草后造 2-3 个真实感测试提示词给用户看，确认后跑。测试用例存 `evals/evals.json`（只放提示词，断言下一步再写；JSON 结构见 `references/schemas.md`）。

## Running and evaluating test cases

本段是连续流程，中途不停。不用 /skill-test 等测试技能。结果放 `<skill-name>-workspace/`（与技能目录同级），按迭代分 `iteration-N/`，内按用例分目录（用描述性名称，不只 eval-0）。

### Step 1: 同一回合并行 spawn 所有 run（with-skill + baseline）

每个用例两个子 agent：一个带技能、一个不带。**同一回合全部启动**，不要先带技能的再回来补 baseline。

- **With-skill run**: 指令包含 技能路径 / 任务提示 / 输入文件 / 输出目录 `<workspace>/iteration-N/<eval名>/with_skill/outputs/` / 要保存什么输出
- **Baseline run**: 同提示词。新建技能 → 无技能（存 `without_skill/outputs/`）；改进技能 → 旧版本（先 `cp -r <skill-path> <workspace>/skill-snapshot/` 快照，baseline 指向快照，存 `old_skill/outputs/`）

每个用例写 `eval_metadata.json`（断言先空，结构见 `references/schemas.md`）。

### Step 2: run 进行中起草断言

不要干等——为每个用例起草可客观验证的断言并讲给用户；`evals/evals.json` 已有断言就复查并解释。断言须客观可验证、名称描述性好（viewer 里一眼看懂查什么）。主观技能（写作/设计）别强上断言，定性评估。更新 `eval_metadata.json` 与 `evals/evals.json`，并给用户讲 viewer 里会看到什么（定性输出 + 定量基准）。

### Step 3: run 完成后立即抓时序数据

每个子 agent 完成通知里含 `total_tokens` 和 `duration_ms`——**只有这一次机会**，立刻存 `timing.json` 到 run 目录（结构见 `references/schemas.md`），来一条存一条，别攒批。

### Step 4: 打分、汇总、启动 viewer

1. **逐 run 打分** — 派 grader 子 agent（读 `agents/grader.md`）按断言评各输出，结果存各 run 目录 `grading.json`。**grading.json 的 expectations 数组字段必须是 `text`/`passed`/`evidence`**（viewer 依赖，别用 name/met/details 变体）。能程序化检查的断言写脚本跑，别肉眼——更快更可靠可复用。
2. **汇总 benchmark** — 在 skill-creator 目录跑：
   ```bash
   python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>
   ```
   产出 `benchmark.json`/`benchmark.md`（pass_rate/time/tokens，mean±stddev + delta）。手工生成 benchmark.json 须对照 `references/schemas.md` 的 schema。每个 with_skill 版本排在 baseline 前。
3. **分析师 pass** — 读 benchmark 数据找汇总隐藏的模式（见 `agents/analyzer.md` "Analyzing Benchmark Results"）：恒过断言（无区分度）、高方差用例（可能 flaky）、时间/token 权衡。
4. **启动 viewer**（定性+定量）：
   ```bash
   nohup python <skill-creator-path>/eval-viewer/generate_review.py \
     <workspace>/iteration-N --skill-name "my-skill" \
     --benchmark <workspace>/iteration-N/benchmark.json > /dev/null 2>&1 &
   VIEWER_PID=$!
   ```
   迭代 2+ 加 `--previous-workspace <workspace>/iteration-<N-1>`。**无显示/headless 环境**（Cowork/远程）：用 `--static <output_path>` 写独立 HTML，反馈以 `feedback.json` 下载，拷入 workspace 供下轮。别自己写自定义 HTML，就用 generate_review.py。
5. **告诉用户**："结果已在浏览器打开，两个标签页——'Outputs' 逐个用例看输出留反馈，'Benchmark' 看定量对比。看完了回来告诉我。"

### Step 5: 读反馈

用户说看完后读 `feedback.json`（结构见 `references/schemas.md`；空反馈=用户觉得没问题）。改进聚焦用户有具体抱怨的用例。用完杀 viewer：`kill $VIEWER_PID 2>/dev/null`。

---

## Improving the skill

这是循环的心脏。改进思路：

1. **从反馈泛化**：别做成只对这几个例子有效的过拟合技能；顽固问题换隐喻/换工作模式试试，别堆压迫性 MUSTs。
2. **保持提示词精简**：读 transcripts（不只最终输出），删掉让模型做无用功的部分。
3. **解释为什么**：尽量讲清每条指令的 why。看到 ALWAYS/NEVER 全大写或僵硬结构是黄旗——重构为解释推理。
4. **找跨用例重复工作**：多个子 agent 都写了同样 helper 脚本 → 技能应内置该脚本到 `scripts/`。

任务重要，思考时间不是瓶颈——起草后换新眼光再看再改。

**迭代循环**：应用改进 → 重跑全部用例到 `iteration-<N+1>/`（含 baseline；新建技能 baseline 恒为 without_skill；改进技能 baseline 用原版或上一迭代，自己判断）→ viewer 加 `--previous-workspace` → 等用户看 → 读新反馈 → 再改进。**终止条件**：用户满意 / 反馈全空 / 无实质进展。

---

## Advanced: Blind comparison

想严格对比两个版本（如"新版本真的更好吗？"）时用：读 `agents/comparator.md` 和 `agents/analyzer.md`——让独立 agent 盲评两个输出谁好，再分析为什么赢。可选、需子 agent，多数用户不需要，人工循环通常够用。

---

## Description Optimization

description 是技能是否被调用的主机制。创建/改进完技能后，主动提议优化描述提高触发准确率。

### Step 1: 生成触发评测查询

造 20 条查询（应触发/不应触发混合），存 JSON（结构见 `references/schemas.md`）。查询须真实感：具体、带细节（路径/个人上下文/列名/网址），混合长短与口语；聚焦边界而非清晰案例。

- Bad: `"Format this data"`、`"Extract text from PDF"`
- Good: `"ok so my boss just sent me this xlsx file (its in my downloads, called something like 'Q4 sales final FINAL v2.xlsx') and she wants me to add a column that shows the profit margin as a percentage..."`

应触发（8-10 条）：同一意图的不同措辞（正式/口语）、不点名技能/文件类型但显然需要的、冷门用例、与其他技能竞争应赢的。不应触发（8-10 条）：最有价值的是**近失**——共享关键词但实际需要别的东西；别用明显无关的（"写个斐波那契"对 PDF 技能太容易，测不出东西）。

### Step 2: 与用户复核

读 `assets/eval_review.html` 模板，替换三个占位符（`__EVAL_DATA_PLACEHOLDER__`/`__SKILL_NAME_PLACEHOLDER__`/`__SKILL_DESCRIPTION_PLACEHOLDER__`）写临时 HTML 打开给用户编辑（改查询/开关 should_trigger/增删，导出 eval set）。导出到 `~/Downloads/eval_set.json`（可能多版本，取最新的）。坏查询 → 坏描述，这步重要。

### Step 3: 跑优化循环

告诉用户"这要花点时间，后台跑，我定期看"。保存 eval set 后后台运行：

```bash
python -m scripts.run_loop \
  --eval-set <path-to-trigger-eval.json> \
  --skill-path <path-to-skill> \
  --model <model-id-powering-this-session> \
  --max-iterations 5 \
  --verbose
```

用当前会话的模型 ID（触发测试要对应用户实际体验）。期间定期 tail 输出给用户播报进度。循环自动：60/40 训练测试切分 → 现描述跑 3 次拿触发率 → Claude 据失败提改进 → 重测 → 最多 5 迭代 → 开 HTML 报告 → 返回按测试集（非训练集）选的 `best_description`（防过拟合）。

### 触发机制（帮助设计查询）

技能以 name+description 出现在 available_skills 里，模型凭描述决定是否调用。**简单一步查询（"读这个 PDF"）可能不触发技能**——模型能直接处理。多步/专业/复杂查询在描述匹配时会稳定触发。所以查询要够实质，别用简单查询当用例。

### Step 4: 应用结果

取 JSON 输出里的 `best_description` 更新 SKILL.md frontmatter，给用户看前后对比和分数。

---

### Package and Present（仅当有 present_files 工具）

有 `present_files` 就打包展示 `.skill` 文件（无则跳过）：
```bash
python -m scripts.package_skill <path/to/skill-folder>
```
打包后告诉用户 `.skill` 文件路径。

---

## 平台适配（Claude Code 为主环境）

本技能原生面向 Claude Code。其他平台差异：

- **Claude.ai**（无子 agent）：逐用例自己跑（读 SKILL.md 照做，一个个来）；无浏览器则对话内直接展示结果并询问反馈；跳过 baseline 与定量基准；描述优化（需 `claude -p`）跳过；盲对比跳过；打包可用。
- **Cowork**（有子 agent 无显示）：主流程照常（超时可串行）；viewer 用 `--static` 输出 HTML 给链接；**务必在自行评估前先生成 eval viewer 给用户看**；反馈走 feedback.json 文件；描述优化可用但等技能定稿后再跑。
- **更新已有技能**（通用）：保留原名（目录名+name 字段不变）；只读路径先拷到可写位置再编辑；手工打包先在 `/tmp/` 暂存再拷贝输出。

---

## Reference files

- `agents/grader.md` — 按断言评估输出的打分 agent
- `agents/comparator.md` — 两输出盲 A/B 对比
- `agents/analyzer.md` — 分析为何一方胜出
- `references/schemas.md` — evals/grading/timing/feedback 等 JSON 结构（唯一 schema 源）
- `references/writing-guide.md` — 技能写作指南（解剖/渐进披露/写作模式）

---

再强调一次核心循环：

- 弄清技能要做什么 → 起草/编辑 → 在测试提示词上跑带技能的 claude → 与用户评估输出（generate_review.py + 定量基准）→ 循环到满意 → 打包交付。

把步骤加进你的 TodoList。Good luck!
