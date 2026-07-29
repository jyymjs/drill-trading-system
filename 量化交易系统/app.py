#!/usr/bin/env python3
"""量化交易系统 - Web 看板入口

启动: streamlit run app.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import streamlit as st

# ── 页面配置 ──
st.set_page_config(
    page_title="量化交易系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 全局 CSS（深色金融主题） ──
st.markdown("""
<style>
    /* 导入 Inter 字体 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

    /* 页面背景 */
    .stApp { background: #0a0a0a; }

    /* 导航栏容器 */
    .nav-container {
        display: flex; align-items: center; justify-content: space-between;
        padding: 0.75rem 1.5rem;
        background: linear-gradient(180deg, #141420 0%, #0f0f1a 100%);
        border-bottom: 1px solid rgba(255,255,255,0.06);
        margin: -0.5rem -1rem 1rem -1rem;
        position: sticky; top: 0; z-index: 999;
    }
    .nav-brand {
        display: flex; align-items: center; gap: 0.75rem;
        font-size: 1.25rem; font-weight: 700;
        background: linear-gradient(135deg, #00d4aa, #60a5fa);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .nav-brand span { font-size: 1.5rem; }
    .nav-links { display: flex; gap: 0.25rem; }
    .nav-link {
        padding: 0.4rem 1rem; border-radius: 6px;
        font-size: 0.875rem; font-weight: 500;
        color: #8b949e; text-decoration: none !important;
        transition: all 0.2s ease; cursor: pointer;
    }
    .nav-link:hover { color: #e0e0e0; background: rgba(255,255,255,0.05); }
    .nav-link.active { color: #00d4aa; background: rgba(0,212,170,0.1); }

    /* Metric 卡片 */
    div[data-testid="metric-container"] {
        background: #141420;
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 10px;
        padding: 1rem 1.25rem;
        transition: border-color 0.2s ease;
    }
    div[data-testid="metric-container"]:hover {
        border-color: rgba(0,212,170,0.3);
    }
    div[data-testid="metric-container"] label {
        font-size: 0.75rem !important;
        font-weight: 500 !important;
        color: #8b949e !important;
    }
    div[data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-size: 1.75rem !important;
        font-weight: 700 !important;
        color: #f0f0f0 !important;
        font-variant-numeric: tabular-nums;
    }
    div[data-testid="metric-container"] [data-testid="stMetricDelta"] {
        font-size: 0.8rem !important;
    }

    /* 数据表格 */
    div[data-testid="stDataFrame"] {
        border-radius: 8px; overflow: hidden;
        border: 1px solid rgba(255,255,255,0.06);
    }
    div[data-testid="stDataFrame"] th {
        background: #141420 !important;
        font-size: 0.75rem !important;
        font-weight: 600 !important;
        color: #8b949e !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        padding: 0.6rem 0.75rem !important;
    }
    div[data-testid="stDataFrame"] td {
        padding: 0.4rem 0.75rem !important;
        font-size: 0.85rem !important;
        font-variant-numeric: tabular-nums;
    }

    /* 按钮 */
    div.stButton > button {
        border-radius: 8px;
        border: 1px solid rgba(255,255,255,0.08);
        font-weight: 500;
        transition: all 0.2s ease;
    }
    div.stButton > button:hover {
        border-color: rgba(0,212,170,0.5);
        box-shadow: 0 2px 12px rgba(0,212,170,0.15);
        transform: translateY(-1px);
    }
    div.stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #00d4aa, #00b894);
        color: #000 !important;
        font-weight: 600;
        border: none;
    }

    /* 进度条 */
    div.stProgress > div > div > div {
        background: linear-gradient(90deg, #00d4aa, #60a5fa);
    }

    /* 分割线 */
    hr { border-color: rgba(255,255,255,0.06) !important; margin: 1.5rem 0; }

    /* 扩展面板 */
    div.streamlit-expanderHeader {
        font-weight: 500;
        color: #8b949e;
    }

    /* 滚动条 */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0a0a0a; }
    ::-webkit-scrollbar-thumb { background: #2a2a3e; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #3a3a4e; }

    /* 标题 */
    .section-title {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e0e0e0;
        margin: 0.5rem 0;
        letter-spacing: -0.01em;
    }
    .card-title {
        font-size: 0.85rem;
        font-weight: 500;
        color: #8b949e;
        margin-bottom: 0.5rem;
    }

    /* 状态信息 */
    div.stAlert { border-radius: 8px; border: none; }
    div.stInfo { background: rgba(0,212,170,0.08) !important; color: #00d4aa !important; }
    div.stSuccess { background: rgba(0,212,170,0.1) !important; }
    div.stWarning { background: rgba(255,170,0,0.08) !important; }
    div.stError { background: rgba(255,77,77,0.08) !important; }
</style>
""", unsafe_allow_html=True)

# ── 导航栏 ──
nav_items = [
    ("📊 市场概览", "pages/1_市场概览.py"),
    ("🔍 选股扫描", "pages/2_选股扫描.py"),
    ("📈 K线分析", "pages/3_K线分析.py"),
]

# 获取当前页面路径（用于高亮）
try:
    current_page = st.query_params.get("page", ["1_市场概览"])[0]
except Exception:
    current_page = "1_市场概览"

# 提取当前文件名
import os
_current_script = os.path.basename(__file__)
_current_page = "1_市场概览"  # 默认

st.markdown(f"""
<div class="nav-container">
    <div class="nav-brand">
        <span>📊</span> 量化交易系统
    </div>
    <div class="nav-links">
        {"".join(f'<a href="{path}" class="nav-link {"active" if label.split()[1]==_current_page else ""}">{label}</a>' for label, path in nav_items)}
    </div>
</div>
""", unsafe_allow_html=True)

# ── 页面路由（使用 st.page_link 作为备用） ──
nav_cols = st.columns(3)
for i, (label, path) in enumerate(nav_items):
    with nav_cols[i]:
        st.page_link(path, label=label, use_container_width=True)
