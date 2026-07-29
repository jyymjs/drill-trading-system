"""页面2: 选股扫描（双栏布局 + 交互升级）"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="选股扫描", page_icon="🔍", layout="wide")
st.markdown('<p class="section-title">🔍 选股扫描</p>', unsafe_allow_html=True)

# ── 双栏布局 ──
left_col, right_col = st.columns([1, 3])

with left_col:
    st.markdown('<p class="card-title">⚙️ 策略配置</p>', unsafe_allow_html=True)
    try:
        from web.strategy_config import select_strategy, show_strategy_info
        strategy = select_strategy(key="scan_strategy")
        if strategy:
            with st.expander("策略详情", expanded=False):
                show_strategy_info(strategy)
    except Exception as e:
        st.error(f"策略模块加载失败: {e}")
        strategy = None

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<p class="card-title">📋 扫描范围</p>', unsafe_allow_html=True)
    security_type = st.radio(
        "选择范围",
        options=["stock", "etf", "all"],
        format_func={"stock": "📈 A股股票", "etf": "📦 ETF", "all": "📊 全部"}.get,
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("<br>", unsafe_allow_html=True)
    scan_btn = st.button("🚀 开始扫描", type="primary", use_container_width=True)
    st.caption(f"🕐 {datetime.now().strftime('%m-%d %H:%M')}")

with right_col:
    if not strategy:
        st.info("👈 请在左侧面板选择策略", icon="💡")
        st.stop()

    st.markdown(f"""
    <div style="
        background: #141420; border: 1px solid rgba(255,255,255,0.06);
        border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem;
        display: flex; justify-content: space-between; align-items: center;
    ">
        <div style="display: flex; gap: 1.5rem; align-items: center;">
            <span style="font-weight: 600; color: #00d4aa;">{strategy.name}</span>
            <span style="font-size: 0.8rem; color: #8b949e;">{['A股','ETF','全部'][['stock','etf','all'].index(security_type)]}</span>
        </div>
        <span style="font-size: 0.75rem; color: #8b949e;">{strategy.description}</span>
    </div>
    """, unsafe_allow_html=True)

    # 初始化 session state
    if "scan_results" not in st.session_state:
        st.session_state["scan_results"] = None
    if "scan_time" not in st.session_state:
        st.session_state["scan_time"] = None

    if scan_btn:
        progress_bar = st.progress(0, text="准备扫描...")
        status_text = st.empty()
        start_time = time.time()

        def on_progress(current, total, name=""):
            pct = min(current / total, 1.0)
            elapsed = time.time() - start_time
            remaining = (elapsed / max(current, 1)) * (total - current)
            progress_bar.progress(
                pct,
                text=f"扫描中... {current}/{total} | 已用 {elapsed:.0f}s | 预估剩余 {remaining:.0f}s"
            )

        try:
            from analysis.scanner import scan
            results = scan(strategy, progress_callback=on_progress,
                           show_progress=False, security_type=security_type)
            elapsed = time.time() - start_time
            progress_bar.progress(1.0, text=f"扫描完成！耗时 {elapsed:.1f}s")

            st.session_state["scan_results"] = results
            st.session_state["scan_time"] = elapsed

            if results:
                st.success(f"✅ 共 {len(results)} 只符合条件 (耗时 {elapsed:.1f}s)")
            else:
                st.warning("当前没有符合条件的标的")
        except Exception as e:
            st.error(f"扫描出错: {e}")

    # 显示结果
    if st.session_state["scan_results"] is not None:
        results = st.session_state["scan_results"]
        elapsed = st.session_state.get("scan_time", 0)

        # 结果摘要
        summary_cols = st.columns(4)
        with summary_cols[0]:
            st.metric("符合条件", len(results))
        with summary_cols[1]:
            st.metric("扫描耗时", f"{elapsed:.1f}s" if elapsed else "--")
        with summary_cols[2]:
            if results:
                avg_gain = sum(r.get("涨幅%", 0) for r in results) / len(results)
                st.metric("平均涨幅", f"{avg_gain:+.2f}%")
        with summary_cols[3]:
            max_gain = max((r.get("涨幅%", 0) for r in results), default=0)
            st.metric("最大涨幅", f"{max_gain:+.2f}%")

        st.markdown("<br>", unsafe_allow_html=True)

        # 结果表格（含条件格式）
        if results:
            display_data = []
            for i, r in enumerate(results, 1):
                gain = r.get("涨幅%", 0)
                gain_str = f'{gain:+.2f}%'
                gain_color = "#ff4d4d" if gain >= 0 else "#26a69a"

                grade = r.get("评级", "?")
                grade_colors = {"S": "#00d4aa", "A": "#60a5fa", "B": "#ffa726", "C": "#8b949e"}
                gc = grade_colors.get(grade, "#8b949e")
                display_data.append({
                    "序号": i,
                    "评级": grade,
                    "评级颜色": gc,
                    "代码": r.get("code", ""),
                    "名称": r.get("name", ""),
                    "收盘": f'{r.get("price", 0):.2f}',
                    "涨幅": gain_str,
                    "涨幅值": gain,
                    "换手率": f'{r.get("换手率%", 0):.2f}%' if r.get("换手率%") else "--",
                    "RSI": r.get("RSI", "--"),
                    "MA5": f'{r.get("MA5", 0):.2f}' if r.get("MA5") else "--",
                    "MA20": f'{r.get("MA20", 0):.2f}' if r.get("MA20") else "--",
                })

            df_display = pd.DataFrame(display_data)

            # 条件格式：涨幅列着色
            col_config = {
                "序号": st.column_config.NumberColumn("序号", width="small"),
                "代码": st.column_config.TextColumn("代码", width="small"),
                "名称": st.column_config.TextColumn("名称"),
                "收盘": st.column_config.TextColumn("收盘", width="small"),
                "涨幅": st.column_config.TextColumn(
                    "涨幅",
                    width="small",
                    help="红色=上涨 绿色=下跌",
                ),
                "涨幅值": st.column_config.NumberColumn("涨幅值", width="small"),
                "换手率": st.column_config.TextColumn("换手率", width="small"),
                "RSI": st.column_config.TextColumn("RSI", width="small"),
                "MA5": st.column_config.TextColumn("MA5", width="small"),
                "MA20": st.column_config.TextColumn("MA20", width="small"),
            }

            # 用 HTML 渲染带颜色的表格
            st.markdown("""
            <style>
                .scan-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
                .scan-table th {
                    background: #141420; color: #8b949e;
                    font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                    padding: 0.6rem 0.75rem; text-align: left;
                    border-bottom: 1px solid rgba(255,255,255,0.06);
                }
                .scan-table td { padding: 0.4rem 0.75rem; border-bottom: 1px solid rgba(255,255,255,0.03); }
                .scan-table tr:hover td { background: rgba(255,255,255,0.02); }
                .gain-up { color: #ff4d4d; font-weight: 600; }
                .gain-down { color: #26a69a; font-weight: 600; }
            </style>
            """, unsafe_allow_html=True)

            rows_html = ""
            for _, row in df_display.iterrows():
                gain_class = "gain-up" if row["涨幅值"] >= 0 else "gain-down"
                gc = row["评级颜色"]
                rows_html += f"""
                <tr>
                    <td>{row['序号']}</td>
                    <td><span style="display:inline-block;background:{gc};color:#000;font-weight:700;font-size:0.7rem;padding:0.1rem 0.4rem;border-radius:4px;">{row['评级']}</span></td>
                    <td><a href="/3_K线分析?symbol={row['代码']}" target="_self" style="color:#60a5fa;text-decoration:none;">{row['代码']}</a></td>
                    <td>{row['名称']}</td>
                    <td>{row['收盘']}</td>
                    <td class="{gain_class}">{row['涨幅']}</td>
                    <td>{row['换手率']}</td>
                    <td>{row['RSI']}</td>
                    <td>{row['MA5']}</td>
                    <td>{row['MA20']}</td>
                </tr>
                """

            st.markdown(f"""
            <table class="scan-table">
                <thead><tr>
                    <th>#</th><th>评级</th><th>代码</th><th>名称</th>
                    <th>收盘</th><th>涨幅</th><th>换手率</th>
                    <th>RSI</th><th>MA5</th><th>MA20</th>
                </tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
            """, unsafe_allow_html=True)

            st.caption(f"共 {len(results)} 只符合条件 | 点击代码跳转K线分析")

            # 导出 CSV
            csv = df_display.drop(columns=["涨幅值"]).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label="📥 下载 CSV",
                data=csv,
                file_name=f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True,
            )
