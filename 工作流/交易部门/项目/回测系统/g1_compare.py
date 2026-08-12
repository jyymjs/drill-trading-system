"""R-080 G1 样本外对照——信号层（2026-08-13）

定参段（2020-2022）vs 验证段（2023-07~2026-07）同引擎同参数信号层对照：
信号数/触发率/avgR/胜率/累计R/最大回撤 + 分年明细。

判据（G1 方案 v2）：验证段相对定参段的衰减率 = 1 - valid/calib
（avgR/胜率 双指标；触发率偏差 2pp 内为稳健）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd

CALIB = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                     "数据", "backtest_calib_2020-2022", "signals.csv")
VALID = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                     "数据", "backtest_final_20260806", "signals.csv")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                   "实验", "G1-样本外对照-信号层-20260813.md")


def seg_stats(df: pd.DataFrame, hold: int = 20) -> dict:
    """某段信号层统计（hold 触发口径）"""
    trig = df[(df[f"triggered_{hold}d"] == 1)].copy()
    rs = trig[f"r_{hold}d"].astype(float)
    wins = int((rs > 0).sum())
    cum, peak, dd = 0.0, 0.0, 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {
        "n_sig": len(df),
        "n_trig": len(trig),
        "trig_rate": len(trig) / len(df) if len(df) else 0.0,
        "win_rate": wins / len(rs) if len(rs) else 0.0,
        "avg_r": float(rs.mean()) if len(rs) else 0.0,
        "total_r": float(rs.sum()) if len(rs) else 0.0,
        "max_dd_r": round(dd, 3),
    }


def yearly(df: pd.DataFrame, hold: int = 20) -> pd.DataFrame:
    rows = []
    for y, g in df.groupby(df["date"].astype(str).str[:4]):
        s = seg_stats(g, hold)
        rows.append({"年": y, **{k: s[k] for k in ("n_sig", "n_trig", "trig_rate",
                                                   "win_rate", "avg_r")}})
    return pd.DataFrame(rows).round(3)


def main() -> int:
    cal = pd.read_csv(CALIB, encoding="utf-8-sig", dtype={"code": str})
    val = pd.read_csv(VALID, encoding="utf-8-sig", dtype={"code": str})
    print(f"定参段信号: {len(cal)} 笔（{cal['date'].min()}~{cal['date'].max()}）", flush=True)
    print(f"验证段信号: {len(val)} 笔（{val['date'].min()}~{val['date'].max()}）", flush=True)

    sc, sv = seg_stats(cal), seg_stats(val)
    decay_avg = 1 - sv["avg_r"] / sc["avg_r"] if sc["avg_r"] else 0
    decay_win = 1 - sv["win_rate"] / sc["win_rate"] if sc["win_rate"] else 0
    print(f"定参段: 触发 {sc['n_trig']} ({sc['trig_rate']:.1%}) | avgR {sc['avg_r']:+.3f} "
          f"| 胜率 {sc['win_rate']:.1%} | 累计R {sc['total_r']:+.1f}", flush=True)
    print(f"验证段: 触发 {sv['n_trig']} ({sv['trig_rate']:.1%}) | avgR {sv['avg_r']:+.3f} "
          f"| 胜率 {sv['win_rate']:.1%} | 累计R {sv['total_r']:+.1f}", flush=True)
    print(f"衰减: avgR {decay_avg:+.1%} | 胜率 {decay_win:+.1%}", flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    yc, yv = yearly(cal), yearly(val)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"""# G1 样本外对照·信号层（2026-08-13）

> 同引擎同参数（prebreak/interval5/S级/C23/phase_in/dn_confirm1.5/成本开），仅时段不同。
> 差异标注：定参段无披露数据 → --no-prbook-gate（验证段 C1 避让生效，为保守方向差异）。

## 总览（hold=20d 触发口径）

| 指标 | 定参段 2020-2022 | 验证段 2023-07~2026-07 | 变化 |
|---|---|---|---|
| 信号数 | {sc['n_sig']} | {sv['n_sig']} | {sv['n_sig']-sc['n_sig']:+d} |
| 触发数 | {sc['n_trig']} | {sv['n_trig']} | |
| 触发率 | {sc['trig_rate']:.1%} | {sv['trig_rate']:.1%} | {sv['trig_rate']-sc['trig_rate']:+.1%} |
| 胜率 | {sc['win_rate']:.1%} | {sv['win_rate']:.1%} | {sv['win_rate']-sc['win_rate']:+.1%} |
| avgR | {sc['avg_r']:+.3f} | {sv['avg_r']:+.3f} | {sv['avg_r']-sc['avg_r']:+.3f} |
| 累计R | {sc['total_r']:+.1f} | {sv['total_r']:+.1f} | |
| 最大回撤(累计R) | {sc['max_dd_r']} | {sv['max_dd_r']} | |

**衰减率**（验证段相对定参段）：avgR {decay_avg:+.1%} ｜ 胜率 {decay_win:+.1%}

## 分年明细（定参段）

{yc.to_markdown(index=False)}

## 分年明细（验证段）

{yv.to_markdown(index=False)}

## 结论判定

- avgR 衰减 {decay_avg:+.1%}（|衰减| ≤ 20% 视为稳健；>40% 提示过拟合/时变）
- 胜率衰减 {decay_win:+.1%}（|衰减| ≤ 10pp 视为稳健）
- 触发率偏差 {sv['trig_rate']-sc['trig_rate']:+.1%}（±2pp 内稳健）
""")
    print(f"报告: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
