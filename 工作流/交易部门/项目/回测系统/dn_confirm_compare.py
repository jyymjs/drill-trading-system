#!/usr/bin/env python3
"""突破日量能确认 回测对照实验（2026-08-06 老板拍板）

背景：prebreak 预突破模式 5 条件评级不含 DN（动能）——突破发生在评级后，无法提前评动能。
老板质疑：触发瞬间"力度小"的突破（无量/磨上去）假突破概率高。
→ 实验：prebreak 触发（突破价成交）后，检查突破日（触发日）量比
   = 触发日成交量 ÷ 触发日前 20 日均量（口径对齐 DN 相对量比 ref.tail(20).mean()）
   > 阈值 X 才计入交易；X 试 {1.5, 2.0, 2.5}；对照组 = 纯价格触发（现状，X=0）。

实验设计（对照组 4 组）：
  组00 基线（现状）      dn_confirm=0.0（纯价格触发）
  组15 量比>1.5 才计入   dn_confirm=1.5
  组20 量比>2.0 才计入   dn_confirm=2.0
  组25 量比>2.5 才计入   dn_confirm=2.5

口径：
  - 样本：duckdb 主库确定性抽样 N 只（seed 固定，可复现；无网络依赖）
  - 区间：--start ~ --end（默认 2023-07-01 ~ 2026-07-31，3 年+，与 B1C3/C1 同口径）
  - 模式：prebreak；主口径评级 S（与 b1c3 同口径）；hold 5/10/20，主口径 20d
  - 环境闸门全关（env/volume/sentiment/prbook）——唯一变量 = 突破日量能确认
  - 指标：信号数/触发率/胜率/平均R/盈亏比/累计R/最大回撤
  - 专项：被剔除集表现——对照组（X=0）中触发日量比 ≤ X 的触发交易的 20d 盈亏，
    直接验证老板直觉"力度小的突破假突破概率高"（被剔除集若显著更差 → 直觉成立）
  - 量比口径：突破日成交量 / 触发日前 20 日均量（不含触发日；前 20 根不足 → 0 不达标）

用法:
  python 回测系统/dn_confirm_compare.py --smoke 30        # 30 只冒烟
  python 回测系统/dn_confirm_compare.py                   # 400 只全量（默认）
"""
import argparse
import random
import sys
from pathlib import Path

import duckdb

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from 回测系统.engine import BacktestEngine
from 回测系统.params import BacktestParams
from 回测系统.tracking import TrackedRecord

# 默认参数（2026-08-06 实验口径；与 B1C3/C1 对照同口径）
DEFAULT_SAMPLE = 400
DEFAULT_SEED = 42
DEFAULT_START = "20230701"
DEFAULT_END = "20260731"
DB_PATH = _ROOT / "数据基础" / "data" / "t017_p2.duckdb"
OUT_DIR = _ROOT / "产出" / "输出" / "dn_confirm_compare"

GROUPS = [  # (标签, dn_confirm 阈值)
    ("00基线(纯价格)", 0.0),
    ("15量比>1.5", 1.5),
    ("20量比>2.0", 2.0),
    ("25量比>2.5", 2.5),
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


def run_group(label: str, dn_confirm: float, codes: list[str], start: str, end: str,
              grades: list[str] | None = None) -> tuple[list[TrackedRecord], dict]:
    """跑一组对照并返回 (records, gate_counts)"""
    params = BacktestParams(
        start=start, end=end, mode="prebreak", interval=5,
        holds=[5, 10, 20], grades=grades or ["S"], codes=codes, max_workers=5,
        dn_confirm=dn_confirm,
        # 闸门全关：唯一变量 = 突破日量能确认（B1/C3/C4/C1 均不干扰）
        env_gate=False, volume_filter=False, sentiment_gate=False, prbook_gate=False,
    )
    engine = BacktestEngine(params)
    result = engine.run()
    n_sig = len(result.records)
    print(f"  [{label}] 完成 | 信号 {n_sig} 笔 | dn_confirm={dn_confirm} | 跳过 {result.skipped}")
    return result.records, result.gate_counts


def summarize(records: list[TrackedRecord], hold: int = 20) -> dict:
    """某组在指定 hold 的汇总（prebreak：仅触发且量能确认通过者参与）"""
    rs = [oc.r for rec in records for h, oc in rec.outcomes.items()
          if h == hold and oc.participate()]
    wins = sum(1 for r in rs if r > 0)
    gains = sum(r for r in rs if r > 0)
    losses = sum(-r for r in rs if r < 0)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "profit_factor": (round(gains / losses, 4) if losses > 0
                          else float("inf") if gains > 0 else 0.0),  # 盈亏比
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


def price_trigger_count(records: list[TrackedRecord], hold: int = 20) -> int:
    """纯价格触发数（对照组：触发即计入；各 X 组量能确认前的价格触发数应相同）"""
    return sum(1 for rec in records for h, oc in rec.outcomes.items()
               if h == hold and oc.triggered)


def rejected_subset(records: list[TrackedRecord], x: float, hold: int = 20) -> dict:
    """被剔除集：对照组（X=0，dn_confirm 关仍记录 vol_ratio）中触发日量比 ≤ X 的触发交易

    这些交易在 X 阈值组会被量能确认剔除——其 20d 表现直接回答老板直觉：
    若该集 平均R/胜率 显著差于全体触发交易 → "力度小的突破假突破概率高"成立。
    """
    rs = [oc.r for rec in records for h, oc in rec.outcomes.items()
          if h == hold and oc.triggered and oc.vol_ratio is not None and oc.vol_ratio <= x]
    wins = sum(1 for r in rs if r > 0)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "total_r": round(sum(rs), 4),
    }


