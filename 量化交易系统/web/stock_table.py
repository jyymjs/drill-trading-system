"""股票表格组件 - 条件格式 + 交互"""
import streamlit as st
import pandas as pd


def display_stock_table(results: list[dict], key: str = "stock_table"):
    """显示选股结果表格

    Args:
        results: 扫描结果列表
        key: Streamlit 组件 key
    """
    if not results:
        st.info("暂无符合条件的股票")
        return

    display_data = []
    for i, r in enumerate(results, 1):
        gain = r.get("涨幅%", 0)
        display_data.append({
            "序号": i,
            "代码": r.get("code", ""),
            "名称": r.get("name", ""),
            "收盘": r.get("price", 0),
            "涨幅": gain,
            "换手率": r.get("换手率%") or 0,
            "RSI": r.get("RSI") or 0,
            "MA5": r.get("MA5") or 0,
            "MA20": r.get("MA20") or 0,
        })

    df = pd.DataFrame(display_data)

    # 条件格式
    def color_gain(val):
        if val > 0:
            return f"color: #ff4d4d; font-weight: 600"
        elif val < 0:
            return f"color: #26a69a; font-weight: 600"
        return ""

    styled = df.style\
        .format({
            "收盘": "{:.2f}",
            "涨幅": "{:+.2f}%",
            "换手率": "{:.2f}%",
            "RSI": "{:.1f}",
            "MA5": "{:.2f}",
            "MA20": "{:.2f}",
        })\
        .applymap(color_gain, subset=["涨幅"])\
        .set_properties(**{
            "font-size": "0.85rem",
            "font-variant-numeric": "tabular-nums",
        })

    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={
            "序号": st.column_config.NumberColumn("序号", width="small"),
            "代码": st.column_config.TextColumn("代码", width="small"),
            "涨幅": st.column_config.NumberColumn("涨幅", format="+%.2f%%"),
        },
    )

    st.caption(f"共 {len(results)} 只符合条件")


def display_stock_selector(stocks: list[dict], key: str = "stock_selector") -> str | None:
    """股票搜索选择器"""
    options = {f"{s['code']} - {s['name']}": s["code"] for s in stocks}
    selected_label = st.selectbox(
        "选择或搜索股票",
        options=[""] + list(options.keys()),
        format_func=lambda x: x if x else "请选择股票...",
        key=key,
    )
    return options.get(selected_label)
