#!/usr/bin/env python3
"""环境闸门 + 量能硬过滤（B1/C3 · 2026-08-05 老板拍板执行优化方案第 3 波）

出处：《量化体系优化方案》（总理/工作区/待确认/2026-08-05）
  - B1 环境闸门（最大缺口）：grade() 只审个股 6 条件，大盘暴跌/情绪冰点时照样给 S/A/B
    → 引入市场环境层，环境不利 → 信号降级或否决（"势不对不做"，参考复盘三支柱·指数支柱）
  - C3 量能硬过滤：A 股无量股多，视觉体系失真 → "无量直接不碰"成文：
    信号日日均成交额 < 阈值（建议 5000 万/日）→ 不进场
  - C4 情绪周期过滤：与 B1 同源，起步版以指数闸门为主；涨跌家数（pytdx up/down_count
    已确认可取）标注为后续扩展，不在本版实现

架构铁律（评级与执行分离）：本模块只作用于"信号输出层"（match 判定后），
不改 grade() 评级计算——个股评级保持原样，环境不利只是执行层否决/降级。

参数口径（方案 C3/C4 定案：建议值 + 回测验证，不拍脑袋）：
  - 指数跌幅阈值：-2.0%（信号日上证指数当日涨跌幅 < -2% → 环境不利）
  - 量能阈值：5000 万元/日（信号日近 5 日均成交额，含信号日）
  - 默认模式：veto（否决）；downgrade（降级 S→A/A→B/B→C）为对照可选项
"""
from dataclasses import dataclass

import pandas as pd

# 默认建议值（2026-08-05 方案建议值；回测验证后定案，见 b1c3_compare 报告）
DEFAULT_INDEX = "上证指数"
DEFAULT_DROP_PCT = -2.0        # 指数当日涨跌幅阈值（%）
DEFAULT_MIN_AMOUNT = 5000.0    # 日均成交额阈值（万元）
DEFAULT_VOL_WINDOW = 5         # 均成交额窗口（交易日，含信号日）


@dataclass
class MarketGateConfig:
    """环境闸门配置（回测参数化入口，建议值见 B1/C3/C4 定案口径）"""

    enabled: bool = False                # 环境闸门总开关
    index: str = DEFAULT_INDEX           # 主闸门指数名（INDEXES 中之一）
    drop_pct: float = DEFAULT_DROP_PCT   # 指数当日跌幅阈值（%），跌破即环境不利
    mode: str = "veto"                   # veto=一票否决 / downgrade=降一档（S→A/A→B/B→C）
    volume_filter: bool = False          # C3 量能硬过滤开关
    min_amount: float = DEFAULT_MIN_AMOUNT   # 日均成交额阈值（万元）
    vol_window: int = DEFAULT_VOL_WINDOW     # 均额窗口（交易日）
    missing_index: str = "pass"          # 信号日指数数据缺失时：pass=放行并计数 / veto=否决

    def validate(self) -> None:
        """参数校验（非法直接抛 ValueError）"""
        if self.drop_pct >= 0:
            raise ValueError(f"drop_pct 必须是负值（跌幅阈值），收到: {self.drop_pct!r}")
        if self.mode not in ("veto", "downgrade"):
            raise ValueError(f"mode 只能是 veto/downgrade，收到: {self.mode!r}")
        if self.min_amount <= 0:
            raise ValueError(f"min_amount 必须 > 0（万元），收到: {self.min_amount!r}")
        if not isinstance(self.vol_window, int) or self.vol_window < 1:
            raise ValueError(f"vol_window 必须是 ≥1 的整数，收到: {self.vol_window!r}")
        if self.missing_index not in ("pass", "veto"):
            raise ValueError(f"missing_index 只能是 pass/veto，收到: {self.missing_index!r}")


# ── 环境闸门判定（纯函数，可单测） ──


def index_pct_on(index_df: pd.DataFrame, date: pd.Timestamp) -> float | None:
    """指数在指定交易日的涨跌幅（%）

    Returns:
        涨跌幅数值；该日无数据（非交易日/数据缺口）→ None
    """
    if index_df is None or index_df.empty:
        return None
    hit = index_df[index_df["日期"] == date]
    if hit.empty or "涨跌幅" not in hit.columns:
        return None
    val = float(hit["涨跌幅"].iloc[0])
    return val if pd.notna(val) else None


