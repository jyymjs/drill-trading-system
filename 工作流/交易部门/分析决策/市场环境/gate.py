#!/usr/bin/env python3
"""环境闸门 + 量能硬过滤 + 情绪闸门（B1/C3/C4 · 2026-08-05 老板拍板执行优化方案第 3 波）

出处：《量化体系优化方案》（总理/工作区/待确认/2026-08-05）
  - B1 环境闸门（最大缺口）：grade() 只审个股 6 条件，大盘暴跌/情绪冰点时照样给 S/A/B
    → 引入市场环境层，环境不利 → 信号降级或否决（"势不对不做"，参考复盘三支柱·指数支柱）
  - C3 量能硬过滤：A 股无量股多，视觉体系失真 → "无量直接不碰"成文：
    信号日日均成交额 < 阈值（建议 5000 万/日）→ 不进场
  - C4 情绪闸门（普跌日盲区实证）：2026-05-29 全市场 71.4% 股票跌但上证仅 -0.73%，
    21 笔信号全亏 -20.3R（指数闸门管不了"家数普跌"）→ 增加涨跌家数维度：
    信号日全市场下跌家数占比 > 阈值（建议 70%）→ 环境否决。
    涨跌家数数据源：pytdx get_index_bars 自带 up/down_count（含历史），
    见 index_data.load_market_breadth（2026-08-05 实测：该日 71.6%，对账一致）。

架构铁律（评级与执行分离）：本模块只作用于"信号输出层"（match 判定后），
不改 grade() 评级计算——个股评级保持原样，环境不利只是执行层否决/降级。

闸门组合（并列否决）：指数闸门与情绪闸门为两个独立维度、任一触发即否决
（2026-08-05 老板拍板 C4：普跌日 90% 信号同亏，情绪闸门固定 veto 语义，不做降级）。

参数口径（方案 C3/C4 定案：建议值 + 回测验证，不拍脑袋）：
  - 指数跌幅阈值：-2.0%（信号日上证指数当日涨跌幅 < -2% → 环境不利）
  - 量能阈值：5000 万元/日（信号日近 5 日均成交额，含信号日）
  - 情绪阈值：70%（信号日全市场下跌家数占比 > 70% → 环境否决；
    参考 2026-05-29 实证 71.4%，回测验证后定案，见 c4_sentiment_compare 报告）
  - 默认模式：veto（否决）；downgrade（降级 S→A/A→B/B→C）为对照可选项
"""
from dataclasses import dataclass

import pandas as pd

# 默认建议值（2026-08-05 方案建议值；回测验证后定案，见 b1c3_compare/c4_sentiment_compare 报告）
DEFAULT_INDEX = "上证指数"
DEFAULT_DROP_PCT = -2.0        # 指数当日涨跌幅阈值（%）
DEFAULT_MIN_AMOUNT = 5000.0    # 日均成交额阈值（万元）
DEFAULT_VOL_WINDOW = 5         # 均成交额窗口（交易日，含信号日）
DEFAULT_SENT_THRESHOLD = 70.0  # C4 下跌家数占比阈值（%，建议值；2026-05-29 实证 71.4%）


@dataclass
class MarketGateConfig:
    """环境闸门配置（回测参数化入口，建议值见 B1/C3/C4 定案口径）"""

    enabled: bool = False                # 指数闸门总开关（B1）
    index: str = DEFAULT_INDEX           # 主闸门指数名（INDEXES 中之一）
    drop_pct: float = DEFAULT_DROP_PCT   # 指数当日跌幅阈值（%），跌破即环境不利
    mode: str = "veto"                   # veto=一票否决 / downgrade=降一档（S→A/A→B/B→C）
    volume_filter: bool = False          # C3 量能硬过滤开关
    min_amount: float = DEFAULT_MIN_AMOUNT   # 日均成交额阈值（万元）
    vol_window: int = DEFAULT_VOL_WINDOW     # 均额窗口（交易日）
    missing_index: str = "pass"          # 信号日指数数据缺失时：pass=放行并计数 / veto=否决
    sentiment_gate: bool = False         # C4 情绪闸门开关（涨跌家数维度，与指数闸门并列）
    sent_threshold: float = DEFAULT_SENT_THRESHOLD  # 全市场下跌家数占比阈值（%），严格大于才触发
    missing_sentiment: str = "pass"      # 信号日涨跌家数缺失时：pass=放行并计数 / veto=否决

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
        if not (0 < self.sent_threshold <= 100):
            raise ValueError(f"sent_threshold 必须是 (0,100] 的占比百分比，收到: {self.sent_threshold!r}")
        if self.missing_sentiment not in ("pass", "veto"):
            raise ValueError(f"missing_sentiment 只能是 pass/veto，收到: {self.missing_sentiment!r}")


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


# ── 情绪闸门判定（C4 纯函数） ──


