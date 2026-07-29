"""结果报告生成"""
import os
from datetime import datetime
import pandas as pd
from prettytable import PrettyTable

from config.settings import OUTPUT_DIR
from analysis.indicators import all_indicators
from data.fetcher import get_daily_kline

try:
    import mplfinance as mpf
    HAS_MPF = True
except ImportError:
    HAS_MPF = False


def print_results(results: list[dict], top_n: int = 30) -> None:
    """打印扫描结果表格"""
    if not results:
        print("\n没有符合条件股票")
        return

    table = PrettyTable()
    table.field_names = ["序号", "代码", "名称", "收盘", "涨幅%", "换手率%", "RSI", "MA5", "MA20"]
    table.align["名称"] = "l"
    table.align["代码"] = "l"
    table.float_format = ".2"

    for i, r in enumerate(results[:top_n], 1):
        table.add_row([
            i,
            r["code"],
            r["name"],
            r.get("price", "--"),
            f'{r.get("涨幅%", 0):+.2f}',
            r.get("换手率%", "--"),
            r.get("RSI", "--"),
            f'{r.get("MA5", 0):.2f}',
            f'{r.get("MA20", 0):.2f}',
        ])

    print(f"\n=== 扫描结果 ({len(results)} 只符合条件) ===\n")
    print(table)

    if len(results) > top_n:
        print(f"\n... 还有 {len(results) - top_n} 只 (完整结果保存在 output/)")


def save_results(results: list[dict]) -> str:
    """保存结果到 CSV"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"scan_result_{timestamp}.csv"

    df = pd.DataFrame(results)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"结果已保存: {path}")
    return str(path)


def _prepare_mpf_data(df: pd.DataFrame) -> pd.DataFrame:
    """将中文列名映射为 mplfinance 所需英文列名"""
    df = df.copy()
    col_map = {
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
    }
    # 只重命名存在的列
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=rename)
    # 确保需要的列都存在
    required = ["Open", "Close", "High", "Low"]
    if not all(c in df.columns for c in required):
        raise ValueError(f"缺少必要列: {required}")
    return df


def plot_kline(
    symbol: str,
    name: str = "",
    save: bool = True,
    show_indicators: bool = True,
) -> None:
    """绘制K线图（含技术指标）

    Args:
        symbol: 股票代码
        name: 股票名称
        save: 是否保存图片
        show_indicators: 是否显示均线和成交量
    """
    if not HAS_MPF:
        print("请安装 mplfinance: pip install mplfinance")
        return

    df = get_daily_kline(symbol)
    if df.empty:
        print(f"未获取到 {symbol} 的数据")
        return

    # 准备好 mplfinance 数据
    df = _prepare_mpf_data(df)
    df = df.set_index("日期")
    df.index = pd.to_datetime(df.index)

    # 取最近120个交易日
    df = df.tail(120)

    # 计算指标
    if show_indicators:
        ma_short = df["Close"].rolling(5).mean()
        ma_medium = df["Close"].rolling(20).mean()
        ma_long = df["Close"].rolling(60).mean()

        apds = [
            mpf.make_addplot(ma_short, color="blue", width=0.8, label="MA5"),
            mpf.make_addplot(ma_medium, color="orange", width=0.8, label="MA20"),
            mpf.make_addplot(ma_long, color="red", width=0.8, label="MA60"),
        ]

        # 成交量颜色（涨红跌绿）
        colors = ["red" if c >= o else "green" for c, o in zip(df["Close"], df["Open"])]
        apds.append(
            mpf.make_addplot(df["Volume"] if "Volume" in df.columns else df["Close"],
                           type="bar", color=colors,
                           panel=1, ylabel="成交量", alpha=0.6)
        )

        title = f"{symbol} {name}" if name else symbol

        fig, axes = mpf.plot(
            df,
            type="candle",
            addplot=apds,
            title=title,
            style="charles",
            volume=False,  # 不用内置成交量，用自定义的
            figsize=(12, 7),
            panel_ratios=(3, 1),
            returnfig=True,
        )

        if save:
            os.makedirs(str(OUTPUT_DIR), exist_ok=True)
            path = OUTPUT_DIR / f"{symbol}_kline.png"
            fig.savefig(str(path), dpi=150, bbox_inches="tight")
            print(f"K线图已保存: {path}")

    else:
        title = f"{symbol} {name}" if name else symbol
        mpf.plot(
            df,
            type="candle",
            title=title,
            style="charles",
            volume=True,
            figsize=(12, 6),
            save=dict(fname=str(OUTPUT_DIR / f"{symbol}_kline.png"), dpi=150)
            if save else None,
        )
