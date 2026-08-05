# 📊 交易部门

A股中长线量化选股系统。基于路肖南钻潜交易体系，全市场日K线扫描 → 6条件评级 → CLI 输出。
（2026-08-05 R-010：交易部门=交易资产库，由主对话（助理）驱动 trader 工具 agent 使用）

## 快速开始

```bash
cd 工作流/交易部门

# 查看A股列表
python main.py list

# 全市场扫描（标准评级）
python main.py scan --strategy zuanqian_strategy

# 预突破扫描（晚间复盘挂条件单）
python main.py scan --strategy zuanqian_strategy --mode prebreak

# 单股诊断
python main.py diagnose 600419 --strategy zuanqian_strategy

# K线图
python main.py kline 600419
```

## 策略体系 V2

6 条件评级：**DL（独立结构）/ PT（平台位测试）/ LK（轮廓质量）/ TY（统一区间）/ DN（动能）/ SF（释放级别）**

评级 S/A/B/C，优先级：Tier0 一票否决 → Tier1 核心三要素 → Tier2 质量分级 → Tier3 加减分

## 配置

编辑 `数据基础/配置/settings.py` 可调整 K 线获取年数、缓存有效期、扫描并发数等参数。

## 目录结构

```
交易部门/
├── main.py          CLI 入口
├── 策略/
│   ├── 核心策略/     策略（基类 + 钻潜评级策略 V2，samples/）
│   └── 知识库/      交易知识（管理规范/素材台账/课程知识卡）
├── 分析决策/         分析（指标/扫描器/报告/因子）+ 风控 + 跟踪 + 市场环境 + journal
├── 数据基础/         数据获取（pytdx→baostock→akshare三路冗余）+ CSV缓存 + 配置
├── 工具链/           工具 + 脚本（视频知识提取/视觉标注/批量处理）
├── 项目/回测系统/     独立回测项目（R-005）
├── 产出/输出/         扫描结果CSV/回测输出/市场回顾
└── 测试/
```

## 添加自定义策略

在 `策略/核心策略/samples/` 下新建文件，继承 `BaseStrategy`：

```python
from 策略.核心策略.base import BaseStrategy

class MyStrategy(BaseStrategy):
    name = "我的策略"
    description = "策略说明"

    def filter(self, df):
        return df["RSI"].iloc[-1] < 30
```

运行：`python main.py scan --strategy my_strategy`

## 数据源

pytdx（通达信直连 ~0.3秒/只） → baostock（fallback） → akshare（最后备选）
