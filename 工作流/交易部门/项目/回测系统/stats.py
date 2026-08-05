"""统计：胜率/平均R/最大回撤/SAB分列/月度分布

口径（计划"待老板确认的默认值"）：
  - 胜 = R>0；normal 全部信号参与统计；prebreak 仅触发者参与（未触发计信号数/触发率）
  - 最大回撤 = 1R 等权累计 R 曲线的最大回撤（组合口径，不做仓位资金曲线）
  - 月度分布 = 信号日按月计数（信号集中度）
所有统计为纯函数、结果稳定排序，保证可复现。
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field

from 回测系统.tracking import TrackedRecord


@dataclass
class StatBlock:
    """单个 (mode, grade, hold) 分组的统计"""

    mode: str
    grade: str
    hold: int
    n_signals: int = 0            # 信号数（全部）
    n_triggered: int = 0          # prebreak 触发数（normal = n_signals）
    trigger_rate: float = 0.0     # 触发率（prebreak；normal = 1.0）
    n_participate: int = 0        # 参与统计笔数
    n_win: int = 0                # 胜笔数（R>0）
    win_rate: float = 0.0
    total_r: float = 0.0
    avg_r: float = 0.0
    max_drawdown: float = 0.0     # 累计R曲线最大回撤
    monthly: dict = field(default_factory=OrderedDict)  # {"YYYY-MM": count}

    def overview(self) -> dict:
        """报告总览行（宽松 float 处理）"""
        return {
            "mode": self.mode, "grade": self.grade, "hold": self.hold,
            "n_signals": self.n_signals, "n_triggered": self.n_triggered,
            "trigger_rate": self.trigger_rate, "n_participate": self.n_participate,
            "n_win": self.n_win, "win_rate": self.win_rate,
            "total_r": self.total_r, "avg_r": self.avg_r, "max_drawdown": self.max_drawdown,
        }


def _safe_ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b > 0 else 0.0


def _max_drawdown_from_r(r_list: list[float]) -> float:
    """累计 R 曲线最大回撤：max(peak - trough)，全亏光也只到峰值（1R 等权口径）"""
    peak, max_dd = 0.0, 0.0
    cum = 0.0
    for r in r_list:
        cum += r
        if cum > peak:
            peak = cum
        elif peak > cum:
            max_dd = max(max_dd, peak - cum)
    return round(max_dd, 4)


def group_stats(records: list[TrackedRecord], holds: list[int]) -> dict[str, StatBlock]:
    """按 (mode, grade) × hold 全组合统计，返回 {f"{mode}|{grade}|{hold}": StatBlock}

    缺失组合补零块，保证输出表结构稳定（确定性）。
    """
    keys = set()
    for rec in records:
        keys.add((rec.signal.mode, rec.signal.grade))
    modes = sorted({rec.signal.mode for rec in records} | {"normal", "prebreak"})
    grades = sorted({rec.signal.grade for rec in records} | {"S", "A", "B"})

    buckets: dict[str, StatBlock] = {}
    for mode in modes:
        for grade in grades:
            for hold in sorted(holds):
                buckets[f"{mode}|{grade}|{hold}"] = StatBlock(mode=mode, grade=grade, hold=hold)

    # 收集每桶的参与 R 序列与月度计数
    rs: dict[str, list[float]] = {k: [] for k in buckets}
    for rec in records:
        sig = rec.signal
        for hold, oc in rec.outcomes.items():
            key = f"{sig.mode}|{sig.grade}|{hold}"
            block = buckets[key]
            block.n_signals += 1
            month = str(sig.date.to_period("M"))
            block.monthly[month] = block.monthly.get(month, 0) + 1
            if oc.participate():
                block.n_triggered += 1
                block.n_participate += 1
                rs[key].append(oc.r)
                if oc.r > 0:
                    block.n_win += 1

    for key, block in buckets.items():
        block.trigger_rate = _safe_ratio(block.n_triggered, block.n_signals)
        block.win_rate = _safe_ratio(block.n_win, block.n_participate)
        r_list = rs[key]
        block.total_r = round(sum(r_list), 4)
        block.avg_r = round(sum(r_list) / len(r_list), 4) if r_list else 0.0
        block.max_drawdown = _max_drawdown_from_r(r_list)
        block.monthly = OrderedDict(sorted(block.monthly.items()))
    return buckets


def merge_monthly(buckets: dict[str, StatBlock], mode: str) -> dict[str, int]:
    """某模式全部等级合并的月度分布（信号集中度）"""
    merged: Counter = Counter()
    for key, block in buckets.items():
        if block.mode == mode:
            for m, c in block.monthly.items():
                merged[m] += c
    return OrderedDict(sorted(merged.items()))


def mode_stats(records: list[TrackedRecord], mode: str, holds: list[int]) -> dict:
    """某模式合计统计（跨等级跨 hold，用真实 R 序列计算 avg_r/max_dd）"""
    r_list: list[float] = []
    n_sig = n_trig = n_part = n_win = 0
    for rec in records:
        if rec.signal.mode != mode:
            continue
        for hold, oc in rec.outcomes.items():
            n_sig += 1
            if oc.participate():
                n_trig += 1
                n_part += 1
                r_list.append(oc.r)
                if oc.r > 0:
                    n_win += 1
    return {
        "n_signals": n_sig,
        "n_triggered": n_trig,
        "trigger_rate": _safe_ratio(n_trig, n_sig),
        "n_participate": n_part,
        "n_win": n_win,
        "win_rate": _safe_ratio(n_win, n_part),
        "avg_r": round(sum(r_list) / len(r_list), 4) if r_list else 0.0,
        "total_r": round(sum(r_list), 4),
        "max_drawdown": _max_drawdown_from_r(r_list),
        "n_without_trigger": n_sig - n_trig,
    }
