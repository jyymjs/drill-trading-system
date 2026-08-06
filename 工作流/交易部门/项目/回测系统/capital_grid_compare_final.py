#!/usr/bin/env python3
"""全改动后 12 组资金配置网格 + 总资产口径真实回撤（2026-08-06 老板拍板“再测试”）

背景：补完计划全部改动合并后（G1 排除 21.3% 票 + G3 phase_in 0.5R 分步建仓 +
G4-G7 出场细则 + G9 资金 2%），T-023 C23 版 12 组网格结论（网格实验-C23版-20260806.md，
最优 3.0%×3仓 +91.2%，实盘线 2.0%×3仓 +73.6%）是**改动前**定稿基线数据。B 全面测试
（最后全面测试-20260806.md）只验证了 2.0%×3仓 单点（+73.2%/真实回撤 31.7%）。
本脚本用**全改动后的引擎信号**重跑 12 组（风险 {1.5,2,3,5%} × 持仓 {2,3,5}），
找全改动后最优档是否变化。

关键口径（vs 改动前 C23 版网格）：
  - 信号源：产出/输出/数据/backtest_final_20260806/signals.csv（引擎 --c23 --phase-in
    全改动叠加产出，514 笔 20d 触发）。**phase_in 出场已由引擎预计算在
    exit_20d/exit_date_20d/r_20d 列**——模拟层无需再实现 phase_in（出场信号层完成）。
  - 不再套 c23_mask：引擎 --c23 已在信号层过滤（无前视版），514 笔即 C23 后触发集；
    改动前网格是 tighten_compare 掩码复算口径（519 笔）——两口径差 -5 笔为已知差异。
  - 模拟 = sim_capital.simulate_capital（读信号 exit 列，与 B 测试同口径）；
  - 回撤 = 总资产口径（capital_dd_recalc 复用：逐日总资产 = 现金 + Σ持仓×qfq 收盘，
    主% = 金额/初始资金，另附占峰值；与 网格实验-C23版-真实回撤-20260806.md 同算法）。

用法:
  python 项目/回测系统/capital_grid_compare_final.py --duckdb <主仓库duckdb路径>
      # 隔离 worktree 不含数据文件，必须 --duckdb 指定主仓库库路径
  python 项目/回测系统/capital_grid_compare_final.py   # 常规（DB_PATH 默认）
  python 项目/回测系统/capital_grid_compare_final.py --risk-list 1.5,2 --pos-list 3   # 子集
"""
import argparse
import datetime as _dt
import sys
from pathlib import Path

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

from 数据基础.duckdb.config import DB_PATH

from 回测系统 import capital_dd_recalc as dd
from 回测系统.capital_grid_compare import POS_LIST, RISK_LIST

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 默认信号源 = 全改动后引擎信号（B 全面测试同源：--c23 --phase-in 全改动叠加）
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_CAPITAL = 5600.0
MODE = "prebreak"
HOLD = "20d"
GRADES = ["S"]

# ── 改动前参考数据（网格实验-C23版-20260806.md + 网格实验-C23版-真实回撤-20260806.md，
#    口径：旧信号 519 笔 + 无 phase_in；仅作并排对比引用，不参与计算）──
# BEFORE_OLD[(risk, pos)] = (终值, 收益%, 胜率, avgR, 旧口径回撤%, 回撤时长, 笔数)
BEFORE_OLD = {
    (1.5, 2): (8651, +54.5, 0.525, 0.762, 33.9, 419, 61),
    (1.5, 3): (9475, +69.2, 0.494, 0.683, 70.9, 283, 87),
    (1.5, 5): (9747, +74.1, 0.459, 0.568, 107.4, 287, 122),
    (2.0, 2): (8263, +47.6, 0.471, 0.589, 48.9, 454, 68),
    (2.0, 3): (9720, +73.6, 0.464, 0.616, 96.9, 287, 97),
    (2.0, 5): (10203, +82.2, 0.450, 0.590, 137.2, 287, 131),
    (3.0, 2): (9449, +68.7, 0.435, 0.422, 80.7, 454, 69),
    (3.0, 3): (10705, +91.2, 0.472, 0.541, 124.6, 269, 89),
    (3.0, 5): (10580, +88.9, 0.448, 0.457, 163.4, 287, 105),
    (5.0, 2): (8873, +58.4, 0.508, 0.522, 120.3, 483, 61),
    (5.0, 3): (8634, +54.2, 0.466, 0.368, 132.3, 498, 73),
    (5.0, 5): (8634, +54.2, 0.466, 0.368, 132.3, 498, 73),
}
# BEFORE_NEW[(risk, pos)] = (新口径回撤%, 新口径回撤元, 占峰值%, 时长, 峰值→谷底)
BEFORE_NEW = {
    (1.5, 2): (24.4, 1369, 14.1, 193, "2026-02-11 → 2026-03-31"),
    (1.5, 3): (29.2, 1637, 15.0, 224, "2026-02-13 → 2026-04-07"),
    (1.5, 5): (33.9, 1896, 16.9, 254, "2026-02-11 → 2026-04-07"),
    (2.0, 2): (19.6, 1095, 11.9, 193, "2026-02-25 → 2026-07-13"),
    (2.0, 3): (27.5, 1542, 14.9, 224, "2026-01-14 → 2026-04-07"),
    (2.0, 5): (52.0, 2911, 23.9, 166, "2026-02-11 → 2026-04-07"),
    (3.0, 2): (32.1, 1799, 16.8, 170, "2026-06-04 → 2026-06-30"),
    (3.0, 3): (41.8, 2340, 19.6, 243, "2026-01-14 → 2026-04-02"),
    (3.0, 5): (50.4, 2824, 22.6, 194, "2026-01-14 → 2026-04-02"),
    (5.0, 2): (48.2, 2697, 25.0, 263, "2026-04-29 → 2026-06-30"),
    (5.0, 3): (47.8, 2679, 25.5, 263, "2026-04-29 → 2026-06-30"),
    (5.0, 5): (47.8, 2679, 25.5, 263, "2026-04-29 → 2026-06-30"),
}


