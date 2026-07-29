"""页面1: 市场概览仪表盘"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="市场概览", page_icon="📊", layout="wide")

# ── 数据加载（带缓存） ──
@st.cache_data(ttl=3600)
def load_market_data():
    """加载市场概览数据（1小时缓存）"""
    from config.stock_pool import get_all_stocks, get_etf_list
    from data.updater import get_cache_stats
    stocks = get_all_stocks()
    etfs = get_etf_list()
    stats = get_cache_stats()
    return stocks, etfs, stats

try:
    stocks, etfs, stats = load_market_data()
except Exception as e:
    st.error(f"数据加载失败: {e}")
    stocks, etfs, stats = [], [], {}

st.markdown('<p class="section-title">📊 市场概览</p>', unsafe_allow_html=True)

# ── KPI 卡片行 ──
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("A股总数", f"{len(stocks):,}" if stocks else "--",
              help="沪深两市全部A股")
with col2:
    st.metric("ETF总数", f"{len(etfs):,}" if etfs else "--",
              help="包括股票型、债券型、商品型ETF")
with col3:
    if stocks:
        sh = sum(1 for s in stocks if s["code"].startswith("6"))
        sz = sum(1 for s in stocks if s["code"].startswith("0") or s["code"].startswith("3"))
        bj = sum(1 for s in stocks if s["code"].startswith("8") or s["code"].startswith("4"))
        st.metric("沪 / 深 / 北", f"{sh:,} / {sz:,} / {bj:,}")
    else:
        st.metric("沪 / 深 / 北", "--")
with col4:
    cached = stats.get("total", 0)
    total_stocks = len(stocks) + len(etfs)
    pct = cached / total_stocks * 100 if total_stocks else 0
    st.metric("已缓存", f"{cached:,} ({pct:.0f}%)" if cached else "--",
              delta=f"{pct:.0f}%" if pct else None)
with col5:
    latest = stats.get("latest_date") or "--"
    st.metric("数据日期", str(latest))

st.markdown("<br>", unsafe_allow_html=True)

# ── 图表区（市场分布 + 缓存状态） ──
col_a, col_b = st.columns(2)
with col_a:
    st.markdown('<p class="card-title">市场分布</p>', unsafe_allow_html=True)
    if stocks:
        sh = sum(1 for s in stocks if s["code"].startswith("6"))
        sz = sum(1 for s in stocks if s["code"].startswith("0") or s["code"].startswith("3"))
        bj_count = sum(1 for s in stocks if s["code"].startswith("8") or s["code"].startswith("4"))
        dist_df = pd.DataFrame({
            "交易所": ["上海主板", "深圳主板/创业板", "北京交易所"],
            "数量": [sh, sz, bj_count],
        })
        import plotly.express as px
        fig_dist = px.pie(dist_df, values="数量", names="交易所",
                          color_discrete_sequence=["#00d4aa", "#60a5fa", "#8b5cf6"],
                          hole=0.5)
        fig_dist.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=20, t=20, b=20),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_dist, use_container_width=True)

with col_b:
    st.markdown('<p class="card-title">缓存状态</p>', unsafe_allow_html=True)
    if stats.get("total", 0):
        cached_val = stats.get("total", 0)
        uncached = total_stocks - cached_val
        cache_df = pd.DataFrame({
            "状态": ["已缓存", "未缓存"],
            "数量": [cached_val, max(uncached, 0)],
        })
        fig_cache = px.bar(cache_df, x="状态", y="数量", text="数量",
                          color="状态",
                          color_discrete_map={"已缓存": "#00d4aa", "未缓存": "#2a2a3e"})
        fig_cache.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=20, t=20, b=20),
            showlegend=False,
            yaxis=dict(range=[0, total_stocks * 1.2]),
        )
        fig_cache.update_traces(textposition="outside")
        st.plotly_chart(fig_cache, use_container_width=True)
    else:
        st.info("暂无缓存数据，请先更新数据")

# ── 数据管理区域 ──
with st.expander("⚙️ 数据管理", expanded=False):
    from web.data_manager import render_data_manager
    render_data_manager()

st.markdown("<br>", unsafe_allow_html=True)

# ── 快速查看 - 热门标的卡片 ──
st.markdown('<p class="section-title">🔍 快速查看</p>', unsafe_allow_html=True)

@st.cache_data(ttl=300)
def get_latest_prices(codes):
    """批量获取最新价格"""
    try:
        from data.fetcher import get_bulk_a_stock_day
        df = get_bulk_a_stock_day()
        if df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            if row["code"] in codes:
                result[row["code"]] = row["收盘"]
        return result
    except Exception:
        return {}

def render_stock_cards(title, items, key_prefix):
    """渲染标的卡片网格"""
    st.markdown(f'<p class="card-title">{title}</p>', unsafe_allow_html=True)
    codes = list(items.keys())
    prices = get_latest_prices(codes)
    cols = st.columns(6)
    for i, (code, name) in enumerate(items.items()):
        with cols[i % 6]:
            price = prices.get(code)
            price_str = f"{price:.2f}" if price else "--"
            card_html = f"""
            <div style="
                background: #141420;
                border: 1px solid rgba(255,255,255,0.06);
                border-radius: 8px;
                padding: 0.75rem;
                text-align: center;
                cursor: pointer;
                transition: all 0.2s ease;
                margin-bottom: 0.5rem;
            "
            onclick="alert('查看 {code} K线图（请在左侧导航进入K线分析）')"
            >
                <div style="font-size: 0.75rem; color: #00d4aa; font-weight: 600; margin-bottom: 0.25rem;">{code}</div>
                <div style="font-size: 0.85rem; color: #e0e0e0; font-weight: 500; margin-bottom: 0.25rem;">{name}</div>
                <div style="font-size: 0.9rem; color: {'#00d4aa' if price_str != '--' else '#8b949e'}; font-weight: 700;">{price_str}</div>
            </div>
            """
            st.markdown(card_html, unsafe_allow_html=True)

popular_stocks = {
    "000001": "平安银行", "600519": "贵州茅台",
    "300750": "宁德时代", "000858": "五粮液",
    "600036": "招商银行", "601318": "中国平安",
}
render_stock_cards("热门股票", popular_stocks, "s")

popular_etfs = {
    "510050": "上证50ETF", "510300": "沪深300ETF",
    "510500": "中证500ETF", "159915": "创业板ETF",
    "512880": "证券ETF", "518880": "黄金ETF",
}
render_stock_cards("热门ETF", popular_etfs, "e")

# 跳转提示（使用 Streamlit 原生组件而非 onclick）
st.info("💡 点击左侧导航栏进入「K线分析」查看详细走势")
