"""Plotly K线图组件 - 暗色金融主题 + 交互增强"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from analysis.indicators import all_indicators


# 自定义暗色模板（与系统主题一致）
CUSTOM_DARK_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0", size=11, family="Inter, sans-serif"),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.08)",
            tickfont=dict(color="#8b949e", size=10),
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.08)",
            tickfont=dict(color="#8b949e", size=10),
        ),
        hoverlabel=dict(
            bgcolor="#1a1a2e",
            font_size=12,
            font_color="#e0e0e0",
        ),
    )
)


def create_kline_chart(
    df: pd.DataFrame,
    symbol: str = "",
    name: str = "",
    show_ma: bool = True,
    show_volume: bool = True,
    show_macd: bool = False,
    show_rsi: bool = False,
    dark: bool = True,
) -> go.Figure:
    """创建交互式 K 线图

    Args:
        df: K线数据
        symbol: 股票代码
        name: 股票名称
        show_ma: 显示均线
        show_volume: 显示成交量
        show_macd: 显示 MACD
        show_rsi: 显示 RSI
        dark: 暗色主题（默认 True，不受影响）

    Returns:
        plotly Figure
    """
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="暂无数据", x=0.5, y=0.5, showarrow=False)
        return fig

    # 计算指标
    if "MA5" not in df.columns:
        needed = []
        if show_ma:
            needed += ["MA5", "MA10", "MA20", "MA60"]
        if show_volume:
            needed += ["VOL_MA5"]
        if show_macd:
            needed += ["DIF", "DEA", "MACD"]
        if show_rsi:
            needed += ["RSI"]
        df = all_indicators(df, needed_cols=needed if needed else None)

    # 副图数量
    subplot_count = 1
    row_heights = [0.55]
    if show_volume:
        subplot_count += 1
        row_heights.append(0.15)
    if show_macd:
        subplot_count += 1
        row_heights.append(0.15)
    if show_rsi:
        subplot_count += 1
        row_heights.append(0.15)

    specs = [[{"secondary_y": True}]] + [[{"secondary_y": False}]] * (subplot_count - 1)

    fig = make_subplots(
        rows=subplot_count, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=row_heights,
        specs=specs,
    )

    # ---- 颜色定义 ----
    color_up = "#00d4aa"   # 多头：青色
    color_down = "#e040a0"  # 空头：玫红

    # ---- K线颜色 ----
    colors = [color_up if c >= o else color_down
              for c, o in zip(df["收盘"], df["开盘"])]

    # ---- 主图：K线 ----
    hover_text = []
    for _, row in df.iterrows():
        text = (
            f"<b>{row['日期'].strftime('%Y-%m-%d')}</b><br>"
            f"开盘: {row['开盘']:.2f}<br>"
            f"最高: {row['最高']:.2f}<br>"
            f"最低: {row['最低']:.2f}<br>"
            f"收盘: <b>{row['收盘']:.2f}</b><br>"
            f"涨跌幅: <b>{row['涨跌幅']:+.2f}%</b><br>"
            f"成交量: {row['成交量']/10000:.0f}万<br>"
            f"成交额: {row['成交额']/100000000:.2f}亿"
        )
        if "换手率" in row and pd.notna(row["换手率"]):
            text += f"<br>换手率: {row['换手率']:.2f}%"
        hover_text.append(text)

    fig.add_trace(go.Candlestick(
        x=df["日期"],
        open=df["开盘"], high=df["最高"],
        low=df["最低"], close=df["收盘"],
        name="K线",
        text=hover_text,
        hoverinfo="text",
        increasing_line_color=color_up,
        decreasing_line_color=color_down,
    ), row=1, col=1)

    # ---- 主图：均线 ----
    if show_ma:
        ma_configs = [
            ("MA5", "#42a5f5", 1),
            ("MA10", "#ffa726", 1.2),
            ("MA20", "#ab47bc", 1),
            ("MA60", "#66bb6a", 1),
        ]
        for ma_name, color, width in ma_configs:
            if ma_name in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["日期"], y=df[ma_name],
                    name=ma_name, line=dict(color=color, width=width),
                    hoverinfo="skip",
                ), row=1, col=1)

    # ---- 副图：成交量 ----
    current_row = 2
    if show_volume and "成交量" in df.columns:
        fig.add_trace(go.Bar(
            x=df["日期"], y=df["成交量"],
            name="成交量",
            marker_color=colors,
            opacity=0.4,
            hovertemplate="成交量: %{y:,}<br>日期: %{x}<extra></extra>",
        ), row=current_row, col=1)

        if "VOL_MA5" in df.columns:
            fig.add_trace(go.Scatter(
                x=df["日期"], y=df["VOL_MA5"],
                name="均量", line=dict(color="#ffa726", width=1, dash="dash"),
                hoverinfo="skip",
            ), row=current_row, col=1)

        fig.update_yaxes(title_text="成交量", row=current_row, col=1)
        current_row += 1

    # ---- 副图：MACD ----
    if show_macd and all(c in df.columns for c in ["DIF", "DEA", "MACD"]):
        macd_colors = [color_up if v >= 0 else color_down for v in df["MACD"]]
        fig.add_trace(go.Bar(
            x=df["日期"], y=df["MACD"],
            name="MACD", marker_color=macd_colors,
            opacity=0.5,
        ), row=current_row, col=1)
        fig.add_trace(go.Scatter(
            x=df["日期"], y=df["DIF"],
            name="DIF", line=dict(color="#42a5f5", width=1.5),
        ), row=current_row, col=1)
        fig.add_trace(go.Scatter(
            x=df["日期"], y=df["DEA"],
            name="DEA", line=dict(color="#ffa726", width=1.5),
        ), row=current_row, col=1)
        fig.add_hline(y=0, line_width=1, line_color="rgba(255,255,255,0.2)",
                      row=current_row, col=1)
        current_row += 1

    # ---- 副图：RSI ----
    if show_rsi and "RSI" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["日期"], y=df["RSI"],
            name="RSI", line=dict(color="#ab47bc", width=1.5),
        ), row=current_row, col=1)
        fig.add_hline(y=70, line_width=1, line_color="rgba(255,77,77,0.4)",
                      line_dash="dash", row=current_row, col=1)
        fig.add_hline(y=30, line_width=1, line_color="rgba(0,212,170,0.4)",
                      line_dash="dash", row=current_row, col=1)
        fig.add_hline(y=50, line_width=1, line_color="rgba(255,255,255,0.1)",
                      line_dash="dot", row=current_row, col=1)
        fig.update_yaxes(range=[0, 100], title_text="RSI", row=current_row, col=1)

    # ---- 全局布局 ----
    title_text = f"{symbol} {name}".strip() if name else symbol

    fig.update_layout(
        title=dict(text=title_text, x=0.5, xanchor="center",
                   font=dict(size=16, color="#e0e0e0")),
        template=CUSTOM_DARK_TEMPLATE,
        xaxis=dict(
            rangeslider=dict(visible=True, thickness=0.06),
            type="date",
            showspikes=True,
            spikecolor="rgba(255,255,255,0.1)",
            spikesnap="cursor",
            spikethickness=1,
        ),
        yaxis=dict(
            showspikes=True,
            spikecolor="rgba(255,255,255,0.1)",
            spikethickness=1,
        ),
        hovermode="x unified",
        height=650,
        margin=dict(l=40, r=20, t=50, b=20),
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="right", x=1,
            font=dict(size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        dragmode="zoom",
    )

    # 统一 xaxis
    for i in range(2, subplot_count + 1):
        fig.update_xaxes(matches="x", row=i, col=1)

    return fig