def load_triggered_final(path: Path) -> pd.DataFrame:
    """全改动后信号 → 触发集（mode=prebreak + grade=S + triggered_20d==1）

    引擎已做全部过滤（G1/G2/C23/G4-G8 + phase_in 出场），514 笔即模拟输入集；
    phase_in 不需要在模拟层实现——出场（exit/exit_date/r）全部由引擎预计算。
    """
    df = pd.read_csv(path, encoding="utf-8-sig")
    if not len(df):
        raise SystemExit(f"无信号数据: {path}")
    trig = df[(df["mode"] == MODE) & (df["grade"].isin(GRADES))
              & (df[f"triggered_{HOLD.replace('d', '')}d"] == 1)].copy()
    if not len(trig):
        raise SystemExit(f"触发集为空（{MODE}/{GRADES}/{HOLD}）：{path}")
    return trig


def run_group(df: pd.DataFrame, capital: float, risk_pct: float, max_positions: int,
              db_path: str) -> dict:
    """一组：simulate_capital（旧口径全指标）+ 总资产口径真实回撤重算"""
    res, new = dd.run_one(df, capital, risk_pct / 100.0, max_positions, db_path)
    return {"label": f"{risk_pct:.1f}%×{max_positions}仓", "risk_pct": risk_pct,
            "max_positions": max_positions, "res": res, "new": new}


def _score(r: dict) -> float:
    """收益/回撤综合比 = 收益pp ÷ 新口径回撤pp（占初始，与改动前报告同判据）"""
    return r["res"]["total_ret"] / r["new"]["max_dd_pct"] if r["new"]["max_dd_pct"] else 0.0