# ── 主流程 ──


def main() -> int:
    parser = argparse.ArgumentParser(description="突破日量能确认对照实验（prebreak 动能补位）")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="样本股票数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样种子")
    parser.add_argument("--start", default=DEFAULT_START, help="起始 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="结束 YYYYMMDD")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟模式：N 只快速验证")
    parser.add_argument("--grades", nargs="+", default=["S"],
                        help="评级（默认 S，与 b1c3 同口径；prebreak 仅 S/A 两级）")
    args = parser.parse_args()

    codes = load_sample(args.smoke or args.sample, args.seed)
    print(f"[DN确认] 对照实验 | 样本 {len(codes)} 只(seed={args.seed}) | "
          f"区间 {args.start}~{args.end} | mode=prebreak 评级={'/'.join(args.grades)} | "
          f"量比口径=突破日成交量/前20日均量")

    # 对照组（X ∈ {0, 1.5, 2.0, 2.5}）
    results = {}
    for label, x in GROUPS:
        print(f"\n[组 {label}] dn_confirm={x}")
        recs, gcounts = run_group(label, x, codes, args.start, args.end, grades=args.grades)
        results[label] = {"records": recs, "gate_counts": gcounts}

    # 报告渲染
    report = render_report(results, args)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"report_{args.start}_{args.end}_n{args.smoke or args.sample}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\n报告 → {path}")
    print(report)
    return 0


def render_report(results: dict, args) -> str:
    """渲染对照实验报告（markdown）"""
    hold = 20  # 主口径
    base_n = len(results["00基线(纯价格)"]["records"])          # 信号数（各组相同）
    base_trig = price_trigger_count(results["00基线(纯价格)"]["records"], hold)
    lines = [
        "# 突破日量能确认 · 回测对照报告（prebreak 动能补位实验）",
        "",
        ("> 日期：2026-08-06 · 背景：老板质疑\"光突破但力度小可以吗\"——prebreak 5条件不含 DN（动能），"
         "突破发生在评级后无法提前评动能；力度小的突破（无量/磨上去）假突破概率高？"),
        (f"> 样本：{args.smoke or args.sample} 只（seed={args.seed}）｜区间 {args.start}~{args.end}｜"
         f"mode=prebreak｜评级={'/'.join(args.grades)}｜hold 主口径 {hold}d｜闸门全关（唯一变量=量能确认）"),
        "> 量比口径：突破日成交量 ÷ 触发日前 20 日均量（不含触发日；对齐 DN 相对量比 ref.tail(20).mean()）",
        "",
        "## 一、总体对照（hold=20d）",
        "",
        "| 组 | 信号数 | 触发率 | 胜率 | 平均R | 盈亏比 | 累计R | 最大回撤 |",
        "|---|-------:|-------:|------:|------:|-------:|-------:|--------:|",
    ]
    for label, x in GROUPS:
        recs = results[label]["records"]
        s = summarize(recs, hold)
        pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
        lines.append(
            f"| {label} | {s['n']} | {s['n']}/{base_n} ({s['n'] / base_n:.1%}) | "
            f"{s['win_rate']:.1%} | {s['avg_r']:.3f} | {pf} | {s['total_r']:.1f} | {s['max_dd']:.1f} |")
    lines.append("")
    lines.append(f"> 信号数 = 评级层信号总量（各组相同 {base_n}）；触发率 = 量能确认后计入交易数/信号数。"
                 f"纯价格触发数（对照组）= {base_trig}（触发率 {base_trig / base_n:.1%}），"
                 f"量能确认只在其内筛选。")
    lines.append("")

    # 全 hold 附表
    lines += ["## 二、全 hold 附表（胜率/平均R/累计R）", "",
              "| 组 | 5d胜率 | 5d均R | 5d累R | 10d胜率 | 10d均R | 10d累R | 20d胜率 | 20d均R | 20d累R |",
              "|---|-------:|------:|------:|--------:|-------:|-------:|--------:|-------:|-------:|"]
    for label, x in GROUPS:
        recs = results[label]["records"]
        cells = []
        for h in (5, 10, 20):
            s = summarize(recs, h)
            cells += [f"{s['win_rate']:.1%}", f"{s['avg_r']:.3f}", f"{s['total_r']:.1f}"]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")

    # 专项：被剔除集表现（验证老板直觉）
    lines += ["## 三、被剔除集专项（力度小的突破是否假突破更多？）", "",
              ("> 口径：对照组（纯价格触发）全部触发交易中，触发日量比 ≤ X 的那批（若开 X 阈值会被剔除），"
               "其 20d 表现与全体触发交易对比。若被剔除集 平均R 显著为负/显著差于全体 → 老板直觉成立。"),
              "",
              "| 阈值 X | 被剔除笔数 | 占比 | 被剔除集20d胜率 | 被剔除集20d均R | 被剔除集20d累计R |",
              "|-------:|----------:|-----:|---------------:|---------------:|-----------------:|"]
    base_recs = results["00基线(纯价格)"]["records"]
    all_trig = summarize(base_recs, hold)
    for label, x in [(g[0], g[1]) for g in GROUPS if g[1] > 0]:
        rs = rejected_subset(base_recs, x, hold)
        lines.append(f"| {x} | {rs['n']} | {rs['n'] / base_trig:.1%} | "
                     f"{rs['win_rate']:.1%} | {rs['avg_r']:.3f} | {rs['total_r']:.1f} |")
    lines.append("")
    lines.append(f"> 对照：全体触发交易（对照组）20d 胜率 {all_trig['win_rate']:.1%}、"
                 f"平均R {all_trig['avg_r']:.3f}、累计R {all_trig['total_r']:.1f}（n={all_trig['n']}）。")
    lines.append("")

    # 结论（数据驱动，自动判定 + 人工复核框）
    lines += ["## 四、结论", ""]
    lines.extend(_verdict(results, base_n, base_trig, hold))
    lines.append("")
    lines.append("> 判定说明：胜率与平均R随阈值提升单调上升（或至少保持）+ 被剔除集均R为负 → 量能确认有效；"
                 "若提升无/为负或被剔除集不差 → 无效，报告原因。最终签字权归老板。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 出处：2026-08-06 老板拍板实验（突破日量能确认）。实现：回测系统实验参数 "
                 "`--dn-confirm`（默认 0 关，不改变现有行为；量比口径对齐 DN 相对量比）；"
                 "接入扫描/挂单指引需老板书面确认阈值。")
    return "\n".join(lines)