def breadth_ratio_on(breadth_df: pd.DataFrame, date: pd.Timestamp) -> float | None:
    """全市场下跌家数占比在指定交易日（%）

    Returns:
        下跌占比数值（0~100）；该日无数据（非交易日/数据缺口）→ None
    """
    if breadth_df is None or breadth_df.empty:
        return None
    hit = breadth_df[breadth_df["日期"] == date]
    if hit.empty or "下跌占比" not in hit.columns:
        return None
    val = float(hit["下跌占比"].iloc[0])
    return val if pd.notna(val) else None


def sentiment_verdict(cfg: MarketGateConfig, breadth_df: pd.DataFrame,
                      sig_date: pd.Timestamp) -> tuple[str, str | None]:
    """情绪闸门判定（C4 普跌日盲区补盲）：全市场下跌家数占比 > 阈值 → 环境否决

    并列否决语义（2026-08-05 老板拍板）：与指数闸门独立、任一触发即否决；
    普跌日全市场 90% 信号同亏（2026-05-29 实证 -20.3R），降级挡不住 → 固定 veto，
    不提供 downgrade 模式。

    Args:
        cfg: 闸门配置（sentiment_gate 关 → 恒放行）
        breadth_df: 全市场涨跌家数日线（load_market_breadth 产出，需"下跌占比"列）
        sig_date: 信号日（T 日收盘后决策，无前视）

    Returns:
        ("keep", None)        放行
        ("veto", "原因")      普跌日否决（下跌占比超阈值）
        ("missing", "原因")  涨跌家数缺失 → 按 cfg.missing_sentiment 处理
    """
    if not cfg.sentiment_gate:
        return ("keep", None)
    ratio = breadth_ratio_on(breadth_df, sig_date)
    if ratio is None:
        if cfg.missing_sentiment == "veto":
            return ("veto", "涨跌家数数据缺失")
        return ("missing", "涨跌家数数据缺失")
    if ratio > cfg.sent_threshold:
        return ("veto", f"全市场下跌家数占比{ratio:.1f}% > 阈值({cfg.sent_threshold:.0f}%)")
    return ("keep", None)


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
                 breadth_df: pd.DataFrame | None = None,
                 ) -> tuple[str, str | None, str]:
    """执行层总判定：指数闸门 → 情绪闸门 → 量能过滤（均为执行层，不动评级核心）

    并列否决（C4）：指数闸门与情绪闸门独立判定、任一触发即否决
    （普跌日例：2026-05-29 上证仅 -0.73% 指数闸门放行，但下跌家数占比 71.6%
    超 70% 阈值 → 情绪闸门否决）。

    Returns:
        (action, info, src)：
          ("keep", None, "none")                  放行
          ("downgrade", new_grade, "env")         指数环境不利 → 降一档
          ("veto", reason, "env"|"sentiment"|"volume")  否决（指数/情绪/量能）
          ("missing", reason, "env"|"sentiment"|"volume") 数据缺口放行（指数/家数/成交额）
    """
    if not cfg.enabled and not cfg.volume_filter and not cfg.sentiment_gate:
        return ("keep", None, "none")
    g_action, g_info = gate_verdict(cfg, index_df, sig_date, grade)
    if g_action == "veto":
        return (g_action, g_info, "env")
    s_action, s_info = sentiment_verdict(cfg, breadth_df, sig_date)
    if s_action == "veto":
        return (s_action, s_info, "sentiment")
    # downgrade：先降级，再看量能（量能仍不过 → 否决）
    v_action, v_info = volume_verdict(cfg, window)
    if v_action == "veto":
        return (v_action, v_info, "volume")
    # 量能缺口（P1 质检修复 2026-08-06）：不立即 return，先走完环境降级分支——
    # 原逻辑 v_action=="missing" 提前短路，会吞掉 downgrade 模式的指数降级
    # （数据缺口放行 ≠ 环境有利；降级语义不应被量能缺数吞掉）。
    v_missing = v_info if v_action == "missing" else None
    if g_action == "downgrade":
        return (g_action, g_info, "env")
    if g_action == "missing":
        # 指数数据缺口：按 missing_index 策略处理（pass=放行）
        if cfg.missing_index == "veto":
            return ("veto", g_info, "env")
        return (g_action, g_info, "env")
    if s_action == "missing":
        # 涨跌家数数据缺口：按 missing_sentiment 策略处理（pass=放行）
        if cfg.missing_sentiment == "veto":
            return ("veto", s_info, "sentiment")
        return (s_action, s_info, "sentiment")
    if v_missing is not None:
        # 环境/情绪均放行（无降级/无缺口）时，量能缺口才独立返回（放行并计数语义不变）
        return ("missing", v_missing, "volume")
    return ("keep", None, "none")


# ── 闸门过滤计数（引擎侧 dict 口径，报告呈现用） ──
# 键：veto_env（指数否决）/ veto_sentiment（情绪否决）/ veto_volume（量能否决）/
#     downgraded（指数降级）/ missing（数据缺口放行）/ kept（正常放行）
#     ——见 engine.BacktestEngine.gate_counts
