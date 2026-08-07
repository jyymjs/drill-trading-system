# 交易部门（交易资产 · 归助理工具）

> 钻潜交易系统资产库。2026-08-07（R-033）：交易部由主对话（总助）驱动**投资研究员**（交易主人格，`.claude/agents/投资研究员.md`，吸收原 trader 定义）执行分析/扫描/复盘/知识库管理；本目录是交易资产（项目/策略/知识库/工具链）。

## 语言规则
- 所有回复均使用中文。

## 眼睛（视觉能力 · 2026-08-03 老板拍板默认启用）
- 模型本身无视觉，看图走**外置视觉**：`工具链/脚本/vision_describe.py <图片路径> --annotate`（智谱 glm-4.6v-flash 免费，方位标注模式）→ 输出坐标锚点客观事实 → 由分析者（主对话）做交易解读
- **默认行为**：任何涉及图片的任务（用户给图/K线图/视频帧/图转文）→ 默认调用眼睛 → 分析；不依赖模型自身视觉
- 图片来源：用户给文件路径（CC 粘贴的图片附件无法接收）
- 配置：`工具链/脚本/config.local.json`（key + 默认模型，已 gitignore 不入库）

## 量化策略规则
- **所有量化条件参数（阈值、周期、比例等）的调整必须经用户书面同意后执行。**
- 策略参数包括但不限于：DL_S/DL_A/DL_B、DL_RANGE_S/A/B、TY_S/TY_A/TY_B、TY_RANGE_S/A/B、DN_S/DN_A/DN_B（量比/实体比阈值）等。
- 未经明确授权，不得修改 `策略/核心策略/samples/zuanqian_strategy.py` 中的任何数值参数。
- 如需建议参数调整，先给出具体修改内容和理由，等待用户确认后再执行。

## 项目架构

```
交易部门/
├── main.py                     # CLI入口 (list/kline/scan/diagnose)
├── 策略/
│   ├── 核心策略/               # base.py / conditions.py / samples/
│   │   └── samples/zuanqian_strategy.py # 钻潜评级策略 V2（唯一主策略）
│   └── 知识库/                 # 交易知识（管理规范/素材台账/周会/课程知识卡）
├── 分析决策/                   # 分析 + 风控 + 跟踪 + 市场环境
│   ├── 分析/（indicators/scanner/reporter/factor_eval）
│   ├── 风控/（capital/exit_manager/position）
│   ├── 跟踪/（monte_carlo/sim_trading）
│   ├── 市场环境/
│   ├── 交易日志/               # 交易日志（journal）
│   └── 图表输出/               # 图表输出（output）
├── 数据基础/                   # 行情数据（fetcher/cache/updater）+ 数据/ + 配置/ + 批处理/
├── 工具链/                     # 工具 + 脚本（extract_knowledge/vision_describe/batch_process…）
├── 项目/                       # 回测系统/（独立回测项目 R-005）+ 回测输出/
├── 产出/输出/                  # 报告/ 实验/ 数据/ 图表/（四类分层）
└── 测试/
```

## 当前状态

> 现状快照见 [README.md](README.md)（按需读取，不随会话加载）

## CLI 命令
```bash
# 全市场扫描
python main.py scan --strategy zuanqian_strategy                    # 标准6条件
python main.py scan --strategy zuanqian_strategy --mode prebreak    # 预突破（挂条件单用）
python main.py scan --strategy demo_strategy                        # demo策略

# 单股诊断
python main.py diagnose 600419 --strategy zuanqian_strategy

# 股票列表
python main.py list

# K线图
python main.py kline 600419
```
