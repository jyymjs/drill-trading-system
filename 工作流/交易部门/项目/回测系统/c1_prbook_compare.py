#!/usr/bin/env python3
"""C1 财报日避让（第一层：预约披露日）回测对照实验（2026-08-05 老板拍板）

出处：《量化体系优化方案》（总理/待办区/待确认/2026-08-05）C1 项定案第 3 条：
财报日避让第一层 = 预约披露日不新开仓。2026-08-05 老板确认执行。

实验设计（对照组 2 组）：
  组00 基线（现状）      prbook_gate=off
  组01 避让（C1 第一层） prbook_gate=on
     —— 信号日 = 该股预约披露日 → 否决；持仓期跨披露日 → 警示（不强制平仓）

口径：
  - 样本：duckdb 主库确定性抽样 N 只（seed 固定，可复现；无网络依赖）
  - 区间：--start ~ --end（默认 2023-07-01 ~ 2026-07-31，3 年+，覆盖 7 个报告期披露）
  - 模式：normal；主口径评级 S（与 b1c3 对照同口径）；hold 5/10/20，主口径 20d
  - 指标：信号数/胜率/平均R/累计R/最大回撤（开/关对照）+ 披露日否决/警示计数
  - 专项：被避让信号的后续表现（00 基线中"信号日==披露日"的信号 20d 盈亏 +
    T+1 跳空）——避让是否躲开了"雷"、机会成本多大（interval=1 全评级放大样本）

用法:
  python 回测系统/c1_prbook_compare.py --smoke 30        # 30 只冒烟
  python 回测系统/c1_prbook_compare.py                   # 400 只全量（默认）
  python 回测系统/c1_prbook_compare.py --no-special      # 跳过专项（省时）
"""
import argparse
import random
import sys
from pathlib import Path

import duckdb
import pandas as pd

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from 分析决策.市场环境.prbook_gate import load_prbook_map, prbook_verdict

from 回测系统.engine import BacktestEngine
from 回测系统.params import BacktestParams
from 回测系统.tracking import TrackedRecord

# 默认参数（2026-08-05 C1 定案口径）
DEFAULT_SAMPLE = 400
DEFAULT_SEED = 42
DEFAULT_START = "20230701"
DEFAULT_END = "20260731"
DB_PATH = _ROOT / "数据基础" / "行情数据" / "t017_p2.duckdb"
OUT_DIR = _ROOT / "项目" / "扫描输出" / "backtest" / "c1_prbook_compare"

GROUPS = [  # (标签, prbook_gate)
    ("00基线", False),
    ("01避让", True),
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── 样本（duckdb 直读，确定性） ──


def load_sample(n: int, seed: int) -> list[str]:
    """从 duckdb 主库全市场确定性抽样（排除数据不足的自然会在引擎内跳过）"""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM daily").fetchall()]
    finally:
        con.close()
    rng = random.Random(seed)
    return rng.sample(sorted(syms), n)


# ── 单组回测 ──


def run_group(label: str, prbook_gate: bool, codes: list[str], start: str, end: str,
              interval: int = 5, grades: list[str] | None = None) -> tuple[list[TrackedRecord], dict]:
    """跑一组对照并返回 (records, gate_counts)"""
    params = BacktestParams(
        start=start, end=end, mode="normal", interval=interval,
        holds=[5, 10, 20], grades=grades or ["S"], codes=codes, max_workers=5,
        prbook_gate=prbook_gate,
        sentiment_gate=False,  # C4 情绪闸门（2026-08-05）：C1 实验唯一变量=财报日避让，其余闸门口径不受干扰
    )
    engine = BacktestEngine(params)
    result = engine.run()
    n_sig = len(result.records)
    print(f"  [{label}] 完成 | 信号 {n_sig} 笔 | 过滤 {result.gate_counts}"
          f" | 跳过 {result.skipped}")
    return result.records, result.gate_counts


def summarize(records: list[TrackedRecord], hold: int = 20) -> dict:
    """某组在指定 hold 的汇总（normal 全参与）"""
    rs = [oc.r for rec in records for h, oc in rec.outcomes.items() if h == hold]
    wins = sum(1 for r in rs if r > 0)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "total_r": round(sum(rs), 4),
        "max_dd": _max_dd(rs),
    }


def _max_dd(rs: list[float]) -> float:
    peak, dd = 0.0, 0.0
    cum = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return round(dd, 4)


# ── 专项：被避让信号的后续表现 ──


