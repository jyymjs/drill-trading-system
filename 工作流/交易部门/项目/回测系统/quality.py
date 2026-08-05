"""回测质检：D1 前后半一致性 + D2 2倍成本压力（方案 D 类 2026-08-05 老板拍板）

D1 前后半一致性检查：回测区间按信号日时间中点切前后两半（各 ≥1.5 年，覆盖牛熊）；
  分别算两半累计 R（1R 等权，与 stats.mode_stats 同口径）：
  - 两半都正 → ✅ 一致性合格
  - 前正后负 → ⚠️ 过拟合嫌疑（标黄）
  - 前负后正 → 正常接受（风格适应慢，不判罪）
  - 两半皆负 → ⚠️ 整体亏损
  - S 级策略必须前后半同为正（must_both_positive）
  - 时长约束：回测区间 <3 年 → 检查不成立；任一半 <1.5 年 → 参考性弱

D2 2倍成本压力测试：佣金万1.3+印花税万5+滑点全 ×2（万2.6+万10+滑点翻倍万2）重跑
  （engine cost_multiplier=2.0，main.py 双跑）；2 倍成本下年化 R 仍为正 → ✅ 抗压合格，
  ≤0 → ⚠️ 标黄（利润太薄，实盘必亏）。

本模块只做纯函数分析，不写文件；渲染在 report.py。
"""
from __future__ import annotations

import pandas as pd

from 回测系统.stats import _max_drawdown_from_r
from 回测系统.tracking import TrackedRecord

# D1 时长约束（方案：回测区间需 ≥3 年，各半 ≥1.5 年）
MIN_TOTAL_YEARS = 3.0
MIN_HALF_YEARS = 1.5
DAYS_PER_YEAR = 365.25

OK = "✅"
WARN = "⚠️"


# ── 公共：参与 R 序列（1R 等权，与 stats 口径一致）──

def participating_r(records: list[TrackedRecord], mode: str | None = None,
                    grade: str | None = None) -> list[tuple[pd.Timestamp, float]]:
    """参与统计的 (信号日, R) 序列，按 (信号日, code, hold) 升序（normal 全参与；
    prebreak 仅触发者参与）

    与 stats.mode_stats 同一口径：跨 hold 合并的 1R 等权 R 序列。
    2026-08-06 质检发现：仅按信号日排序时，同日多笔/同股多 hold 的次序仍取决于
    records 传入顺序（进程池并发下随机），导致下游最大回撤不可复现 → 排序键补全序。
    """
    pairs: list[tuple[pd.Timestamp, str, int, float]] = []
    for rec in records:
        sig = rec.signal
        if mode is not None and sig.mode != mode:
            continue
        if grade is not None and sig.grade != grade:
            continue
        for hold, oc in rec.outcomes.items():
            if oc.participate():
                pairs.append((sig.date, sig.code, hold, oc.r))
    pairs.sort(key=lambda p: (p[0], p[1], p[2]))
    return [(p[0], p[3]) for p in pairs]


def span_days(records: list[TrackedRecord], mode: str | None = None) -> float:
    """信号日期跨度（天）：末信号日 - 首信号日"""
    pairs = participating_r(records, mode)
    if len(pairs) < 2:
        return 0.0
    return float((pairs[-1][0] - pairs[0][0]).days)


def annualized_r(total_r: float, span_days: float) -> float:
    """年化 R = 累计 R / 回测年数（1R 等权累计口径）"""
    if span_days <= 0:
        return 0.0
    return total_r / (span_days / DAYS_PER_YEAR)


# ── D1 前后半一致性 ──

def _judge_halves(front_total: float, back_total: float,
                  must_both_positive: bool = False) -> tuple[str, str]:
    """两半累计 R 判定（must_both_positive=True 用于 S 级硬要求）"""
    if must_both_positive:
        if front_total > 0 and back_total > 0:
            return OK, "S 级前后半同为正，一致性合格"
        return WARN, "S 级未达'前后半同为正'要求（S 级策略硬性门槛）"
    if front_total > 0 and back_total > 0:
        return OK, "一致性合格：两半累计 R 同为正"
    if front_total > 0 and back_total <= 0:
        return WARN, "过拟合嫌疑：前正后负（收益集中在后半区间，防过拟合红线）"
    if front_total <= 0 and back_total > 0:
        return "正常", "正常接受：风格适应慢（前负后正），不判罪"
    return WARN, "整体亏损：两半累计 R 皆非正，策略本身不合格"


