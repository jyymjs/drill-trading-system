"""数据管理组件 - ETF统计 + 自动更新面板"""
import sys
import streamlit as st
from datetime import datetime
import subprocess
import os

from data.updater import incremental_update, update_all_stocks, get_cache_stats, MODE_SKIP, MODE_OVERWRITE
from config.stock_pool import get_all_stocks, get_etf_list


def render_data_manager():
    """渲染数据管理区域（含ETF统计和自动更新）"""
    st.markdown("### 📊 数据管理")

    stats = get_cache_stats()
    stocks = get_all_stocks()
    etfs = get_etf_list()
    total = len(stocks) + len(etfs)
    cached = stats.get("total", 0)
    pct = cached / total * 100 if total else 0

    # 统计卡片 + 缓存可视化
    col_stats, col_chart = st.columns([2, 1])
    with col_stats:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("A股", f"{len(stocks):,}")
        with c2:
            st.metric("ETF", f"{len(etfs):,}")
        with c3:
            st.metric("已缓存", f"{cached:,}" if cached else "--",
                      delta=f"{pct:.0f}%" if pct else None)
        with c4:
            st.metric("数据日期", stats.get("latest_date") or "--")

    with col_chart:
        if total > 0:
            import plotly.graph_objects as go
            fig = go.Figure(go.Pie(
                values=[cached, max(total - cached, 0)],
                labels=["已缓存", "未缓存"],
                marker_colors=["#00d4aa", "#2a2a3e"],
                hole=0.6,
                textinfo="label+percent",
                textfont=dict(size=11, color="#e0e0e0"),
                showlegend=False,
            ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=5, b=5),
                height=100,
            )
            st.plotly_chart(fig, use_container_width=True)

    # ---- 更新模式设置 ----
        col_mode, col_type, _ = st.columns([1, 1, 2])
        with col_mode:
            force = st.toggle("强制覆盖已有数据", value=False,
                           help="开启后重新下载所有已缓存数据")
        with col_type:
            include_etf = st.toggle("包含ETF", value=True,
                                  help="关闭则只更新A股股票")

        mode = MODE_OVERWRITE if force else MODE_SKIP

        # ---- 更新按钮 ----
        col_inc, col_full = st.columns(2)
        with col_inc:
            inc_btn = st.button("🚀 快速增量更新", type="primary", use_container_width=True)
        with col_full:
            full_btn = st.button("📦 全量更新（逐只）", use_container_width=True)

        # ---- 增量更新 ----
        if inc_btn:
            progress_bar = st.progress(0, text="批量拉取最新数据...")
            status_text = st.empty()
            import time as _time
            _start_t = _time.time()

            def on_progress(current, total, code, name, status):
                pct = min(current / total, 1.0)
                elapsed = _time.time() - _start_t
                remaining = (elapsed / max(current, 1)) * (total - current)
                progress_bar.progress(pct, text=f"合并中... {current}/{total} | 剩余 {remaining:.0f}s")
                icons = {"updated": "✅", "skipped": "⏭️", "failed": "❌"}
                status_text.text(f"{icons.get(status, '?')} [{current}/{total}] {code}")

            result = incremental_update(
                mode=mode, include_etf=include_etf, progress_callback=on_progress
            )

            progress_bar.progress(1.0, text="更新完成！")

            date_str = result.get("date", "?")
            st.success(
                f"✅ 增量更新完成 (数据日期: {date_str})\n"
                f"更新: {result['updated']} | 跳过: {result['skipped']} | 失败: {result['failed']}"
            )

            st.rerun()

        # ---- 全量更新 ----
        if full_btn:
            target = get_all_stocks()
            if include_etf:
                target += [{"code": e["code"], "name": e["name"]} for e in etfs]

            progress_bar = st.progress(0, text="准备全量更新...")
            status_text = st.empty()

            def on_full_progress(current, total, code, name, status):
                pct = min(current / total, 1.0)
                progress_bar.progress(pct, text=f"{current}/{total}")
                if current % 50 == 0 or current == total:
                    icons = {"updated": "✅", "skipped": "⏭️", "failed": "❌"}
                    status_text.text(f"{icons.get(status, '?')} [{current}/{total}] {code} {name}")

            result = update_all_stocks(
                target, mode=mode, progress_callback=on_full_progress
            )
            progress_bar.progress(1.0, text="全量更新完成！")
            st.success(
                f"全量更新完成!\n"
                f"更新: {result['updated']} | 跳过: {result['skipped']} | 失败: {result['failed']}"
            )
            st.rerun()

    # ---- 自动更新设置 ----
    with st.expander("⏰ 定时自动更新"):
        _render_auto_update_section()


def _render_auto_update_section():
    """渲染自动更新设置面板"""
    st.markdown("#### 🪟 Windows 定时任务")

    col1, col2 = st.columns(2)
    with col1:
        update_time = st.time_input("更新时间", value=datetime.strptime("18:00", "%H:%M").time(),
                                   help="每天这个时间自动更新数据")
    with col2:
        st.markdown("&nbsp;")

    if st.button("✅ 创建定时任务", use_container_width=True):
        time_str = update_time.strftime("%H:%M")
        try:
            script_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
            script_path = os.path.join(script_dir, "schedule_update.py")

            st.info(f"正在创建定时任务 (每天 {time_str})...")

            # 运行 schedule_update.py 创建任务
            python = sys.executable
            result = subprocess.run(
                [python, script_path, "--time", time_str],
                capture_output=True, text=True, shell=True
            )

            if result.returncode == 0:
                st.success(f"✅ 定时任务创建成功！每天 {time_str} 自动更新数据")
                st.caption(f"任务名称: 量化交易系统-数据更新")
            else:
                st.error(f"创建失败: {result.stderr}")
                st.info("可以手动运行: python scripts/schedule_update.py --time " + time_str)
        except Exception as e:
            st.error(f"出错: {e}")
            st.info("可以手动运行: python scripts/schedule_update.py --time " + time_str)

    if st.button("🗑️ 删除定时任务", use_container_width=True):
        try:
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(script_dir, "scripts", "schedule_update.py")
            python = sys.executable
            result = subprocess.run(
                [python, script_path, "--remove"],
                capture_output=True, text=True, shell=True
            )
            if result.returncode == 0:
                st.success("定时任务已删除")
            else:
                st.error(f"删除失败: {result.stderr}")
        except Exception as e:
            st.error(f"出错: {e}")

    st.divider()
    st.markdown("#### 📋 当前缓存状态")

    stats = get_cache_stats()
    st.markdown(f"""
| 项目 | 数量 |
|------|------|
| 🏢 股票缓存 | {stats.get('stock_cached', 0)} 只 |
| 📦 ETF缓存 | {stats.get('etf_cached', 0)} 只 |
| 📅 最新数据 | {stats.get('latest_date', '--')} |
""")