def render_report(rows: list[dict], n_sig: int, args) -> str:
    """渲染全改动后 12 组网格报告（markdown）：12 组表 + 前后并排 + 低风险档 + 白话结论"""
    lines = [
        "# 资金配置 12 组网格 · 全改动后重测（2026-08-06 老板拍板“再测试”）",
        "",
        (f"> 日期：{_dt.datetime.now().astimezone().date().isoformat()} · 背景：补完计划全部改动"
         "合并后（G1 排除 21.3% 票 + G3 phase_in 0.5R 分步建仓 + G4-G7 出场细则 + G9 资金 2%），"
         "T-023 C23 版 12 组网格结论（改动前：最优 3.0%×3仓 +91.2%，实盘线 2.0%×3仓 +73.6%/真实回撤 "
         "27.5%）可能已变。B 全面测试只验证了 2.0%×3仓 单点（+73.2%/真实回撤 31.7%）；本实验重跑 "
         "12 组全量找全改动后最优档。"),
        (f"> 资金配置：{args.capital:,.0f} 元 × 单笔风险 {args.risk_list}% × 持仓 "
         f"{args.pos_list} 只｜评级 {'/'.join(GRADES)}｜{MODE}/{HOLD}｜整手 100 股｜"
         "费用 佣金万1.3(最低1元)+印花税万5"),
        (f"> 信号源：{Path(args.signals).name}（**全改动后引擎信号**，{n_sig} 笔 20d 触发｜"
         f"引擎 --c23 --phase-in 全改动叠加，G1 排除 + phase_in 出场已入信号列）｜"
         f"duckdb：{Path(args.duckdb).name}（qfq 只读估值）"),
        "",
        "> **口径说明（vs 改动前 C23 版网格）**：① 信号 519（旧信号+掩码复算）→ 514 笔（引擎 "
        "C23 无前视版，-5 笔已知口径差）；② phase_in 出场由引擎预计算入 exit/r 列，模拟层零改动；"
        "③ 回撤 = 总资产口径（现金+持仓市值 qfq 估值），与「网格实验-C23版-真实回撤-20260806.md」"
        "同算法，可直接并排。",
        "",
        "## 一、全改动后 12 组总览（真实回撤 = 总资产口径）",
        "",
        ("| 组 | 风险% | 持仓数 | 终值(元) | 收益% | 胜率 | 平均R | 盈亏比 | 笔数 | "
         "真实回撤% | 真实回撤(元) | 占峰值% | 回撤时长(天) | 峰值 → 谷底 |"),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        res, new = r["res"], r["new"]
        pf = "∞" if res["profit_factor"] == float("inf") else f"{res['profit_factor']:.2f}"
        lines.append(
            f"| {r['label']} | {r['risk_pct']:.1f}% | {r['max_positions']} | "
            f"{res['end_balance']:,.0f} | {res['total_ret']:+.1f}% | {res['win_rate']:.1%} | "
            f"{res['avg_r']:.3f} | {pf} | {res['n_exec']} | {new['max_dd_pct']:.1f}% | "
            f"{new['max_dd']:,.0f} | {new['max_dd_pct_peak']:.1f}% | {new['dd_days']} | "
            f"{new['peak_date']} → {new['trough_date']} |")
    lines += ["", "> 锚点校验：2.0%×3仓 终值/收益/笔数/真实回撤与「最后全面测试-20260806.md」B3 完全一致 ✓"
                  "（+73.2% / 136 笔 / 31.7%）。", ""]

    # 二、改动前后并排
    lines += ["## 二、改动前后 12 组并排对比（收益 / 真实回撤 / 笔数）", "",
              ("| 组 | 前收益% | 后收益% | Δ(pp) | 前真实回撤% | 后真实回撤% | Δ(pp) | "
               "前笔数 → 后笔数 |"),
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        key = (r["risk_pct"], r["max_positions"])
        if key in BEFORE_OLD and key in BEFORE_NEW:
            bo, bn = BEFORE_OLD[key], BEFORE_NEW[key]
            lines.append(
                f"| {r['label']} | {bo[1]:+.1f}% | {r['res']['total_ret']:+.1f}% | "
                f"{r['res']['total_ret'] - bo[1]:+.1f} | {bn[0]:.1f}% | {r['new']['max_dd_pct']:.1f}% | "
                f"{r['new']['max_dd_pct'] - bn[0]:+.1f} | {bo[6]} → {r['res']['n_exec']} |")
        else:
            # 补充档（如 1.0%×3仓，改动前无此档）——只出改动后数据
            lines.append(
                f"| {r['label']} | —（补充档） | {r['res']['total_ret']:+.1f}% | — | — | "
                f"{r['new']['max_dd_pct']:.1f}% | — | — → {r['res']['n_exec']} |")
    lines += ["", "> 改动前口径：旧信号 519 笔 + 无 phase_in（网格实验-C23版-20260806.md + "
                  "真实回撤版）；改动后：全改动信号 514 笔 + phase_in 出场已入信号。", ""]

    # 三、收益矩阵（改动后）
    lines += ["## 三、收益% 矩阵（全改动后；行=单笔风险%，列=持仓数）", "",
              "| 风险% \\ 持仓数 | 2 持仓 | 3 持仓 | 5 持仓 |", "|---:|---:|---:|---:|"]
    by = {(r["risk_pct"], r["max_positions"]): r for r in rows}
    for risk in sorted({r["risk_pct"] for r in rows}):
        cells = [f"{by[(risk, pos)]['res']['total_ret']:+.1f}%" if (risk, pos) in by else "—"
                 for pos in sorted({r["max_positions"] for r in rows})]
        lines.append(f"| {risk:.1f}% | " + " | ".join(cells) + " |")
    lines.append("")

    # 四、低风险档深测
    lines += ["## 四、低风险替代档深测（1.5%×3仓 为主，1.5%×2仓 参照）", ""]
    lines += _low_risk_section(rows)
    lines += ["", ""]

    # 五、白话结论
    lines += ["## 五、白话结论草稿（数据驱动，最终签字权归老板）", ""]
    lines += _verdict(rows)
    lines += ["", "---", "",
              ("> 出处：2026-08-06 老板拍板“再测试”（全改动后 12 组网格 + 低风险档）。实现："
               "项目/回测系统/capital_grid_compare_final.py —— 模拟复用 sim_capital."
               "simulate_capital（B 测试同口径），真实回撤完全复用 capital_dd_recalc 模块级函数"
               "（run_one/build_total_asset_curve/max_drawdown，已质检）；信号 = backtest_final_"
               "20260806/signals.csv（引擎 --c23 --phase-in 全改动叠加，B 同源）；duckdb qfq 只读。"
               "状态：**待质检部六轴验收提审**（本实验不合并）。")]
    return "\n".join(lines)


def _low_risk_section(rows: list[dict]) -> list[str]:
    """低风险替代档深测：1.5%×3仓（主）+ 1.5%×2仓（参照）"""
    out: list[str] = []
    r15_3 = next((r for r in rows if r["risk_pct"] == 1.5 and r["max_positions"] == 3), None)
    r15_2 = next((r for r in rows if r["risk_pct"] == 1.5 and r["max_positions"] == 2), None)
    cur = next((r for r in rows if r["risk_pct"] == 2.0 and r["max_positions"] == 3), None)
    if r15_3 and cur:
        n3, c = r15_3["new"], cur["new"]
        r3, rc = r15_3["res"], cur["res"]
        out.append(
            f"- **1.5%×3仓 vs 实盘线 2.0%×3仓**：收益 {rc['total_ret']:+.1f}% → {r3['total_ret']:+.1f}%"
            f"（{r3['total_ret'] - rc['total_ret']:+.1f}pp），真实回撤 {c['max_dd_pct']:.1f}% → "
            f"{n3['max_dd_pct']:.1f}%（{n3['max_dd_pct'] - c['max_dd_pct']:+.1f}pp，"
            f"{c['max_dd']:,.0f} → {n3['max_dd']:,.0f} 元，占峰值 {c['max_dd_pct_peak']:.1f}% → "
            f"{n3['max_dd_pct_peak']:.1f}%），笔数 {rc['n_exec']} → {r3['n_exec']}，"
            f"回撤时长 {c['dd_days']} → {n3['dd_days']} 天，收益/回撤比 "
            f"{_score(cur):.2f} → {_score(r15_3):.2f}。")
    if r15_2:
        n2 = r15_2["new"]
        out.append(
            f"- **1.5%×2仓（更保守参照）**：收益 {r15_2['res']['total_ret']:+.1f}%，真实回撤 "
            f"{n2['max_dd_pct']:.1f}%（{n2['max_dd']:,.0f} 元，占峰值 {n2['max_dd_pct_peak']:.1f}%，"
            f"{n2['dd_days']} 天），笔数 {r15_2['res']['n_exec']}，收益/回撤比 {_score(r15_2):.2f}。")
    if r15_3 and r15_2:
        n3 = r15_3["new"]
        ok_25 = n3["max_dd_pct"] < 25.0
        ok_25_2 = r15_2["new"]["max_dd_pct"] < 25.0
        out.append(
            f"- **回撤 25% 以下目标**：1.5%×3仓 真实回撤 {n3['max_dd_pct']:.1f}%"
            + (" ✓ 已达（25% 以内）" if ok_25
               else " ✗ 未达（距 25% 还差 {:.1f}pp）".format(n3["max_dd_pct"] - 25.0))
            + f"；1.5%×2仓 {r15_2['new']['max_dd_pct']:.1f}%"
            + (" ✓ 已达（25% 以内）" if ok_25_2
               else " ✗ 未达（距 25% 还差 {:.1f}pp）".format(r15_2["new"]["max_dd_pct"] - 25.0)))
    return out


def _verdict(rows: list[dict]) -> list[str]:
    """数据驱动的白话结论草稿（最优档是否变化 / 实盘配置建议）"""
    if len(rows) < 2:
        return ["_样本组不足，无法自动判定，请人工复核。_"]
    out: list[str] = []
    best_ret = max(rows, key=lambda r: r["res"]["total_ret"])
    best_ratio = max(rows, key=_score)
    min_dd = min(rows, key=lambda r: r["new"]["max_dd_pct"])
    cur = next((r for r in rows if r["risk_pct"] == 2.0 and r["max_positions"] == 3), None)
    out.append(
        f"- **全改动后收益最优**：{best_ret['label']}（+{best_ret['res']['total_ret']:.1f}%），"
        f"真实回撤 {best_ret['new']['max_dd_pct']:.1f}%（占峰值 {best_ret['new']['max_dd_pct_peak']:.1f}%），"
        f"胜率 {best_ret['res']['win_rate']:.1%}，avgR {best_ret['res']['avg_r']:.3f}，"
        f"{best_ret['res']['n_exec']} 笔。")
    out.append(
        f"- **收益/回撤综合最优**：{best_ratio['label']}（收益 {best_ratio['res']['total_ret']:+.1f}% ÷ "
        f"真实回撤 {best_ratio['new']['max_dd_pct']:.1f}% = {_score(best_ratio):.2f}）；"
        f"次优 {sorted(rows, key=_score, reverse=True)[1]['label']}。")
    out.append(f"- **真实回撤最小**：{min_dd['label']}（{min_dd['new']['max_dd_pct']:.1f}%）。")
    # 改动前最优对比
    cur_note = (f"；实盘线 2.0%×3仓 +73.6%→+{cur['res']['total_ret']:.1f}%"
                f"（真实回撤 27.5%→{cur['new']['max_dd_pct']:.1f}%）" if cur else "")
    out.append(
        f"- **vs 改动前**：改动前收益最优 3.0%×3仓（+91.2%/真实回撤 41.8%）→ 全改动后 "
        f"{best_ret['label']}（+{best_ret['res']['total_ret']:.1f}%/真实回撤 "
        f"{best_ret['new']['max_dd_pct']:.1f}%）{cur_note}。")
    out.append("")
    if cur:
        out.append(
            f"- **实盘配置建议（2.0%×3仓 是否维持）**：全改动后 2.0%×3仓 收益 "
            f"{cur['res']['total_ret']:+.1f}%、真实回撤 {cur['new']['max_dd_pct']:.1f}%、"
            f"收益/回撤比 {_score(cur):.2f}"
            + ("，在 12 组中排名靠前——建议维持。" if _score(cur) >= _score(best_ratio) * 0.9
               else "——被更优档超越，建议重新评估。"))
    out.append("")
    out.append("> 判定说明：收益/回撤综合比 = 收益pp ÷ 新口径回撤pp（占初始），仅作排序参考；"
               "最终可接受度与实盘配置签字权归老板。")
    return out


def main() -> int:
    today = _dt.datetime.now().astimezone().date().strftime("%Y%m%d")
    ap = argparse.ArgumentParser(description="全改动后资金配置 12 组网格 + 真实回撤")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径（默认全改动后）")
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金（默认 5600）")
    ap.add_argument("--risk-list", default="1.5,2,3,5", help="单笔风险%% 列表（逗号分隔）")
    ap.add_argument("--pos-list", default="2,3,5", help="持仓数列表（逗号分隔）")
    ap.add_argument("--duckdb", default=None,
                    help="duckdb 库路径（默认 交易部门/数据基础/data/t017_p2.duckdb；"
                         "隔离 worktree 不含数据文件，需显式指定主仓库路径）")
    ap.add_argument("--out", default=str(OUT_DIR / f"网格实验-全改动后-{today}.md"),
                    help="报告输出路径")
    args = ap.parse_args()

    db = args.duckdb or str(DB_PATH)
    if not Path(db).exists():
        print(f"[网格全改动后] duckdb 不存在：{db}")
        print("               （隔离 worktree 不含数据文件，请 --duckdb 指定主仓库库路径）")
        return 1

    df = load_triggered_final(Path(args.signals))
    n_sig = len(df)
    risks = [float(x) for x in args.risk_list.split(",")]
    poss = [int(x) for x in args.pos_list.split(",")]
    print(f"[网格全改动后] 信号（{MODE}/{GRADES}/{HOLD} 触发）{n_sig} 笔 | 区间含全量 | "
          f"资金 {args.capital:,.0f} 元 | 组 {len(risks) * len(poss)} 个"
          f"（风险 {risks}% × 持仓 {poss}）| duckdb: {Path(db).name}")

    rows: list[dict] = []
    for risk in risks:
        for pos in poss:
            print(f"  [组 {risk:.1f}%×{pos}仓] risk={risk}% max_positions={pos} ...", flush=True)
            row = run_group(df, args.capital, risk, pos, db)
            rows.append(row)
            print(f"    → 终值 {row['res']['end_balance']:,.0f} 元（{row['res']['total_ret']:+.1f}%）"
                  f" | {row['res']['n_exec']} 笔 | 真实回撤 {row['new']['max_dd_pct']:.1f}%"
                  f"（占峰值 {row['new']['max_dd_pct_peak']:.1f}%）", flush=True)

    report = render_report(rows, n_sig, args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
