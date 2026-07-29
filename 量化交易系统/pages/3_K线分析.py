"""页面3: 交互式K线分析"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="K线分析", page_icon="📈", layout="wide")
st.markdown('<p class="section-title">📈 K 线分析</p>', unsafe_allow_html=True)

# ── 数据缓存 ──
@st.cache_data(ttl=300)
def load_securities():
    """加载标的列表（5分钟缓存）"""
    try:
        from config.stock_pool import get_all_securities
        return get_all_securities(include_etf=True)
    except Exception:
        return []

@st.cache_data(ttl=60)
def load_kline_data(code, start_date, end_date, adjust):
    """加载K线数据（1分钟缓存）"""
    from data.fetcher import get_daily_kline
    return get_daily_kline(code, start_date=start_date, end_date=end_date, adjust=adjust)

# ── 初始化 Session State ──
for key in ["kline_symbol", "kline_name", "kline_period", "kline_adjust"]:
    if key not in st.session_state:
        st.session_state[key] = "" if key != "kline_period" else "daily"

# ── 侧边栏 ──
with st.sidebar:
    st.markdown("### 🔧 设置")

    # 证券选择
    securities = load_securities()
    sec_options = {}
    if securities:
        sec_options = {
            f"{'📈' if s['type']=='stock' else '📦'} {s['code']} - {s['name']}": s["code"]
            for s in securities
        }

    selected_label = st.selectbox(
        "搜索标的",
        options=[""] + list(sec_options.keys()),
        format_func=lambda x: x if x else "🔍 搜索代码或名称...",
        key="stock_selector",
    )
    if selected_label and selected_label in sec_options:
        code = sec_options[selected_label]
        name = selected_label.split(" - ")[1]
        st.session_state["kline_symbol"] = code
        st.session_state["kline_name"] = name

    code = st.session_state.get("kline_symbol", "000001")
    name = st.session_state.get("kline_name", "")

    st.divider()

    # ── 时间范围（按钮组替代滑块） ──
    st.markdown("#### 时间范围")
    period_options = {"1月": 1, "3月": 3, "6月": 6, "1年": 12, "3年": 36}
    period_labels = list(period_options.keys())
    selected_period = st.segmented_control(
        "时间范围", period_labels, default="3年",
        key="period_buttons", label_visibility="collapsed",
    )
    months = period_options.get(selected_period, 36)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=int(365 * months / 12))

    st.divider()

    # ── 周期切换 ──
    st.markdown("#### K线周期")
    period_map = {"日线": "daily", "周线": "weekly", "月线": "monthly"}
    selected_period_label = st.segmented_control(
        "周期", list(period_map.keys()), default="日线",
        key="kline_period_selector", label_visibility="collapsed",
    )
    st.session_state["kline_period"] = period_map.get(selected_period_label, "daily")

    st.divider()

    # ── 复权 ──
    adjust = st.radio("复权方式", ["不复权", "前复权", "后复权"], index=1, horizontal=True)
    adjust_map = {"不复权": "", "前复权": "qfq", "后复权": "hfq"}

    st.divider()

    # ── 技术指标 ──
    st.markdown("#### 技术指标")
    cols = st.columns(2)
    with cols[0]:
        show_ma = st.checkbox("均线", value=True)
        show_macd = st.checkbox("MACD", value=False)
    with cols[1]:
        show_volume = st.checkbox("成交量", value=True)
        show_rsi = st.checkbox("RSI", value=False)

# ── 主区域 ──
st.markdown(f"""
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
    <div>
        <span style="font-size:1.3rem; font-weight:700; color:#e0e0e0;">
            <span style="color:#60a5fa;">{code}</span>
            {f'<span style="font-size:0.9rem; color:#8b949e; margin-left:0.5rem;">{name}</span>' if name else ''}
        </span>
        <span style="font-size:0.75rem; color:#8b949e; margin-left:1rem;">
            {selected_period_label} · {selected_period}
        </span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── 加载数据 ──
with st.spinner(f"加载 {code} 数据..."):
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    df = load_kline_data(code, start_str, end_str, adjust_map[adjust])

if df.empty:
    st.warning(f"未获取到 {code} 的数据，请检查代码或网络连接")
    st.info("提示: ETF代码如 510050, 股票代码如 000001(深市) 600519(沪市)")
    st.stop()

# ── 最新行情卡片（图表上方） ──
latest = df.iloc[-1]
try:
    metric_cols = st.columns(6)
    metrics_data = [
        ("收盘价", f'{latest["收盘"]:.2f}', None),
        ("涨跌幅", f'{latest["涨跌幅"]:+.2f}%', latest["涨跌幅"]),
        ("最高", f'{latest["最高"]:.2f}', None),
        ("最低", f'{latest["最低"]:.2f}', None),
        ("成交量", f'{latest["成交量"]/10000:.0f}万', None),
        ("换手率", f'{latest["换手率"]:.2f}%' if "换手率" in latest and pd.notna(latest["换手率"]) else "--", None),
    ]
    for i, (label, val, delta) in enumerate(metrics_data):
        with metric_cols[i]:
            if delta is not None:
                st.metric(label, val, delta=f"{delta:+.2f}%")
            else:
                st.metric(label, val)
except Exception:
    pass

# ── K线图 ──
try:
    from web.kline_chart import create_kline_chart
    fig = create_kline_chart(
        df, symbol=code, name=name,
        show_ma=show_ma, show_volume=show_volume,
        show_macd=show_macd, show_rsi=show_rsi,
        dark=True,
    )
    st.plotly_chart(fig, use_container_width=True, config={
        "scrollZoom": True,
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "displaylogo": False,
    })
except Exception as e:
    st.error(f"K线图渲染失败: {e}")
