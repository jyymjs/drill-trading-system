#!/usr/bin/env python3
"""B1 环境闸门 + C3 量能过滤 回测对照实验（2026-08-05 优化方案第 3 波）

出处：《量化体系优化方案》（总理/工作区/待确认/2026-08-05）B1/C3/C4 项——
"阈值用建议值 + 回测验证（方案 C3/C4 定案口径），不拍脑袋"。

实验设计（对照矩阵 2×2）：
  组00 基线（现状）            env_gate=off  volume_filter=off
  组10 仅环境闸门              env_gate=on   volume_filter=off
  组01 仅量能过滤              env_gate=off  volume_filter=on
  组11 全开                    env_gate=on   volume_filter=on

口径：
  - 样本：duckdb 库确定性抽样 N 只（seed 固定，可复现；无网络依赖）
  - 区间：--start ~ --end（默认 2023-07-01 ~ 2026-07-31，3 年）
  - 模式：normal（B1 主战场=已突破信号）；评级 S（C1 定案：只做 S 级）
  - 指标：信号数/胜率/平均R/累计R/最大回撤（20d 主口径，全部 hold 附表）
  - 普跌日专项：C6 体检发现"普跌日全市场 90% 信号同亏"（如 2024-12-27 / 2026-05-29）
    → 验证这两日普跌性（duckdb 全市场下跌占比）+ 各组这两日信号数与盈亏对比
  - 环境闸门计数：各组被否决/降级/放行的信号数（gate_counts）

用法:
  python 回测系统/b1c3_compare.py --smoke 30        # 30 只冒烟
  python 回测系统/b1c3_compare.py                   # 400 只全量（默认）
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
PRU_DUO_DAYS = ["2024-12-27", "2026-05-29"]   # C6 体检发现的普跌日
DB_PATH = _ROOT / "数据基础" / "data" / "t017_p2.duckdb"
OUT_DIR = _ROOT / "项目" / "output" / "backtest" / "b1c3_compare"

GROUPS = [  # (标签, env_gate, volume_filter)
    ("00基线", False, False),
    ("10仅环境", True, False),
    ("01仅量能", False, True),
    ("11全开", True, True),
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
    """全市场普跌性验证：某交易日下跌家数占比

    SQL 口径：该日全部个股收盘 vs 前一交易日（库内该日前最近交易日）收盘。
    """
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


# ── 单组回测 ──


def run_group(label: str, env_gate: bool, volume_filter: bool, codes: list[str],
              start: str, end: str) -> tuple[list[TrackedRecord], dict]:
    """跑一组对照并返回 (records, gate_counts)"""
    params = BacktestParams(
        start=start, end=end, mode="normal", interval=5,
        holds=[5, 10, 20], grades=["S"], codes=codes, max_workers=5,
        env_gate=env_gate, volume_filter=volume_filter,
        sentiment_gate=False,  # C4 实验见 c4_sentiment_compare.py，本实验对照组不受情绪闸门污染
        prbook_gate=False,  # C1 财报日避让（2026-08-05 老板拍板）：B1C3 实验口径纯净，不受 C1 干扰
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

    C6 体检口径：普跌日全市场 90% 信号同亏——专项必须能观测"普跌日当天的信号"。
    主实验网格 interval=5，普跌日大概率不在网格上 → 专项独立重跑：
      区间 = 普跌日 ± window_days 自然日；interval=1；评级全开（S/A/B）保证信号量；
      对照 00 基线 vs 11 全开。
    """
    from datetime import timedelta
    d = pd.Timestamp(day)
    start = (d - timedelta(days=window_days)).strftime("%Y%m%d")
    end = (d + timedelta(days=window_days)).strftime("%Y%m%d")
    out = {}
    for label, eg, vf in [("00基线", False, False), ("11全开", True, True)]:
        params = BacktestParams(
            start=start, end=end, mode="normal", interval=1,
            holds=[20], grades=["S", "A", "B"], codes=codes, max_workers=5,
            env_gate=eg, volume_filter=vf,
            sentiment_gate=False,  # C4 实验见 c4_sentiment_compare.py，本实验对照组不受情绪闸门污染
            prbook_gate=False,  # C1 财报日避让（2026-08-05 老板拍板）：B1C3 实验口径纯净
        )
        result = BacktestEngine(params).run()
        day_recs = [r for r in result.records
                    if r.signal.date.strftime("%Y-%m-%d") == day]
        out[label] = {"n": len(day_recs), "sum": summarize(day_recs, 20),
                      "gate": result.gate_counts}
    return out


