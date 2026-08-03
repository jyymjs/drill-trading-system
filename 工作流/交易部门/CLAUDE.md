# 钻潜交易系统

本工具继承 `deepseek/` 根级工程框架（`../工程部门/`）。规划部自动在线。

## 语言规则
- 所有回复均使用中文。

## 眼睛（视觉能力 · 2026-08-03 老板拍板默认启用）
- 模型本身无视觉，看图走**外置视觉**：`脚本/vision_describe.py <图片路径> --annotate`（智谱 glm-4.6v-flash 免费，方位标注模式）→ 输出坐标锚点客观事实 → 由分析者（主对话）做交易解读
- **默认行为**：任何涉及图片的任务（用户给图/K线图/视频帧/图转文）→ 默认调用眼睛 → 分析；不依赖模型自身视觉
- 图片来源：用户给文件路径（CC 粘贴的图片附件无法接收）
- 配置：`脚本/config.local.json`（key + 默认模型，已 gitignore 不入库）
- 输入图片临时目录：`临时/eye_input/`（已 gitignore）

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
│   ├── 核心策略/               # 原 strategy/
│   │   ├── base.py             # BaseStrategy（filter/grade/quick_prefilter/to_trade_signal）
│   │   ├── conditions.py        # 条件函数库
│   │   └── samples/
│   │       ├── demo_strategy.py # 示例策略（均线金叉+放量）
│   │       └── zuanqian_strategy.py # 钻潜评级策略 V2（唯一主策略）
│   └── 知识库/                 # 交易知识（原 知识库/）
├── 分析/                       # 原 analysis/
│   ├── indicators.py           # 技术指标（MA/MACD/RSI/KDJ/BOLL/ATR + 12 Alpha因子 + PT/LK/回踩/通道/像素感/冲突感/横盘感/过高点）
│   ├── scanner.py              # 全市场扫描器（5线程并发，normal/prebreak双模式）
│   ├── reporter.py             # 结果报告（表格/CSV/K线图，双模式表格）
│   └── factor_eval.py          # 因子评估（Alphalens IC分析 + 快检）
├── 数据/                       # 原 data/
│   ├── fetcher.py              # 数据获取 pytdx→baostock→akshare 三路冗余
│   ├── cache.py                # CSV缓存管理（5210只A股）
│   └── updater.py              # 数据更新（8线程并发pytdx）
├── 配置/                       # 原 config/
│   ├── settings.py             # 全局配置
│   └── stock_pool.py           # 股票池管理
└── 脚本/                       # 原 scripts/
    ├── extract_knowledge.py    # 视频知识提取引擎
    ├── batch_process.py        # 批量视频处理
    └── 输出/                   # 53份知识文档（30课程+23扫盘）
```

## 当前状态

> 现状快照见 [docs/status.md](docs/status.md)（按需读取，不随会话加载）

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