def check_half_consistency(records: list[TrackedRecord], holds: list[int],
                           mode: str | None = None, grade: str | None = None) -> dict:
    """D1 前后半一致性检查（纯函数）

    Args:
        records: 引擎产出记录
        holds: 观察窗（参与序列跨 hold 合并，与 stats 口径一致）
        mode: 过滤模式（None=全部）
        grade: 过滤评级（"S" 时启用"必须前后半同为正"硬门槛）

    Returns:
        dict: 区间起止/中点/前后半累计R/时长/判定/说明（report 渲染用）
    """
    pairs = participating_r(records, mode, grade)
    if not pairs:
        return {"ok": False, "verdict": "跳过", "reason": "无参与统计信号",
                "front_total_r": 0.0, "back_total_r": 0.0,
                "front_days": 0.0, "back_days": 0.0, "total_days": 0.0,
                "start": None, "end": None, "mid": None}

    start_d = pairs[0][0]
    end_d = pairs[-1][0]
    mid = start_d + (end_d - start_d) / 2          # 时间中点（含时分）
    front = [r for d, r in pairs if d < mid]
    back = [r for d, r in pairs if d >= mid]
    front_total = round(sum(front), 4)
    back_total = round(sum(back), 4)
    total_days = float((end_d - start_d).days)
    front_days = float((mid - start_d).days)
    back_days = float((end_d - mid).days)

    verdict, reason = _judge_halves(front_total, back_total,
                                    must_both_positive=(grade == "S"))
    # 时长约束：整体 <3 年 → 检查不成立（不下两半结论）；任一半 <1.5 年 → 参考性弱
    if total_days / DAYS_PER_YEAR < MIN_TOTAL_YEARS:
        verdict = WARN
        reason = f"回测区间 {total_days / DAYS_PER_YEAR:.1f} 年 < {MIN_TOTAL_YEARS:.0f} 年，D1 检查不成立，无法判定"
    elif front_days / DAYS_PER_YEAR < MIN_HALF_YEARS or back_days / DAYS_PER_YEAR < MIN_HALF_YEARS:
        reason = f"{reason}；注意：任一半不足 {MIN_HALF_YEARS} 年（前 {front_days / DAYS_PER_YEAR:.1f} 年 / 后 {back_days / DAYS_PER_YEAR:.1f} 年），参考性弱"

    return {
        "ok": verdict == OK,
        "verdict": verdict, "reason": reason,
        "front_total_r": front_total, "back_total_r": back_total,
        "front_days": front_days, "back_days": back_days, "total_days": total_days,
        "start": start_d.date().isoformat(), "end": end_d.date().isoformat(),
        "mid": mid.date().isoformat(),
    }


# ── D2 2倍成本压力 ──

def check_cost_stress(base_records: list[TrackedRecord],
                      stress_records: list[TrackedRecord],
                      holds: list[int], mode: str | None = None) -> dict:
    """D2 2倍成本压力对比（纯函数）

    Args:
        base_records: 基线（1 倍成本）引擎产出
        stress_records: 2 倍成本（cost_multiplier=2.0）重跑产出
        holds: 观察窗
        mode: 过滤模式（None=全部）

    Returns:
        dict: 两条口径的 参与笔数/累计R/年化R/平均R/最大回撤 + 判定
    """
    base_pairs = participating_r(base_records, mode)
    stress_pairs = participating_r(stress_records, mode)
    days = span_days(base_records, mode)

    base_rs = [r for _, r in base_pairs]
    stress_rs = [r for _, r in stress_pairs]
    base_total = round(sum(base_rs), 4)
    stress_total = round(sum(stress_rs), 4)
    base_annual = round(annualized_r(base_total, days), 4)
    stress_annual = round(annualized_r(stress_total, days), 4)
    years = round(days / DAYS_PER_YEAR, 2)

    if not stress_pairs:
        verdict, reason = WARN, "2 倍成本重跑无参与信号，压力测试无法判定"
    elif stress_annual > 0:
        verdict, reason = OK, "抗压合格：2 倍成本下年化 R 仍为正，利润有安全垫"
    else:
        verdict, reason = WARN, "利润太薄：2 倍成本下年化 R ≤ 0，实盘必亏（成本敏感，需提高策略质量）"

    return {
        "ok": verdict == OK,
        "verdict": verdict, "reason": reason,
        "years": years,
        "base": {"n_participate": len(base_pairs), "total_r": base_total,
                 "annual_r": base_annual,
                 "avg_r": round(base_total / len(base_pairs), 4) if base_pairs else 0.0,
                 "max_drawdown": _max_drawdown_from_r(base_rs)},
        "stress": {"n_participate": len(stress_pairs), "total_r": stress_total,
                   "annual_r": stress_annual,
                   "avg_r": round(stress_total / len(stress_pairs), 4) if stress_pairs else 0.0,
                   "max_drawdown": _max_drawdown_from_r(stress_rs)},
    }
