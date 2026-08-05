#!/usr/bin/env python3
"""C4 情绪闸门（涨跌家数）回测对照实验（2026-08-05 老板拍板执行优化方案第 3 波）

出处：《量化体系优化方案》C4 项——普跌日盲区实证：
  2026-05-29 全市场 71.4% 股票跌（家数口径），但上证仅 -0.73%——指数闸门（B1，
  阈值 -2%）管不了"家数普跌"，当天 21 笔信号全亏 -20.3R。
  C4 增加涨跌家数维度：信号日全市场下跌家数占比 > 阈值（建议 70%）→ 环境否决。

数据源（2026-08-05 实测确认）：
  - pytdx get_index_bars 每根指数 K 线自带 up/down_count（含历史），
    上证=沪市家数、深证成指=深市家数，相加=全市场（2026-05-29 实测 71.6%，
    与 duckdb 全库 71.4% 对账一致）→ index_data.load_market_breadth

实验设计（对照矩阵，突出情绪闸门增量价值）：
  组00 基线（无闸门）        env=off volume=off sentiment=off
  组10 B1C3（指数+量能）     env=on  volume=on  sentiment=off   ← 2026-08-05 已上线形态
  组01 仅情绪闸门            env=off volume=off sentiment=on
  组11 全开（B1C3+C4）       env=on  volume=on  sentiment=on    ← C4 上线后形态

口径：
  - 样本：duckdb 库确定性抽样 N 只（seed 固定，可复现；无网络依赖）
  - 区间：--start ~ --end（默认 2023-07-01 ~ 2026-07-31，3 年）
  - 模式：normal（B1 主战场=已突破信号）；评级 S（C1 定案：只做 S 级）
  - 指标：信号数/胜率/平均R/累计R/最大回撤（20d 主口径，全部 hold 附表）
  - 普跌日专项：2026-05-29（真普跌日 71.4%，任务重点：情绪闸门应能拦住）；
    2024-12-27（实测 31.4% 非普跌日——C6 体检疑似日期误记，如实呈现）；
    2024-12-30（实测 73.9%，12 月底真普跌日，补充验证）
  - 情绪闸门专杀日明细：00 组信号日中"下跌占比超阈值"的日子及当日信号数

用法:
  python 回测系统/c4_sentiment_compare.py --smoke 30        # 30 只冒烟
  python 回测系统/c4_sentiment_compare.py                   # 400 只全量（默认）
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

from 回测系统.engine import BacktestEngine                       # noqa: E402
from 回测系统.params import BacktestParams                        # noqa: E402
from 回测系统.tracking import TrackedRecord                       # noqa: E402

# 默认参数（2026-08-05 定案口径）
DEFAULT_SAMPLE = 400
DEFAULT_SEED = 42
DEFAULT_START = "20230701"
DEFAULT_END = "20260731"
DEFAULT_SENT_THRESHOLD = 70.0   # C4 建议值（2026-05-29 实证 71.4%）
PRU_DUO_DAYS = ["2026-05-29", "2024-12-27", "2024-12-30"]  # 专项日（含实测口径）
DB_PATH = _ROOT / "数据基础" / "data" / "t017_p2.duckdb"
OUT_DIR = _ROOT / "项目" / "output" / "backtest" / "c4_sentiment_compare"

GROUPS = [  # (标签, env_gate, volume_filter, sentiment_gate)
    ("00基线", False, False, False),
    ("10B1C3", True, True, False),
    ("01仅情绪", False, False, True),
    ("11全开", True, True, True),
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── 样本与普跌性验证（duckdb 直读，确定性） ──


def load_sample(n: int, seed: int) -> list[str]:
    """从 duckdb 全市场确定性抽样（排除数据不足的自然会在引擎内跳过）"""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM daily").fetchall()]
    finally:
        con.close()
    rng = random.Random(seed)
    return rng.sample(sorted(syms), n)


def market_down_pct(date_str: str) -> dict:
    """全市场普跌性验证（duckdb 口径）：某交易日下跌家数占比"""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            "WITH tgt AS (SELECT symbol, close FROM daily WHERE date = ?), "
            "prev AS (SELECT symbol, close AS prev_close FROM daily "
            "         WHERE date = (SELECT MAX(date) FROM daily WHERE date < ?)) "
            "SELECT t.symbol, t.close, p.prev_close "
            "FROM tgt t JOIN prev p ON t.symbol = p.symbol",
            [date_str, date_str]).df()
    finally:
        con.close()
    if rows.empty or (rows["prev_close"] == 0).all():
        return {"n": 0, "down_pct": 0.0}
    down = (rows["close"] < rows["prev_close"]).sum()
    return {"n": len(rows), "down_pct": round(down / len(rows) * 100, 1)}


def pytdx_down_pct(date_str: str) -> dict:
    """全市场普跌性验证（情绪闸门数据源口径，pytdx 涨跌家数，供对照）

    与 duckdb 口径交叉验证（2026-05-29：71.6% vs 71.4%，一致）。
    """
    from 分析决策.市场环境.index_data import load_market_breadth  # noqa: E402
    df = load_market_breadth()
    hit = df[df["日期"] == pd.Timestamp(date_str)]
    if hit.empty:
        return {"n": 0, "down_pct": 0.0}
    row = hit.iloc[0]
    total = float(row["上涨家数"] + row["下跌家数"])
    return {"n": int(total), "down_pct": round(float(row["下跌占比"]), 1)}


# ── 单组回测 ──


def run_group(label: str, env_gate: bool, volume_filter: bool, sentiment_gate: bool,
              codes: list[str], start: str, end: str) -> tuple[list[TrackedRecord], dict]:
    """跑一组对照并返回 (records, gate_counts)"""
    params = BacktestParams(
        start=start, end=end, mode="normal", interval=5,
        holds=[5, 10, 20], grades=["S"], codes=codes, max_workers=5,
        env_gate=env_gate, volume_filter=volume_filter,
        sentiment_gate=sentiment_gate, sent_threshold=DEFAULT_SENT_THRESHOLD,
    )
    engine = BacktestEngine(params)
    result = engine.run()
    n_sig = len(result.records)
    print(f"  [{label}] 完成 | 信号 {n_sig} 笔 | 过滤 {result.gate_counts}"
          f" | 跳过 {result.skipped}")
    return result.records, result.gate_counts


def summarize(records: list[TrackedRecord], hold: int = 20) -> dict:
    """某组在指定 hold 的汇总（S 级 normal 全参与）"""
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


def day_subset(records: list[TrackedRecord], day: str, hold: int = 20) -> dict:
    """普跌日专项：某日信号（20d）统计"""
    recs = [rec for rec in records
            if rec.signal.date.strftime("%Y-%m-%d") == day]
    return summarize(recs, hold)


def run_day_window(day: str, window_days: int, codes: list[str]) -> dict:
    """普跌日窗口子实验：以普跌日为中心的窄窗口，interval=1 保证当日必在网格上

    专项必须能观测"普跌日当天的信号"：主实验网格 interval=5，普跌日大概率不在网格上
    → 专项独立重跑：区间 = 普跌日 ± window_days 自然日；interval=1；评级全开（S/A/B）；
    对照 00 基线 vs 11 全开（B1C3+C4）。
    """
    from datetime import timedelta
    d = pd.Timestamp(day)
    start = (d - timedelta(days=window_days)).strftime("%Y%m%d")
    end = (d + timedelta(days=window_days)).strftime("%Y%m%d")
    out = {}
    for label, eg, vf, sg in [("00基线", False, False, False),
                              ("10B1C3", True, True, False),
                              ("11全开", True, True, True)]:
        params = BacktestParams(
            start=start, end=end, mode="normal", interval=1,
            holds=[20], grades=["S", "A", "B"], codes=codes, max_workers=5,
            env_gate=eg, volume_filter=vf,
            sentiment_gate=sg, sent_threshold=DEFAULT_SENT_THRESHOLD,
        )
        result = BacktestEngine(params).run()
        day_recs = [r for r in result.records
                    if r.signal.date.strftime("%Y-%m-%d") == day]
        out[label] = {"n": len(day_recs), "sum": summarize(day_recs, 20),
                      "gate": result.gate_counts}
    return out


# ── 主流程 ──


def main() -> int:
    parser = argparse.ArgumentParser(description="C4 情绪闸门对照实验")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="样本股票数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样种子")
    parser.add_argument("--start", default=DEFAULT_START, help="起始 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="结束 YYYYMMDD")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟模式：N 只快速验证")
    args = parser.parse_args()

    codes = load_sample(args.smoke or args.sample, args.seed)
    print(f"[C4] 情绪闸门对照实验 | 样本 {len(codes)} 只(seed={args.seed}) | "
          f"区间 {args.start}~{args.end} | mode=normal 评级=S | 情绪阈值 {DEFAULT_SENT_THRESHOLD}%")

    # 普跌性验证（双口径：duckdb 全库 vs pytdx 涨跌家数）
    print("\n[普跌日验证] 全市场下跌占比（duckdb 全库 vs pytdx 家数口径）:")
    day_meta = {}
    for d in PRU_DUO_DAYS:
        m1 = market_down_pct(d)
        m2 = pytdx_down_pct(d)
        day_meta[d] = {"duckdb": m1, "pytdx": m2}
        print(f"  {d}: duckdb {m1['down_pct']}% (n={m1['n']}) | "
              f"pytdx {m2['down_pct']}% (n={m2['n']})")

    # 对照组
    results = {}
    for label, eg, vf, sg in GROUPS:
        print(f"\n[组 {label}] env_gate={'on' if eg else 'off'} "
              f"volume_filter={'on' if vf else 'off'} "
              f"sentiment_gate={'on' if sg else 'off'}")
        recs, gcounts = run_group(label, eg, vf, sg, codes, args.start, args.end)
        results[label] = {"records": recs, "gate_counts": gcounts}

    # 普跌日窗口子实验（interval=1 全评级，保证普跌日当天信号可观测）
    print("\n[普跌日窗口子实验] interval=1, 评级 S/A/B, 窗口 ±30 自然日")
    day_exp = {}
    for day in PRU_DUO_DAYS:
        print(f"  {day}: 重跑中…")
        day_exp[day] = run_day_window(day, 30, codes)

    # 报告渲染
    report = render_report(results, day_exp, day_meta, args)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"report_{args.start}_{args.end}_n{args.smoke or args.sample}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\n报告 → {path}")
    print(report)
    return 0


def render_report(results: dict, day_exp: dict, day_meta: dict, args) -> str:
    """渲染对照实验报告（markdown）"""
    hold = 20  # 主口径
    lines = [
        "# C4 情绪闸门（涨跌家数）· 回测对照报告",
        "",
        "> 日期：2026-08-05 · 出处：《量化体系优化方案》C4（普跌日盲区实证）· 老板拍板",
        "> 盲区实证：2026-05-29 全市场 71.4% 股票跌但上证仅 -0.73%，21 笔信号全亏 -20.3R",
        "> 情绪闸门：信号日全市场下跌家数占比 > 阈值（建议 70%）→ 环境否决，与指数闸门并列任一触发即否决",
        f"> 样本：{args.smoke or args.sample} 只（seed={args.seed}）｜区间 {args.start}~{args.end}｜"
        f"mode=normal｜评级=S（C1 只做 S）｜hold 主口径 {hold}d｜情绪阈值 {DEFAULT_SENT_THRESHOLD}%",
        "",
        "## 一、普跌日口径验证（duckdb 全库 vs pytdx 涨跌家数）",
        "",
        "| 日期 | duckdb 下跌占比 | pytdx 下跌占比 | 是否普跌日（>70%） |",
        "|------|--------------:|--------------:|:-----------------:|",
    ]
    for d in PRU_DUO_DAYS:
        m1, m2 = day_meta[d]["duckdb"], day_meta[d]["pytdx"]
        is_pd = "是" if max(m1["down_pct"], m2["down_pct"]) > DEFAULT_SENT_THRESHOLD else "否"
        lines.append(f"| {d} | {m1['down_pct']}% (n={m1['n']}) | {m2['down_pct']}% (n={m2['n']}) | {is_pd} |")
    lines += ["",
              "> 注：2024-12-27 实测非普跌日（约 31%），C6 体检疑似日期误记——12 月底真普跌日",
              "> 为 2024-12-30（约 73%），故专项同时纳入两日，以数据为准。",
              "",
              "## 二、总体对照（hold=20d）",
              "",
              "| 组 | 信号数 | 胜率 | 平均R | 累计R | 最大回撤 | 指数否决 | 情绪否决 | 量能否决 | 降级 | 缺口放行 |",
              "|---|-------:|------:|------:|--------:|--------:|--------:|--------:|-----:|--------:|",
    ]
    for label, eg, vf, sg in GROUPS:
        recs = results[label]["records"]
        gc = results[label]["gate_counts"]
        s = summarize(recs, hold)
        lines.append(
            f"| {label} | {s['n']} | {s['win_rate']:.1%} | {s['avg_r']:.3f} | "
            f"{s['total_r']:.1f} | {s['max_dd']:.1f} | {gc['veto_env']} | "
            f"{gc['veto_sentiment']} | {gc['veto_volume']} | {gc['downgraded']} | "
            f"{gc['missing']} |")
    lines.append("")

    # 全 hold 附表
    lines += ["## 三、全 hold 附表（胜率/平均R/累计R）", "",
              "| 组 | 5d胜率 | 5d均R | 5d累R | 10d胜率 | 10d均R | 10d累R | 20d胜率 | 20d均R | 20d累R |",
              "|---|-------:|------:|------:|--------:|-------:|-------:|--------:|-------:|-------:|"]
    for label, eg, vf, sg in GROUPS:
        recs = results[label]["records"]
        cells = []
        for h in (5, 10, 20):
            s = summarize(recs, h)
            cells += [f"{s['win_rate']:.1%}", f"{s['avg_r']:.3f}", f"{s['total_r']:.1f}"]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")

    # 普跌日窗口子实验（interval=1 全评级，普跌日当天信号可观测）
    lines += ["## 四、普跌日专项（重点：情绪闸门能否拦住普跌日）", "",
              "| 普跌日 | 组 | 当日信号数 | 20d胜率 | 20d均R | 20d累计R | 窗口内情绪否决 |",
              "|---|----|----------:|------:|------:|--------:|----------:|"]
    for day in PRU_DUO_DAYS:
        m1 = day_meta[day]["duckdb"]
        for label in ("00基线", "10B1C3", "11全开"):
            e = day_exp[day][label]
            s = e["sum"]
            gc = e["gate"]
            lines.append(f"| {day}（跌{m1['down_pct']}%）| {label} | {e['n']} | "
                         f"{s['win_rate']:.1%} | {s['avg_r']:.3f} | {s['total_r']:.1f} | "
                         f"{gc['veto_sentiment']} |")
    lines.append("")

    # 情绪闸门专杀日明细：00 组信号日中"下跌占比超阈值"的信号（11 全开下必然被情绪否决）
    lines += ["## 五、情绪闸门专杀日明细（00 基线信号中全市场下跌占比超阈值的信号）", "",
              "| 日期 | 下跌占比% | 00基线信号数 |", "|------|---------:|----------:|"]
    from 分析决策.市场环境.index_data import load_market_breadth  # noqa: E402
    breadth = load_market_breadth()
    recs00 = results["00基线"]["records"]
    day_counts: dict[str, int] = {}
    for rec in recs00:
        d = rec.signal.date.strftime("%Y-%m-%d")
        day_counts[d] = day_counts.get(d, 0) + 1
    gate_days = []
    for d, cnt in day_counts.items():
        hit = breadth[breadth["日期"] == pd.Timestamp(d)]
        ratio = float(hit["下跌占比"].iloc[0]) if len(hit) else float("nan")
        if len(hit) and ratio > DEFAULT_SENT_THRESHOLD:
            gate_days.append((d, ratio, cnt))
    gate_days.sort(key=lambda x: -x[1])
    if gate_days:
        for d, ratio, cnt in gate_days[:10]:
            lines.append(f"| {d} | {ratio:.1f}% | {cnt} |")
    else:
        lines.append("| （无：样本内信号日无一超情绪阈值） | - | - |")
    lines.append("")
    lines.append("> 注：上表列出 00 基线组中全市场下跌家数占比超阈值（70%）的信号——"
                 "这些信号在 11 全开组全部被情绪闸门否决（见上表总对照的情绪否决计数）。")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