def run_special(codes: list[str], start: str, end: str) -> dict:
    """被避让信号专项：interval=1 全评级重跑 00 基线（无避让），放大披露日命中样本

    - 被避让信号 = 00 基线中"信号日 == 该股预约披露日"的信号（若开 C1 会被否决）
    - 统计其 20d 盈亏：若普遍亏损 → 避让躲开了雷；若普遍盈利 → 避让是机会成本
    - T+1 跳空：披露日次日涨跌幅（财报公布后的市场反应，业绩雷的直接证据），
      对比全体信号（非披露日）的 T+1 涨跌幅
    """
    params = BacktestParams(
        start=start, end=end, mode="normal", interval=1,
        holds=[20], grades=["S", "A", "B"], codes=codes, max_workers=5,
        prbook_gate=False,
        sentiment_gate=False,  # C4 情绪闸门（2026-08-05）：专项口径纯净，只测 C1 变量
    )
    engine = BacktestEngine(params)
    result = engine.run()
    records = result.records
    print(f"  [专项] 完成 | interval=1 全评级信号 {len(records)} 笔 | 跳过 {result.skipped}")

    prbook_map = load_prbook_map(codes, db_path=str(DB_PATH))
    avoided: list[TrackedRecord] = []
    for rec in records:
        rows = prbook_map.get(rec.signal.code)
        if rows and prbook_verdict(rows, rec.signal.date)[0] == "veto":
            avoided.append(rec)

    # 20d 盈亏统计
    base_sum = summarize(records, 20)
    avo_sum = summarize(avoided, 20)

    # T+1 跳空（披露日次日涨跌幅 %；无次日数据跳过）
    next_day_pcts = {"avoided": [], "all": []}
    for rec in records:
        df = engine.provider.load(rec.signal.code)
        if df.empty:
            continue
        sig = rec.signal.date
        idx = df.index[df["日期"] == sig]
        if idx.empty or int(idx[0]) + 1 >= len(df):
            continue
        pct = df["涨跌幅"].iloc[int(idx[0]) + 1]
        if pd.notna(pct):
            next_day_pcts["all"].append(float(pct))
            if rec in avoided:
                next_day_pcts["avoided"].append(float(pct))
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else float("nan")
    return {
        "n_avoided": len(avoided),
        "avoided_sum": avo_sum,
        "all_sum": base_sum,
        "t1_avoided": {"n": len(next_day_pcts["avoided"]), "avg_pct": avg(next_day_pcts["avoided"])},
        "t1_all": {"n": len(next_day_pcts["all"]), "avg_pct": avg(next_day_pcts["all"])},
        "prbook_codes": len(prbook_map),   # 样本中带披露数据的股票数
    }


# ── 主流程 ──


def main() -> int:
    parser = argparse.ArgumentParser(description="C1 财报日避让（预约披露日）对照实验")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="样本股票数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样种子")
    parser.add_argument("--start", default=DEFAULT_START, help="起始 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="结束 YYYYMMDD")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟模式：N 只快速验证")
    parser.add_argument("--no-special", action="store_true", help="跳过专项（省时）")
    args = parser.parse_args()

    codes = load_sample(args.smoke or args.sample, args.seed)
    print(f"[C1] 对照实验 | 样本 {len(codes)} 只(seed={args.seed}) | "
          f"区间 {args.start}~{args.end} | mode=normal 主口径评级=S")

    # 对照组（开/关）
    results = {}
    for label, pg in GROUPS:
        print(f"\n[组 {label}] prbook_gate={'on' if pg else 'off'}")
        recs, gcounts = run_group(label, pg, codes, args.start, args.end)
        results[label] = {"records": recs, "gate_counts": gcounts}

    # 专项（被避让信号后续表现）
    special = None
    if not args.no_special:
        print("\n[专项] 被避让信号后续表现（interval=1 全评级，00 基线重跑放大样本）…")
        special = run_special(codes, args.start, args.end)

    # 报告渲染
    report = render_report(results, special, args)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"report_{args.start}_{args.end}_n{args.smoke or args.sample}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\n报告 → {path}")
    print(report)
    return 0


