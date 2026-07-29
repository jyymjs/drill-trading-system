# 📊 量化交易系统

A股中长线量化选股系统。全市场日K线扫描 → 技术指标计算 → 策略筛选 → 命令行输出 + K线图。

## 快速开始

```bash
# 1. 查看A股市场概览
python main.py list

# 2. 查看单只股票K线（含均线/成交量）
python main.py kline 000001

# 3. 全市场扫描选股
python main.py scan
```

## Web 启动

```bash
pip install -r requirements.txt
streamlit run app.py
```

应用包含三个页面：市场概览、选股扫描、K线分析。

## 配置说明

编辑 `config/settings.py` 可调整 K 线获取年数、缓存有效期、扫描并发数等参数。

## 目录结构

```
├── config/      配置（股票池、K线周期、指标参数）
├── data/        数据获取（akshare封装 + CSV缓存）
├── strategy/    交易策略（基类 + 条件函数库 + 示例策略）
├── analysis/    技术指标 + 扫描器 + 报告生成
├── utils/       日志、工具函数
└── output/      扫描结果CSV、K线图
```

## 添加自定义策略

在 `strategy/samples/` 下新建一个文件，继承 `BaseStrategy` 并实现 `filter()` 方法：

```python
from strategy.base import BaseStrategy

class MyStrategy(BaseStrategy):
    name = "我的策略"
    description = "策略说明"

    def filter(self, df):
        # df 包含K线数据 + 技术指标
        # 返回 True=符合条件
        return df["RSI"].iloc[-1] < 30
```

运行：`python main.py scan --strategy my_strategy`

## 数据源

基于 [akshare](https://github.com/akfamily/akshare) 的东方财富接口，免费且覆盖全面。
数据首次获取后自动缓存，避免重复请求。