def gate_verdict(cfg: MarketGateConfig, index_df: pd.DataFrame,
                 sig_date: pd.Timestamp, grade: str) -> tuple[str, str | None]:
    """环境闸门判定（B1 指数闸门起步版）

    Args:
        cfg: 闸门配置（关闭时恒放行）
        index_df: 主闸门指数日线（中文列）
        sig_date: 信号日（T 日收盘后决策，无前视）
        grade: 当前个股评级（S/A/B/C）

    Returns:
        (action, new_grade_or_reason)：
          - ("keep", None)        放行，评级不变
          - ("downgrade", "A")    环境不利 → 降一档（downgrade 模式）
          - ("veto", "原因")      环境不利 → 否决（veto 模式）
          - ("missing", "原因")   指数数据缺失 → 按 cfg.missing_index 处理
    """
    if not cfg.enabled:
        return ("keep", None)

    pct = index_pct_on(index_df, sig_date)
    if pct is None:
        # 信号日必须是指数交易日（A 股统一日历），缺失视为数据缺口
        if cfg.missing_index == "veto":
            return ("veto", f"指数数据缺失({cfg.index})")
        return ("missing", f"指数数据缺失({cfg.index})")

    if pct < cfg.drop_pct:
        reason = f"{cfg.index}当日{pct:+.2f}%跌破阈值({cfg.drop_pct}%)"
        if cfg.mode == "downgrade":
            new_grade = {"S": "A", "A": "B", "B": "C", "C": "C"}.get(grade, grade)
            return ("downgrade", new_grade)
        return ("veto", reason)
    return ("keep", None)


# ── 量能硬过滤（C3 纯函数） ──


def volume_verdict(cfg: MarketGateConfig, window: pd.DataFrame) -> tuple[str, str | None]:
    """量能硬过滤判定（C3）：信号日近 vol_window 日均成交额是否达阈值

    Args:
        cfg: 闸门配置（volume_filter 关 → 恒放行）
        window: 截至信号日的截断 K 线（无前视；需含"成交额"列，单位元）

    Returns:
        ("keep", None)      通过
        ("veto", "原因")    无量否决（日均成交额 < min_amount 万元）
        ("missing", "原因") 无成交额列/全为 0 → 数据缺口（放行并计数）
    """
    if not cfg.volume_filter:
        return ("keep", None)
    if window is None or window.empty or "成交额" not in window.columns:
        return ("missing", "无成交额数据")
    amount = window["成交额"].tail(cfg.vol_window)
    if amount.isna().all() or float(amount.sum()) <= 0:
        return ("missing", "无成交额数据")
    avg_wan = float(amount.mean()) / 1e4  # 元 → 万元
    if avg_wan < cfg.min_amount:
        return ("veto", f"日均成交额{avg_wan:.0f}万 < {cfg.min_amount:.0f}万")
    return ("keep", None)


# ── 汇总判定（引擎挂载点：评级与执行分离） ──


def exec_verdict(cfg: MarketGateConfig, index_df: pd.DataFrame,
                 sig_date: pd.Timestamp, grade: str, window: pd.DataFrame,
                 ) -> tuple[str, str | None, str]:
    """执行层总判定：环境闸门 → 量能过滤（顺序固定，均为执行层，不动评级核心）

    Returns:
        (action, info, src)：
          ("keep", None, "none")                 放行
          ("downgrade", new_grade, "env")        环境不利 → 降一档
          ("veto", reason, "env"|"volume")       否决（环境或量能）
          ("missing", reason, "env"|"volume")    数据缺口放行（指数/成交额）
    """
    if not cfg.enabled and not cfg.volume_filter:
        return ("keep", None, "none")
    g_action, g_info = gate_verdict(cfg, index_df, sig_date, grade)
    if g_action == "veto":
        return (g_action, g_info, "env")
    # downgrade：先降级，再看量能（量能仍不过 → 否决）
    v_action, v_info = volume_verdict(cfg, window)
    if v_action == "veto":
        return (v_action, v_info, "volume")
    if v_action == "missing":
        return (v_action, v_info, "volume")
    if g_action == "downgrade":
        return (g_action, g_info, "env")
    if g_action == "missing":
        # 指数数据缺口：按 missing_index 策略处理（pass=放行）
        if cfg.missing_index == "veto":
            return ("veto", g_info, "env")
        return (g_action, g_info, "env")
    return ("keep", None, "none")


# ── 闸门过滤计数（引擎侧 dict 口径，报告呈现用） ──
# 键：veto_env（环境否决）/ veto_volume（量能否决）/ downgraded（环境降级）/
#     missing（数据缺口放行）/ kept（正常放行）——见 engine.BacktestEngine.gate_counts
