# 钻潜交易系统

本工具继承 `deepseek/` 根级工程框架（`工程中枢/`）。规划部自动在线。

## 语言规则
- 所有回复均使用中文。

## 眼睛（视觉能力 · 2026-08-03 老板拍板默认启用）
- 模型本身无视觉，看图走**外置视觉**：`scripts/vision_describe.py <图片路径> --annotate`（智谱 glm-4.6v-flash 免费，方位标注模式）→ 输出坐标锚点客观事实 → 由分析者（主对话）做交易解读
- **默认行为**：任何涉及图片的任务（用户给图/K线图/视频帧/图转文）→ 默认调用眼睛 → 分析；不依赖模型自身视觉
- 图片来源：用户给文件路径（CC 粘贴的图片附件无法接收）
- 配置：`scripts/config.local.json`（key + 默认模型，已 gitignore 不入库）
- 输入图片临时目录：`temp/eye_input/`（已 gitignore）

## 量化策略规则
- **所有量化条件参数（阈值、周期、比例等）的调整必须经用户书面同意后执行。**
- 策略参数包括但不限于：DL_S/DL_A/DL_B、DL_RANGE_S/A/B、TY_S/TY_A/TY_B、TY_RANGE_S/A/B、DN_S/DN_A/DN_B（量比/实体比阈值）等。
- 未经明确授权，不得修改 `strategy/samples/zuanqian_strategy.py` 中的任何数值参数。
- 如需建议参数调整，先给出具体修改内容和理由，等待用户确认后再执行。

## 项目架构

```
交易部/
├── main.py                     # CLI入口 (list/kline/scan/diagnose)
├── strategy/
│   ├── base.py                 # BaseStrategy（filter/grade/quick_prefilter/to_trade_signal）
│   ├── conditions.py            # 条件函数库
│   └── samples/
│       ├── demo_strategy.py    # 示例策略（均线金叉+放量）
│       └── zuanqian_strategy.py # 钻潜评级策略 V2（唯一主策略）
├── analysis/
│   ├── indicators.py           # 技术指标（MA/MACD/RSI/KDJ/BOLL/ATR + 12 Alpha因子 + PT/LK/回踩/通道/像素感/冲突感/横盘感/过高点）
│   ├── scanner.py              # 全市场扫描器（5线程并发，normal/prebreak双模式）
│   ├── reporter.py             # 结果报告（表格/CSV/K线图，双模式表格）
│   └── factor_eval.py          # 因子评估（Alphalens IC分析 + 快检）
├── data/
│   ├── fetcher.py              # 数据获取 pytdx→baostock→akshare 三路冗余
│   ├── cache.py                # CSV缓存管理（5210只A股）
│   └── updater.py              # 数据更新（8线程并发pytdx）
├── config/
│   ├── settings.py             # 全局配置
│   └── stock_pool.py           # 股票池管理
└── scripts/
    ├── extract_knowledge.py    # 视频知识提取引擎
    ├── batch_process.py        # 批量视频处理
    └── output/                 # 53份知识文档（30课程+23扫盘）
```

## 当前状态（2026-07-30）

### 策略层 V2（课程标准版）
- 唯一主策略：`ZuanQianStrategy`（钻潜评级策略 V2）
- 6条件体系：**DL（独立结构）/ PT（平台位测试）/ LK（轮廓质量）/ TY（统一区间）/ DN（动能）/ SF（释放级别）**
- 优先级：Tier0 一票否决 → Tier1 核心三要素(PT>TY≈DN) → Tier2 质量分级(DL>LK>SF) → Tier3 加减分
- 评级 S/A/B/C：
  - **S级**：全部≥A + ≥3个S。全市场扫描 2只
  - **A级**：全部≥A 或 仅1个B。全市场扫描 16只
  - **B级**：最多3个B。全市场扫描 32只
  - **C级**：不展示（5152只）
- 双重扫描模式：
  - `--mode normal`：标准6条件评级（已突破的品种）
  - `--mode prebreak`：预突破5条件（不含DN），输出触发价/止损价/手数——晚间复盘挂条件单用

### 因子库
- 12个精选 Alpha 因子（从 Qlib Alpha158 提炼）：ROC10/STD20/VSTD10/MAXPOS20/KLEN/WVMA20/CORR10/RSQR20/BIAS5/MAD20/ILLIQUIDITY/TURN_ZSCORE
- Alphalens 因子评估模块（IC分析 + 分层回测 + 快检）

### 数据层
- 数据源：**pytdx**（通达信协议直连，~0.3秒/只）→ **baostock**（fallback）→ **akshare**（最后备选）
- 服务器：`180.153.18.170:7709`（最快）和 `60.191.117.167:7709`（稳定）
- 缓存：5,202 只 A 股全部缓存，11列完整（换手率全为0是pytdx已知局限）
- 全市场扫描速度：~7分钟（normal）/ ~8分钟（prebreak）

### 知识库
- 103份知识文档：30节课程 + 23份市场扫描录屏 + 50份周会/周课堂
- 25个 Claude 记忆文件（2026-07-30 周会学习+视觉量化完成后新增7个）：
  - 新增7个：周会学习总结、策略优化差距、视觉识别评级原则、经验型模式规则、出场六层体系、品种筛选排除清单、最新会话变更
- 69份周会/周课堂/扫盘文档已完成系统性学习（覆盖2021-2024），120+知识点增量

### 前端
- **已废弃**。Streamlit Web 前端（app.py/web/pages/）已删除，全部操作通过 CLI 完成

### 远端仓库
- GitHub: `https://github.com/jyymjs/drill-trading-system`

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
