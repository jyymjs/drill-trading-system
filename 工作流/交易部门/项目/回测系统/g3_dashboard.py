"""R-080 G3 回测-实盘对账看板（2026-08-13）

回测分布基准固化（V4 3y 触发集：触发率/avgR/胜率/R 分布 μ/σ）→ 模拟盘/实盘
每 20 笔滚窗 vs 基准偏离度检验（z 检验 + 分位数）。

输出：产出/输出/实验/G3-对账看板-<日期>.md
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SIGNALS = os.path.join(ROOT, "产出", "输出", "数据", "backtest_final_20260806", "signals.csv")
JOURNAL = os.path.join(ROOT, "分析决策", "交易日志", "trade_journal.csv")
SIM_JOURNAL = os.path.join(ROOT, "分析决策", "交易日志", "sim_journal.csv")
WINDOW = 20          # 滚窗笔数
ALPHA = 0.05         # 偏离度显著性（z 检验双侧）


def backtest_baseline() -> dict:
    """V4 3y 触发集分布基准（触发率/avgR/胜率/R 分布 μ/σ）"""
    sig = pd.read_csv(SIGNALS, encoding="utf-8-sig", dtype={"code": str})
    trig = sig[sig["triggered_20d"] == 1]
    rs = trig["r_20d"].astype(float)
    return {
        "n_sig": len(sig), "n_trig": len(trig),
        "trig_rate": len(trig) / len(sig),
        "avg_r": float(rs.mean()), "win_rate": float((rs > 0).mean()),
        "r_std": float(rs.std(ddof=1)),
        "r_pct": {q: float(np.percentile(rs, q)) for q in (5, 25, 50, 75, 95)},
    }


def rolling_checks(rs: np.ndarray, mu: float, sigma: float) -> list[dict]:
    """每 20 笔滚窗偏离度：z 检验（窗口均值 vs 基准均值）"""
    out = []
    for i in range(0, len(rs), WINDOW):
        w = rs[i:i + WINDOW]
        if len(w) < 10:          # 不足 10 笔不成窗（数据不足提示）
            continue
        x = float(w.mean())
        se = sigma / np.sqrt(len(w))
        z = (x - mu) / se if se > 0 else 0.0
        p = 2 * (1 - _norm_cdf(abs(z)))
        out.append({
            "start": i + 1, "end": i + len(w), "n": len(w),
            "avg_r": round(x, 3), "win_rate": round(float((w > 0).mean()), 3),
            "z": round(z, 2), "p": round(p, 4),
            "flag": "⚠️ 显著偏离" if p < ALPHA else "正常",
        })
    return out


def _norm_cdf(x: float) -> float:
    """标准正态 CDF（无 scipy 依赖）"""
    return 0.5 * (1 + np.math.erf(x / np.sqrt(2)))


def _load_journal(path: str, prefix: str) -> list[float]:
    """读日志 closed 交易的 r_multiple 序列（按 exit_date 排序）"""
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, encoding="utf-8-sig")
    closed = df[df["status"] == "closed"]
    closed = closed[closed["r_multiple"].notna() & (closed["r_multiple"] != 0)]
    closed = closed.sort_values("exit_date")
    return [float(v) for v in closed["r_multiple"]]


def main() -> int:
    base = backtest_baseline()
    print(f"回测基准: 触发率 {base['trig_rate']:.1%} | avgR {base['avg_r']:+.3f} "
          f"| 胜率 {base['win_rate']:.1%} | R σ {base['r_std']:.3f}", flush=True)

    live = _load_journal(JOURNAL, "LIVE")
    sim = _load_journal(SIM_JOURNAL, "SIM")
    print(f"实盘 closed R 值: {len(live)} 笔 | 模拟盘 closed R 值: {len(sim)} 笔",
          flush=True)

    lc = rolling_checks(np.array(live), base["avg_r"], base["r_std"])
    sc = rolling_checks(np.array(sim), base["avg_r"], base["r_std"])

    out = os.path.join(ROOT, "产出", "输出", "实验", f"G3-对账看板-{time.strftime('%Y-%m-%d')}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"""# R-080 G3 回测-实盘对账看板（{time.strftime('%Y-%m-%d')}）

## 1. 回测分布基准（V4 3y 验证段触发集）

| 指标 | 值 |
|---|---|
| 信号数 | {base['n_sig']} |
| 触发数（20d） | {base['n_trig']} |
| 触发率 | {base['trig_rate']:.1%} |
| avgR | {base['avg_r']:+.3f} |
| 胜率 | {base['win_rate']:.1%} |
| R 分布 σ | {base['r_std']:.3f} |
| R 分位数 | p5={base['r_pct'][5]:+.2f} p25={base['r_pct'][25]:+.2f} p50={base['r_pct'][50]:+.2f} p75={base['r_pct'][75]:+.2f} p95={base['r_pct'][95]:+.2f} |

## 2. 实盘对账（每 {WINDOW} 笔滚窗）

{_fmt_checks(lc, live, "实盘")}

## 3. 模拟盘对账（每 {WINDOW} 笔滚窗）

{_fmt_checks(sc, sim, "模拟盘")}

## 4. 说明

- 偏离度检验：窗口均值 vs 基准均值 z 检验（σ=回测触发 R 标准差，双侧 α={ALPHA}）；
  窗口不足 10 笔不判定（数据积累后自动生效）
- 当前实盘 {len(live)} 笔 closed / 模拟盘 {len(sim)} 笔 closed——滚动窗随交易积累
- 基准为 V4 3y 验证段（2023-07~2026-07）信号层分布；资金层基准（回撤 -25.1%）
  另见 G1 完整报告
""")
    print(f"看板: {out}", flush=True)
    return 0


def _fmt_checks(checks: list[dict], rs: list[float], label: str) -> str:
    if not checks:
        return f"- {label} closed R 值仅 {len(rs)} 笔（<10），滚窗检验未生效——随交易积累"
    rows = ["| 窗口 | n | avgR | 胜率 | z | p | 判定 |", "|---|---|---|---|---|---|---|"]
    for c in checks:
        rows.append(f"| {c['start']}-{c['end']} | {c['n']} | {c['avg_r']:+.3f} "
                    f"| {c['win_rate']:.0%} | {c['z']:+.2f} | {c['p']:.4f} | {c['flag']} |")
    return "\n".join(rows)


if __name__ == "__main__":
    raise SystemExit(main())