def render_report(results: dict, special: dict | None, args) -> str:
    """渲染对照实验报告（markdown）"""
    hold = 20  # 主口径
    lines = [
        "# C1 财报日避让（第一层：预约披露日）· 回测对照报告",
        "",
        "> 日期：2026-08-05 · 出处：《量化体系优化方案》C1 定案第 3 条（老板拍板执行）",
        (f"> 样本：{args.smoke or args.sample} 只（seed={args.seed}）｜区间 {args.start}~{args.end}｜"
         f"mode=normal｜主口径评级 S｜hold 主口径 {hold}d｜"
         f"数据：巨潮预约披露 {special['prbook_codes'] if special else '?'} 只/报告期"
         f"（actual_date 未披露=避让对象）"),
        "",
        "## 一、总体对照（hold=20d）",
        "",
        "| 组 | 信号数 | 胜率 | 平均R | 累计R | 最大回撤 | 披露日否决 | 持仓警示 | 无数据放行 |",
        "|---|-------:|------:|------:|--------:|--------:|----------:|--------:|----------:|",
    ]
    for label, pg in GROUPS:
        recs = results[label]["records"]
        gc = results[label]["gate_counts"]
        s = summarize(recs, hold)
        lines.append(
            f"| {label} | {s['n']} | {s['win_rate']:.1%} | {s['avg_r']:.3f} | "
            f"{s['total_r']:.1f} | {s['max_dd']:.1f} | {gc['veto_prbook']} | "
            f"{gc['prbook_warn']} | {gc['prbook_missing']} |")
    lines.append("")

    # 全 hold 附表
    lines += ["## 二、全 hold 附表（胜率/平均R/累计R）", "",
              "| 组 | 5d胜率 | 5d均R | 5d累R | 10d胜率 | 10d均R | 10d累R | 20d胜率 | 20d均R | 20d累R |",
              "|---|-------:|------:|------:|--------:|-------:|-------:|--------:|-------:|-------:|"]
    for label, pg in GROUPS:
        recs = results[label]["records"]
        cells = []
        for h in (5, 10, 20):
            s = summarize(recs, h)
            cells += [f"{s['win_rate']:.1%}", f"{s['avg_r']:.3f}", f"{s['total_r']:.1f}"]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")

    # 专项：被避让信号后续表现
    lines += ["## 三、被避让信号专项（避让是否躲开了雷）", ""]
    if special is None:
        lines.append("_（已跳过专项 --no-special）_")
    else:
        av, al = special["avoided_sum"], special["all_sum"]
        lines += [
            "> 口径：00 基线以 interval=1 + 评级 S/A/B 重跑放大样本，",
            "其中\"信号日==该股预约披露日\"的信号计为被避让信号（开 C1 时会被否决）。",
            "",
            "| 信号集 | 信号数 | 20d胜率 | 20d均R | 20d累计R |",
            "|--------|-------:|------:|------:|--------:|",
            f"| 被避让信号（本会开仓） | {av['n']} | {av['win_rate']:.1%} | {av['avg_r']:.3f} | {av['total_r']:.1f} |",
            f"| 全部信号（对照） | {al['n']} | {al['win_rate']:.1%} | {al['avg_r']:.3f} | {al['total_r']:.1f} |",
            "",
            "### T+1 跳空（披露日次日涨跌幅，财报公布后市场反应）",
            "",
            "| 信号集 | 样本数 | T+1 平均涨跌幅 |",
            "|--------|------:|--------------:|",
            f"| 被避让信号（披露日次日） | {special['t1_avoided']['n']} | {special['t1_avoided']['avg_pct']:.3f}% |",
            f"| 全部信号（对照） | {special['t1_all']['n']} | {special['t1_all']['avg_pct']:.3f}% |",
            "",
            "### 结论判断",
            "",
        ]
        # 结论：被避让信号盈亏符号
        if av["n"] == 0:
            lines.append("_样本内无披露日命中信号，无法下结论（需扩大样本或延长区间）。_")
        else:
            avoid_lost = av["total_r"] > 0
            if av["total_r"] < 0 and av["win_rate"] < al["win_rate"]:
                verdict = ("**避让有效**：被避让信号 20d 普遍亏损/低胜率，"
                           "避让躲开了财报雷（机会成本为负 = 净收益）。")
            elif avoid_lost:
                verdict = ("**避让有成本**：被避让信号 20d 为正收益，避让牺牲了这部分利润"
                           "（但第一层目的是防尾部风险，见 T+1 跳空对比）。")
            else:
                verdict = ("**信号不足/中性**：样本偏小，需结合 T+1 跳空与后续层级综合判断。")
            lines.append(verdict)
            lines.append("")
            lines.append("> 注：被避让信号盈利 ≠ 避让错误——披露日次日跳空风险是尾部不对称的"
                         "（黑天鹅亏损一次可抹平多次小赚），第一层以防御为主，结论看长期回测。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 出处：《量化体系优化方案》（总理/待办区/待确认/2026-08-05）C1 定案第 3 条"
                 "· 2026-08-05 老板拍板执行。第一层=预约披露日不新开仓；持仓警示不强制平仓；"
                 "评级与执行分离（grade() 不动）。已披露报告期（actual_date 非空）不避让。")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
