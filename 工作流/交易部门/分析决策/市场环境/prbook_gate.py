"""财报日避让（C1 第一层 · 预约披露日）——执行层信号过滤

出处：《量化体系优化方案》（总理/待办区/待确认/2026-08-05）C1 项：
财报日避让第一层 = 预约披露日不新开仓。2026-08-05 老板确认执行（C1 定案第 3 条）。

口径（C1 定案第一层，后续层级见优化方案 C2/C3 扩展项）：
  1. 新开仓否决：信号日 T 当天是该股未披露报告期的预约披露日（first_appoint == T）
     → 不新开仓（财报当晚公布，T+1 存在跳空/业绩雷风险，否决该信号）。
  2. 持仓警示：持仓期内（T+1 ~ 出场日）跨过披露日 → 输出警示（记录到报告），
     不强制平仓——第一层设计如此，是否平仓由后续层级定夺。
  3. 已披露报告期（actual_date 早于信号日）→ 不避让（财报已出，不确定性消除；
     判定按"信号日 T 时点是否已披露"，见 _undisclosed_at）。
  4. 无该股预约披露数据 → 放行并计数（数据缺失不误杀，与 B1 missing 同哲学）。

架构铁律（评级与执行分离）：本模块只作用于"信号输出层"（match 判定后），
不改 grade() 评级计算——个股评级保持原样，披露日只是执行层否决/警示。

查询接口：复用 data_sources/store.py 查询层——
  - 实时避让（主系统）：next_prbook_dates（当前未披露报告期）；
  - 回测历史视图：prbook_rows（全部报告期含已披露，actual_date 供
    "信号日 T 时点是否已披露"判断）。
actual_date 未披露时返回空值，统一用 pd.isna 判断（质检观察项，见测试）。
"""
from __future__ import annotations

import pandas as pd
from 数据基础.duckdb.data_sources.store import prbook_rows


def load_prbook_map(codes: list[str], con=None, db_path=None) -> dict[str, list[dict]]:
    """全市场预约披露（回测历史视图：全部报告期含已披露）→ {symbol: 行列表}

    - 复用 store.prbook_rows：返回全部报告期行（含已披露），
      由 prbook_verdict/prbook_warn 按"信号日 T 时点是否已披露"判断避让——
      回测区间跨多个报告期，历史披露日必须参与匹配（next_prbook_dates 只给
      当前未披露行，是实时视图，回测会漏掉历史披露日）。
    - con 显式传入时直接用（测试注入临时库）；否则按 db_path 开只读连接。
    - 缺表/无数据/异常 → 返回空 dict（引擎按"无数据放行"处理，不误杀信号）。

    Args:
        codes: 待查股票列表（空 → 返回空 dict）
        con: 已打开的 duckdb 连接（可选，测试用）
        db_path: duckdb 库路径（可选，con 未给时用）

    Returns:
        {symbol: [{symbol, report_period, first_appoint, actual_date}, ...]}
    """
    if not codes:
        return {}
    own_con = None
    try:
        if con is None:
            own_con = _open_ro(db_path)
            con = own_con
        rows = prbook_rows(con, codes)
    except Exception:  # noqa: BLE001 - 缺表/库损坏一律视为无数据，引擎侧放行
        return {}
    finally:
        if own_con is not None:
            own_con.close()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append(r)
    return out


def _open_ro(db_path):
    """只读连接（默认主库 t017_p2.duckdb；异常由调用方兜底）"""
    from 数据基础.duckdb.data_sources.store import open_db
    return open_db(db_path, read_only=True)


# ── 判定（纯函数，可单测） ──


def _undisclosed_at(row: dict, sig_date: pd.Timestamp) -> bool:
    """该报告期在信号日 T 时点是否尚未披露

    actual_date 为空（未披露，pd.isna 判断——质检观察项）→ 未披露；
    actual_date ≥ T → 财报在 T 收盘后才公布（披露日当晚），T 时点仍未出
    → 避让；actual_date < T → 财报已出，不避让。
    """
    actual = row.get("actual_date")
    if actual is None or pd.isna(actual):
        return True
    return pd.Timestamp(actual).date() >= sig_date.date()


def prbook_verdict(rows: list[dict], sig_date: pd.Timestamp) -> tuple[str, str | None]:
    """新开仓避让判定（C1 第一层：信号日 == 预约披露日 → 否决）

    Args:
        rows: 该股预约披露行（store.prbook_rows 全量输出；first_appoint 可能缺失）
        sig_date: 信号日 T（收盘后决策；预约披露日表事前公开，无前视）

    Returns:
        ("keep", None)                    放行
        ("veto", "预约披露日 2026-08-20（报告期 2026-06-30）")  否决
    """
    for r in rows:
        first = r.get("first_appoint")
        if first is None or pd.isna(first):      # 质检观察项：未披露/缺失统一 pd.isna
            continue
        first = pd.Timestamp(first)
        if first.date() == sig_date.date() and _undisclosed_at(r, sig_date):
            return ("veto", (f"预约披露日 {first.strftime('%Y-%m-%d')}"
                             f"（报告期 {r.get('report_period')}）"))
    return ("keep", None)


def prbook_warn(rows: list[dict], sig_date: pd.Timestamp,
                exit_date: pd.Timestamp | None) -> str | None:
    """持仓警示（C1 第一层：已持仓股的披露日 → 警示，不强制平仓）

    判定：持仓期（T+1 ~ 出场日）内跨过某报告期的预约披露日（且 T 时点尚未披露）
    → 返回警示信息；出场日为空（prebreak 未触发）或无披露日 → None。

    Args:
        rows: 该股预约披露行（store.prbook_rows 全量输出）
        sig_date: 信号日 T
        exit_date: 该 hold 窗口实际出场日（跟踪结果；None = 未触发）

    Returns:
        警示文本 "2026-08-20（报告期 2026-06-30）" 或 None
    """
    if exit_date is None:
        return None
    for r in rows:
        first = r.get("first_appoint")
        if first is None or pd.isna(first):
            continue
        first = pd.Timestamp(first)
        d = first.date()
        if d > sig_date.date() and d <= exit_date.date() and _undisclosed_at(r, sig_date):
            return f"{d.strftime('%Y-%m-%d')}（报告期 {r.get('report_period')}）"
    return None
