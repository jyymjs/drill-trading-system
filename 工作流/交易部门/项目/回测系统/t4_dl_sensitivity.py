#!/usr/bin/env python3
"""T-4.2 DL 阈值敏感性分析（只读 signals.csv，不改策略参数）

目标：回答"S 级阈值该调到哪"——
  1. 按 DL 评级（S/A/B/C）分组：验证"DL 必须 S"在回测中的表现
  2. 综合评级 grade × DL 交叉：定位是综合评级逻辑错，还是 DL 本身错
  3. 按持有窗（5d/10d/20d）分列：阈值结论是否随持有期变化

口径（对齐回测方法论）：
  - 只统计 triggered=1 的信号（未触发不参与胜率/平均R）
  - 胜率 = r>0 占比；止损率 = stopped=1 占比
  - R = 该持有窗的实际盈亏 / 信号日 risk

用法:
    python 项目/回测系统/t4_dl_sensitivity.py [--signals 路径] [--report 路径]
"""
import argparse
import os

import pandas as pd

HOLDS = ("5d", "10d", "20d")
DL_ORDER = ("S", "A", "B", "C")
GRADE_ORDER = ("S", "A", "B", "C")


def load_signals(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    for col in ("triggered_5d", "stopped_5d"):
        if col not in df.columns:
            raise SystemExit(f"缺少列 {col}，signals.csv 结构不符预期")
    return df


def stat_block(df: pd.DataFrame, r_col: str, stop_col: str) -> dict:
    """统计单组表现：样本量/触发数/平均R/胜率/中位R/止损率"""
    n_all = len(df)
    tr = df[df["triggered_" + r_col.replace("r_", "")] == 1]
    n_tr = len(tr)
    if n_tr == 0:
        return {"n_all": n_all, "n": 0, "avg_r": float("nan"),
                "win": float("nan"), "med": float("nan"), "stop": float("nan")}
    r = tr[r_col]
    stopped = tr[stop_col]
    return {
        "n_all": n_all, "n": n_tr,
        "avg_r": round(r.mean(), 3),
        "win": round((r > 0).mean() * 100, 1),
        "med": round(r.median(), 3),
        "stop": round(stopped.mean() * 100, 1),
    }


def fmt(b: dict) -> str:
    if b["n"] == 0:
        return f"{b['n_all']:>6} |  0触发 |"
    return (f"{b['n_all']:>6} | {b['n']:>6} | "
            f"{b['avg_r']:>7} | {b['win']:>5}% | {b['med']:>7} | {b['stop']:>4}%")


def by_dl(df: pd.DataFrame, mode: str, hold: str, r_col: str, stop_col: str) -> list[str]:
    """按 DL 评级分组，返回表格行"""
    lines = []
    for g in DL_ORDER:
        sub = df[(df["mode"] == mode) & (df["DL"] == g)]
        b = stat_block(sub, r_col, stop_col)
        lines.append(f"  DL={g} | {fmt(b)}")
    return lines


def cross_grade_dl(df: pd.DataFrame, hold: str, r_col: str, stop_col: str) -> list[str]:
    """normal 模式 grade × DL 交叉（平均R）"""
    lines = ["  综合grade\\DL   " + "".join(f"{g:>9}" for g in DL_ORDER)]
    for gr in GRADE_ORDER:
        cells = []
        for dl in DL_ORDER:
            sub = df[(df["mode"] == "normal") & (df["grade"] == gr) & (df["DL"] == dl)]
            b = stat_block(sub, r_col, stop_col)
            if b["n"] == 0:
                cells.append(f"{'—':>9}")
            else:
                cells.append(f"{b['avg_r']:>8}({b['n']})")
        lines.append(f"  {gr:>12} | " + "".join(cells))
    return lines


def main():
    ap = argparse.ArgumentParser()
    base = os.path.dirname(os.path.abspath(__file__))
    default_sig = os.path.join(base, "..", "output", "backtest",
                               "20230701_20260804", "signals.csv")
    ap.add_argument("--signals", default=default_sig, help="signals.csv 路径")
    ap.add_argument("--report", default=None, help="报告输出路径（默认 t4/t4_dl_sensitivity.txt）")
    args = ap.parse_args()

    df = load_signals(args.signals)
    out = []
    out.append(f"T-4.2 DL 阈值敏感性分析（{len(df)} 信号）")
    out.append(f"数据：{args.signals}")
    out.append("口径：仅统计 triggered=1；胜率=r>0；R=盈亏/信号日risk；DL 阈值 S=90/A=70/B=60")
    out.append("")

    for mode in ("normal", "prebreak"):
        sub_m = df[df["mode"] == mode]
        out.append("=" * 78)
        out.append(f"{mode} 模式：按 DL 评级分组（样本全部 | 触发 | 平均R | 胜率 | 中位R | 止损率）")
        out.append("=" * 78)
        for hold, r_col, stop_col in (("5d", "r_5d", "stopped_5d"),
                                      ("10d", "r_10d", "stopped_10d"),
                                      ("20d", "r_20d", "stopped_20d")):
            out.append(f"--- 持有 {hold} ---")
            out.extend(by_dl(sub_m, mode, hold, r_col, stop_col))
            out.append("")

    out.append("=" * 78)
    out.append("normal 模式：综合评级 grade × DL 评级交叉（平均R(样本数)，20d 持有）")
    out.append("=" * 78)
    out.extend(cross_grade_dl(df, "20d", "r_20d", "stopped_20d"))
    out.append("")

    out.append("=" * 78)
    out.append("核心问题速答")
    out.append("=" * 78)

    # ① DL=S vs 非 S（normal, 20d）
    for mode in ("normal", "prebreak"):
        sub = df[(df["mode"] == mode) & (df["triggered_20d"] == 1)]
        s = stat_block(sub[sub["DL"] == "S"], "r_20d", "stopped_20d")
        ns = stat_block(sub[sub["DL"] != "S"], "r_20d", "stopped_20d")
        out.append(f"{mode}: DL=S 平均R={s['avg_r']}(n={s['n']}) vs DL≠S 平均R={ns['avg_r']}(n={ns['n']})")

    # ② DL=C 但综合评级 B（T4 审计违规案例类）表现
    sub = df[(df["mode"] == "normal") & (df["triggered_20d"] == 1) & (df["DL"] == "C")]
    b = stat_block(sub, "r_20d", "stopped_20d")
    out.append(f"normal: DL=C 的信号共 {b['n_all']} 个（综合评级多为 C），触发 {b['n']}，平均R={b['avg_r']}")

    # ③ 综合 S 中 DL 分布（normal）
    gs = df[(df["mode"] == "normal") & (df["grade"] == "S")]
    dist = gs["DL"].value_counts().to_dict()
    out.append(f"normal: 综合S级信号 {len(gs)} 个，DL 分布 {dist}")

    # ④ 触发率比较（prebreak 预突破有效性）
    for mode in ("normal", "prebreak"):
        for g in DL_ORDER:
            sub = df[(df["mode"] == mode) & (df["DL"] == g)]
            if len(sub):
                tr = (sub["triggered_5d"] == 1).mean() * 100
                out.append(f"{mode} DL={g}: 触发率 {tr:.1f}%（n={len(sub)}）")

    text = "\n".join(out) + "\n"
    print(text)

    report = args.report or os.path.join(base, "..", "..", "产出", "输出", "t4",
                                         "t4_dl_sensitivity.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(f"[T4.2] 报告已写入 {report}")


if __name__ == "__main__":
    main()