# ── 主流程 ──


def main() -> int:
    parser = argparse.ArgumentParser(description="B1/C3 对照实验")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="样本股票数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样种子")
    parser.add_argument("--start", default=DEFAULT_START, help="起始 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="结束 YYYYMMDD")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟模式：N 只快速验证")
    args = parser.parse_args()

    codes = load_sample(args.smoke or args.sample, args.seed)
    print(f"[B1C3] 对照实验 | 样本 {len(codes)} 只(seed={args.seed}) | "
          f"区间 {args.start}~{args.end} | mode=normal 评级=S")

    # 普跌性验证（全市场）
    print("\n[普跌日验证] 全市场下跌占比（duckdb 直算）:")
    for d in PRU_DUO_DAYS:
        m = market_down_pct(d)
        print(f"  {d}: 覆盖 {m['n']} 只, 下跌占比 {m['down_pct']}%")

    # 对照组
    results = {}
    for label, eg, vf in GROUPS:
        print(f"\n[组 {label}] env_gate={'on' if eg else 'off'} volume_filter={'on' if vf else 'off'}")
        recs, gcounts = run_group(label, eg, vf, codes, args.start, args.end)
        results[label] = {"records": recs, "gate_counts": gcounts}

    # 普跌日窗口子实验（interval=1 全评级，保证普跌日当天信号可观测）
    print("\n[普跌日窗口子实验] interval=1, 评级 S/A/B, 窗口 ±30 自然日")
    day_exp = {}
    for day in PRU_DUO_DAYS:
        print(f"  {day}: 重跑中…")
        day_exp[day] = run_day_window(day, 30, codes)

    # 报告渲染
    report = render_report(results, day_exp, args)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"report_{args.start}_{args.end}_n{args.smoke or args.sample}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\n报告 → {path}")
    print(report)
    return 0


