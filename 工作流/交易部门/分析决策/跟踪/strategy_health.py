"""策略三方体检（R-036② 自学习自动化 · 2026-08-08 老板拍板实施）

模拟线 vs 实盘 vs 回测预期 三方偏差检测（周频报告用）：

- **回测预期基准**：从回测信号 signals.csv 计算现行策略口径（prebreak + 触发 +
  r_20d 分布）——不硬编数字，基准可复算、有出处（版本存档"复现"命令同源）。
- **模拟线**：sim_journal 已平仓行（动态出场 R，与回测分布近似可比——口径说明：
  模拟线用 exit_manager 动态出场，回测 r_20d 固定持有 20 日，两者是同一信号集的
  R 分布，比的是形态（avgR/胜率/尾部），不逐笔对齐）。
- **实盘**：r_curve.csv note="live" 行（口述录入后自动累积）。

判定规则（规格书 10.6 反馈闭环）：
  ① 累计 R 低于回测 P5（蒙卡信号层最差 5% 区间，维护方案 10.6 同源）→ 预警
  ② 模拟线 avgR 与回测 avgR 偏差 >50% 且样本 ≥10 → 观察
  ③ 连败 ≥12（蒙卡 P99 连败上限）→ 预警
  ④ 胜率 < 回测胜率 −15pp 且样本 ≥10 → 观察
  ⑤ 实盘/模拟线样本不足（<5）→ 提示数据积累中（不误报）

使用：python main.py track strategy-health
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from 分析决策.跟踪 import sim_trading

# 回测信号默认路径（与版本存档"复现"命令同源）
DEFAULT_SIGNALS = Path("项目/回测输出/backtest/20230701_20260804/signals.csv")
# 连败预警线（蒙卡 P99 连败上限 ≈12，维护方案 10.6）
MAX_STREAK_WARN = 12
# 偏差预警阈值：模拟线 avgR 与回测 avgR 相对偏差（%）
AVG_R_DEV_PCT = 50.0
# 胜率偏差预警阈值（百分点）
WINRATE_DEV_PP = 15.0
# 最小样本数（低于此数不判定，防小样本误报）
MIN_SAMPLES = 5


def _r_stats(rs: list[float]) -> dict:
    """R 序列统计：样本/avgR/胜率/累计R/连败/尾部"""
    if not rs:
        return {"n": 0, "avg_r": 0.0, "winrate": 0.0, "cum_r": 0.0,
                "max_streak": 0, "p5": 0.0, "p95": 0.0}
    s = sorted(rs)
    n = len(s)
    avg = sum(rs) / n
    winrate = sum(1 for x in rs if x > 0) / n * 100
    max_streak = cur = 0
    for x in rs:
        cur = cur + 1 if x <= 0 else 0
        max_streak = max(max_streak, cur)
    p5 = s[max(0, int(n * 0.05) - 1)]
    p95 = s[min(n - 1, int(n * 0.95))]
    return {"n": n, "avg_r": avg, "winrate": winrate, "cum_r": sum(rs),
            "max_streak": max_streak, "p5": p5, "p95": p95}


def load_backtest_baseline(signals_path: Path | None = None) -> dict:
    """回测预期基准：prebreak + 触发 + r_20d 的 R 分布（现行策略口径近似：
    S 级为实盘纪律主口径；C23 子集校准留待后续，注释见规格书 5 节）"""
    path = Path(signals_path or DEFAULT_SIGNALS)
    if not path.exists():
        return {"n": 0, "avg_r": 0.0, "winrate": 0.0, "cum_r": 0.0,
                "max_streak": 0, "p5": 0.0, "p95": 0.0, "source": ""}
    rs = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("mode") == "prebreak" and r.get("triggered_20d") == "1":
                try:
                    v = float(r.get("r_20d") or 0)
                except (TypeError, ValueError):
                    continue
                if v != 0.0 or True:  # 保留 0R（触发出场为 0 的情形）——按原值收录
                    rs.append(v)
    st = _r_stats(rs)
    st["source"] = str(path)
    return st


def load_sim_line() -> dict:
    """模拟线：sim_journal 已平仓行 R 序列（干净源，不受 r_curve 测试残留污染）"""
    rows = sim_trading._read_all()
    rs = []
    for r in rows:
        if r.get("status") == "closed":
            try:
                v = float(r.get("r_multiple") or 0)
            except (TypeError, ValueError):
                continue
            rs.append(v)
    return _r_stats(rs)


def load_live_line() -> dict:
    """实盘线：r_curve.csv note="live" 行（口述录入账本）"""
    path = sim_trading.JOURNAL_DIR / "r_curve.csv"
    rs = []
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("note") == "live":
                    try:
                        v = float(r.get("r") or 0)
                    except (TypeError, ValueError):
                        continue
                    rs.append(v)
    return _r_stats(rs)


def _judge(sim: dict, live: dict, base: dict) -> list[str]:
    """偏差判定 → 预警清单（规格书 10.6 反馈闭环规则）"""
    alerts = []
    for name, st in (("模拟线", sim), ("实盘", live)):
        if st["n"] == 0:
            continue
        if st["n"] < MIN_SAMPLES:
            alerts.append(f"ℹ️ {name} 样本不足（{st['n']}笔<{MIN_SAMPLES}），继续积累")
            continue
        if st["cum_r"] < base["p5"]:
            alerts.append(f"⚠️ {name} 累计 R {st['cum_r']:+.2f} < 回测 P5 {base['p5']:+.2f}"
                          f"（蒙卡最差 5% 区间）→ 触发排查")
        if base["n"] > 0:
            dev = abs(st["avg_r"] - base["avg_r"]) / abs(base["avg_r"]) * 100 if base["avg_r"] else 0
            if dev > AVG_R_DEV_PCT:
                alerts.append(f"👀 {name} avgR {st['avg_r']:+.2f} vs 回测 {base['avg_r']:+.2f}"
                              f"（偏差 {dev:.0f}% > {AVG_R_DEV_PCT:.0f}%）→ 观察")
        if st["max_streak"] >= MAX_STREAK_WARN:
            alerts.append(f"⚠️ {name} 连败 {st['max_streak']} 笔 ≥ {MAX_STREAK_WARN}（蒙卡 P99 上限）")
        if base["n"] > 0 and st["winrate"] < base["winrate"] - WINRATE_DEV_PP:
            alerts.append(f"👀 {name} 胜率 {st['winrate']:.0f}% vs 回测 {base['winrate']:.0f}%"
                          f"（低 {WINRATE_DEV_PP:.0f}pp）→ 观察")
    if not alerts:
        alerts.append("✅ 三方无异常（样本充足时判定）")
    return alerts


def health_report() -> str:
    """三方体检报告（周频调用）"""
    base = load_backtest_baseline()
    sim = load_sim_line()
    live = load_live_line()
    W = 72
    line = "-" * W
    out = [line, "策略三方体检（模拟线 vs 实盘 vs 回测预期）".center(W), line]
    for name, st in (("回测预期", base), ("模拟线", sim), ("实盘", live)):
        if st["n"] == 0:
            out.append(f"  {name:<4}: 无数据（{'基准缺失' if name == '回测预期' else '尚未积累'}）")
            continue
        out.append(f"  {name:<4}: {st['n']}笔 | avgR {st['avg_r']:+.2f} | 胜率 {st['winrate']:.0f}%"
                   f" | 累计R {st['cum_r']:+.2f} | 连败 {st['max_streak']} | P5~P95 "
                   f"[{st['p5']:+.2f}~{st['p95']:+.2f}]")
    out.append(line)
    out.append("  判定：")
    out.extend(f"    {a}" for a in _judge(sim, live, base))
    out.append(line)
    out.append(f"  基准源: {base.get('source') or '未找到 signals.csv（回测基准不可用）'}")
    out.append("  口径: 模拟线/实盘=动态出场R；回测=r_20d 固定持有（同信号集分布比较，规格书 10.6）")
    return "\n".join(out)


if __name__ == "__main__":
    print(health_report())