def _verdict(results: dict, base_n: int, base_trig: int, hold: int = 20) -> list[str]:
    """数据驱动的结论草稿（最终由老板/助理复核）"""
    base = summarize(results["00基线(纯价格)"]["records"], hold)
    series = [(label, x, summarize(results[label]["records"], hold))
              for label, x in GROUPS if x > 0]
    out = [(f"- 基线（纯价格触发）：信号 {base_n} | 触发 {base_trig}（{base_trig / base_n:.1%}）| "
            f"20d 胜率 {base['win_rate']:.1%} | 平均R {base['avg_r']:.3f} | 累计R {base['total_r']:.1f}")]
    for label, x, s in series:
        delta_wr = s["win_rate"] - base["win_rate"]
        delta_r = s["avg_r"] - base["avg_r"]
        out.append(f"- {label}（X={x}）：计入 {s['n']} 笔（-{(base_trig - s['n']) / base_trig:.1%}）| "
                   f"胜率 {s['win_rate']:.1%}（{delta_wr:+.1%}）| 平均R {s['avg_r']:.3f}（{delta_r:+.3f}）| "
                   f"累计R {s['total_r']:.1f}")

    # 提升判定：随阈值单调提升 且 最高阈值组比基线显著更好
    if len(series) >= 2:
        mono_wr = all(series[i][2]["win_rate"] >= series[i - 1][2]["win_rate"] for i in range(1, len(series)))
        mono_r = all(series[i][2]["avg_r"] >= series[i - 1][2]["avg_r"] for i in range(1, len(series)))
        best = series[-1][2]
        if (best["win_rate"] > base["win_rate"] + 0.01 and best["avg_r"] > base["avg_r"] + 0.02):
            out.append("")
            out.append("**倾向结论：量能确认有效**——胜率/平均R 随阈值提升" +
                       ("（单调）" if mono_wr and mono_r else "（有波动但整体向上）") +
                       f"，最高阈值组 {series[-1][0]} 胜率 {best['win_rate']:.1%}、"
                       f"平均R {best['avg_r']:.3f}，均优于基线；代价=交易数减少 "
                       f"{100 * (1 - best['n'] / base_trig):.0f}%。"
                       "若被剔除集（第三节）均R为负 → 老板直觉成立，可建议接入。")
        else:
            out.append("")
            out.append("**倾向结论：量能确认未见有效提升**——胜率/平均R 未随阈值持续改善"
                       "（或改善幅度低于经验判据 +1%/+0.02R），代价是信号数大幅减少；"
                       "需结合第三节被剔除集与全 hold 附表复核，可能原因是突破日量比与突破后走势相关性弱。")
    else:
        out.append("")
        out.append("_样本组不足，无法自动判定，请人工复核。_")
    return out


if __name__ == "__main__":
    sys.exit(main())