def render_report(results: dict, day_exp: dict, args) -> str:
    """渲染对照实验报告（markdown）"""
    hold = 20  # 主口径
    lines = [
        "# B1 环境闸门 + C3 量能过滤 · 回测对照报告",
        "",
        f"> 日期：2026-08-05 · 出处：《量化体系优化方案》B1/C3/C4（建议值+回测验证，不拍脑袋）",
        f"> 样本：{args.smoke or args.sample} 只（seed={args.seed}）｜区间 {args.start}~{args.end}｜"
        f"mode=normal｜评级=S（C1 只做 S）｜hold 主口径 {hold}d",
        "",
        "## 一、总体对照（hold=20d）",
        "",
        "| 组 | 信号数 | 胜率 | 平均R | 累计R | 最大回撤 | 环境否决 | 量能否决 | 降级 | 缺口放行 |",
        "|---|-------:|------:|------:|--------:|--------:|--------:|-----:|--------:|",
    ]
    for label, eg, vf in GROUPS:
        recs = results[label]["records"]
        gc = results[label]["gate_counts"]
        s = summarize(recs, hold)
        lines.append(
            f"| {label} | {s['n']} | {s['win_rate']:.1%} | {s['avg_r']:.3f} | "
            f"{s['total_r']:.1f} | {s['max_dd']:.1f} | {gc['veto_env']} | "
            f"{gc['veto_volume']} | {gc['downgraded']} | {gc['missing']} |")
    lines.append("")

    # 全 hold 附表
    lines += ["## 二、全 hold 附表（胜率/平均R/累计R）", "",
              "| 组 | 5d胜率 | 5d均R | 5d累R | 10d胜率 | 10d均R | 10d累R | 20d胜率 | 20d均R | 20d累R |",
              "|---|-------:|------:|------:|--------:|-------:|-------:|--------:|-------:|-------:|"]
    for label, eg, vf in GROUPS:
        recs = results[label]["records"]
        cells = []
        for h in (5, 10, 20):
            s = summarize(recs, h)
            cells += [f"{s['win_rate']:.1%}", f"{s['avg_r']:.3f}", f"{s['total_r']:.1f}"]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")

    # 普跌日专项（主实验网格内，信号日恰为普跌日的信号）
    lines += ["## 三、普跌日专项（C6 体检：普跌日全市场 90% 信号同亏）", "",
              "| 普跌日 | 组 | 信号数 | 20d胜率 | 20d均R | 20d累计R |", "|---|----|-------:|------:|------:|--------:|"]
    for day in PRU_DUO_DAYS:
        m = market_down_pct(day)
        for label, eg, vf in GROUPS:
            recs = results[label]["records"]
            s = day_subset(recs, day, 20)
            lines.append(f"| {day}（跌{m['down_pct']}%）| {label} | {s['n']} | "
                         f"{s['win_rate']:.1%} | {s['avg_r']:.3f} | {s['total_r']:.1f} |")
    lines.append("")

    # 普跌日窗口子实验（interval=1 全评级，普跌日当天信号可观测）
    lines += ["## 四、普跌日窗口子实验（interval=1，评级 S/A/B，普跌日当天信号）", "",
              "| 普跌日 | 组 | 当日信号数 | 20d胜率 | 20d均R | 20d累计R | 窗口内过滤 |",
              "|---|----|----------:|------:|------:|--------:|----------:|"]
    for day in PRU_DUO_DAYS:
        m = market_down_pct(day)
        for label in ("00基线", "11全开"):
            e = day_exp[day][label]
            s = e["sum"]
            gc = e["gate"]
            filtered = gc["veto_env"] + gc["veto_volume"]
            lines.append(f"| {day}（跌{m['down_pct']}%）| {label} | {e['n']} | "
                         f"{s['win_rate']:.1%} | {s['avg_r']:.3f} | {s['total_r']:.1f} | {filtered} |")
    lines.append("")

    # 环境闸门专杀日明细：00 组信号日中"上证当日跌破阈值"的信号（11 全开下必然被否决）
    lines += ["## 五、环境闸门专杀日明细（00 基线信号中上证当日跌超阈值的信号）", "",
              "| 日期 | 上证当日涨跌% | 00基线信号数 |", "|------|------------:|----------:|"]
    from 分析决策.市场环境.index_data import load_index_daily  # noqa: E402
    idx = load_index_daily("上证指数")
    recs00 = results["00基线"]["records"]
    day_counts: dict[str, int] = {}
    for rec in recs00:
        d = rec.signal.date.strftime("%Y-%m-%d")
        day_counts[d] = day_counts.get(d, 0) + 1
    gate_days = []
    for d, cnt in day_counts.items():
        hit = idx[idx["日期"] == pd.Timestamp(d)]
        pct = hit["涨跌幅"].iloc[0] if len(hit) else float("nan")
        if len(hit) and pct < -2.0:
            gate_days.append((d, pct, cnt))
    gate_days.sort(key=lambda x: x[1])
    if gate_days:
        for d, pct, cnt in gate_days[:10]:
            lines.append(f"| {d} | {pct:+.2f}% | {cnt} |")
    else:
        lines.append("| （无：样本内信号日无一跌破指数阈值） | - | - |")
    lines.append("")
    lines.append("> 注：上表列出 00 基线组中指数当日跌破 -2% 阈值的信号——"
                 "这些信号在 11 全开组全部被环境闸门否决（见上表总对照的环境否决计数）。")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
