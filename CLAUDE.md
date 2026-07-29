# Claude Code 项目指令

## 语言规则
- 所有回复均使用中文。

## 量化策略规则
- **所有量化条件参数（阈值、周期、比例等）的调整必须经用户书面同意后执行。**
- 策略参数包括但不限于：DL_S/DL_A/DL_B、DL_RANGE_S/A/B、TY_S/TY_A/TY_B、TY_RANGE_S/A/B、DN_S/DN_A/DN_B（量比/实体比阈值）、VCP_S/A/B 等。
- 未经明确授权，不得修改 `strategy/samples/zuanqian_strategy.py` 中的任何数值参数。
- 如需建议参数调整，先给出具体修改内容和理由，等待用户确认后再执行。

## 项目架构

```
量化交易系统/
├── main.py                     # CLI入口 (list/kline/scan/diagnose)
├── app.py                      # Streamlit Web入口
├── .streamlit/config.toml      # 深色金融主题配置
├── strategy/
│   ├── base.py                 # BaseStrategy基类（filter/grade/quick_prefilter）
│   ├── conditions.py            # 条件函数库
│   └── samples/
│       ├── demo_strategy.py    # 示例策略（均线金叉+放量）
│       └── zuanqian_strategy.py # 钻潜评级策略（唯一主策略）
├── analysis/
│   ├── indicators.py           # 技术指标计算（MA/MACD/RSI/KDJ/BOLL/量比/ATR等）
│   ├── scanner.py              # 全市场扫描器（5线程并发）
│   └── reporter.py             # 结果报告（表格/CSV/K线图）
├── data/
│   ├── fetcher.py              # 数据获取 pytdx→baostock→akshare 三路冗余
│   ├── cache.py                # CSV缓存管理
│   └── updater.py              # 数据更新（8线程并发pytdx）
├── config/
│   ├── settings.py             # 全局配置
│   └── stock_pool.py           # 股票池管理（ETF静态列表→pytdx→baostock）
├── web/
│   ├── kline_chart.py          # Plotly K线图（自定义暗色模板）
│   ├── stock_table.py          # 结果表格（条件格式）
│   ├── strategy_config.py      # 策略注册/选择
│   └── data_manager.py         # 数据管理面板
└── pages/
    ├── 1_市场概览.py           # KPI仪表盘+市场分布+缓存状态+数据管理
    ├── 2_选股扫描.py           # 双栏布局+评级结果表格+条件格式
    └── 3_K线分析.py            # 交互式K线+均线/MACD/RSI+周期切换
```

## 当前状态（2026-07-29）

### 数据层
- 数据源：**pytdx**（通达信协议直连，~0.3秒/只）→ **baostock**（fallback）→ **akshare**（最后备选）
- 服务器：`180.153.18.170:7709`（最快）和 `60.191.117.167:7709`（稳定）
- 缓存：5,202 只 A 股全部缓存，数据日期 2026-07-29
- 全市场扫描速度：~3分钟（旧版 baostock 需数小时）

### 策略层
- 唯一主策略：`ZuanQianStrategy`（钻潜评级策略）
- 评级体系 S/A/B/C（与课程一致）：
  - **S级**（优质）：全部条件优秀，独立结构≥120根+量比≥2.5x+1st释放
  - **A级**（常规）：核心条件满足，独立结构≥90根+量比≥1.8x+1st/2nd
  - **B级**（瑕疵）：基本满足，独立结构≥60根+量比≥1.5x
  - **C级**（不满足）：不展示
- 6条件评级：独立结构 + 统一区间 + 动能 + 释放级别 + 波动率收缩 + 均线过滤
- VCP 模式前称已合并进评级体系作为"波动率收缩"维度

### Web层
- 深色金融主题（青色#00d4aa主色）
- 三页面：市场概览→选股扫描→K线分析
- 市场概览：KPI卡片+市场分布饼图+缓存状态图+标的卡片网格
- 选股扫描：双栏布局+评级彩色徽章（S绿/A蓝/B黄）+条件格式表格
- K线分析：按钮组时间选择+周期切换(日/周/月)+暗色Plotly模板

### 记忆文件
- 10个交易知识记忆文件在 `C:\Users\32032\.claude\memory/`
- 覆盖：标准模式6条件、意图模式、离场规则、K线形态、市场结构、成交量、心理、资金管理

### 远端仓库
- GitHub: `https://github.com/jyymjs/drill-trading-system`
- 最后提交: `feat: 钻潜交易系统 - A股量化选股策略框架`

## CLI 常用命令
```bash
python main.py scan --strategy zuanqian_strategy    # 全市场评级扫描
python main.py diagnose 000001 --strategy zuanqian_strategy  # 单股诊断
python main.py scan --strategy demo_strategy        # demo策略扫描
```

## Web 启动
```bash
cd 量化交易系统 && streamlit run app.py
```
桌面快捷方式：`C:\Users\32032\Desktop\启动看板.bat`
