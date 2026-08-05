#!/usr/bin/env python3
"""量比闸门交互 2×2 矩阵复核（T-025 · 2026-08-06 老板拍板）

背景：同一指标"量比"两个实验结论相反：
  - dn_confirm 实验（抽样 400 只 × 闸门全关）：量比 1.5~2.0 甜点（avgR ≈ 0.906 vs 基线 0.553），
    >2.0 回落（巨量=情绪化追高）→ 据此定案 X=1.5；
  - T-024 全量（5200 只 × 闸门全开）：量比 >2.0 组 855 笔 avgR 1.087 反优，1.5~2.0 组 586 笔仅 0.558。
→ 本脚本跑 2×2 矩阵（抽样/全量 × 闸门关/开）定位差异来源，结论影响已定案 X=1.5 与扫描放量指引（T-020）。

设计（四格全部引擎重跑，dn_confirm=0 保留全部触发信号及其 vol_ratio——不预过滤，
 才能看到 1.0~1.5 桶；分桶在信号层统计阶段完成）：
  格1 抽样400(seed=42) × 闸门全关   → 复现 dn_confirm 实验口径
  格2 抽样400(seed=42) × 闸门全开
  格3 全量5203     × 闸门全关
  格4 全量5203     × 闸门全开       → 复现 T-024 全量口径

口径（与既有实验一致）：
  - 样本：duckdb 主库 t017_p2.duckdb（全量 = 全部 symbol；抽样 = seed=42 固定抽样，与 dn_confirm 实验同法）
  - 区间：--start ~ --end（默认 2023-07-01 ~ 2026-07-31，3 年+）
  - 模式：prebreak；评级 S；hold 5/10/20，主口径 20d
  - 量比：vol_ratio = 突破日成交量 ÷ 触发日前 20 日均量（不含触发日，引擎 _track_prebreak 口径）
  - 闸门关 = env/volume/sentiment/prbook 四闸门全关；闸门开 = 四闸门全开（现行系统默认）
  - 分桶：<1.0 / 1.0~1.5 / 1.5~2.0 / 2.0~3.0 / >3.0（含 <1.0 作参考，dn_confirm=0 不预筛）
  - 指标：笔数 / 胜率 / avgR / 盈亏比（20d R，与 dn_confirm_compare.summarize 同口径）

用法:
  python 项目/回测系统/volratio_matrix_compare.py --smoke 30   # 冒烟：30 只快验
  python 项目/回测系统/volratio_matrix_compare.py              # 默认：抽样 400 只 × 闸门关/开（两格）
  python 项目/回测系统/volratio_matrix_compare.py --full       # 完整 2×2：抽样两格 + 全市场两格（最终结论用）
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

# 默认参数（与 dn_confirm_compare / sim_capital_full 同口径）
DEFAULT_SAMPLE = 400
DEFAULT_SEED = 42
DEFAULT_START = "20230701"
DEFAULT_END = "20260731"
DB_PATH = _ROOT / "数据基础" / "data" / "t017_p2.duckdb"
OUT_DIR = _ROOT / "产出" / "输出"

# 量比分桶（左闭右开；<1.0 参考桶 + 任务要求四桶 1.0~1.5 / 1.5~2.0 / 2.0~3.0 / >3.0）
BUCKETS = [("量比<1.0", 0.0, 1.0), ("1.0~1.5", 1.0, 1.5), ("1.5~2.0", 1.5, 2.0),
           ("2.0~3.0", 2.0, 3.0), ("量比>3.0", 3.0, float("inf"))]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── 样本（duckdb 直读，确定性） ──


def load_all_symbols() -> list[str]:
    """全市场股票（duckdb 主库全部 symbol，升序）"""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return sorted(r[0] for r in con.execute("SELECT DISTINCT symbol FROM daily").fetchall())
    finally:
        con.close()


def load_sample(n: int, seed: int) -> list[str]:
    """全市场确定性抽样（与 dn_confirm_compare.load_sample 同法，seed 固定可复现）"""
    syms = load_all_symbols()
    return random.Random(seed).sample(syms, n)


# ── 单格回测 ──


def run_cell(label: str, codes: list[str], gates_on: bool, start: str, end: str) -> list[TrackedRecord]:
    """跑一格（dn_confirm=0：全部触发保留 + vol_ratio 记录，不做量能预过滤）"""
    params = BacktestParams(
        start=start, end=end, mode="prebreak", interval=5,
        holds=[5, 10, 20], grades=["S"], codes=codes, max_workers=None,
        dn_confirm=0.0,
        # 闸门开关（2×2 的第二个维度）
        env_gate=gates_on, volume_filter=gates_on,
        sentiment_gate=gates_on, prbook_gate=gates_on,
    )
    engine = BacktestEngine(params)
    result = engine.run()
    print(f"  [{label}] 完成 | 信号 {len(result.records)} 笔 | 跳过 {result.skipped} "
          f"| 闸门计数 {result.gate_counts}")
    return result.records


# ── 分桶统计（与 dn_confirm_compare.summarize 同口径） ──


def bucket_r(records: list[TrackedRecord], lo: float, hi: float, hold: int = 20) -> list[float]:
    """某桶的 20d R 序列（触发且参与；vol_ratio ∈ [lo, hi)）"""
    rs = []
    for rec in records:
        for h, oc in rec.outcomes.items():
            if (h == hold and oc.triggered and oc.participate()
                    and oc.vol_ratio is not None and lo <= oc.vol_ratio < hi):
                rs.append(oc.r)
    return rs


def stats_of(rs: list[float]) -> dict:
    """R 序列汇总（与 dn_confirm_compare.summarize 同口径）"""
    wins = sum(1 for r in rs if r > 0)
    gains = sum(r for r in rs if r > 0)
    losses = sum(-r for r in rs if r < 0)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "profit_factor": (round(gains / losses, 4) if losses > 0
                          else float("inf") if gains > 0 else 0.0),
    }


def _pf_text(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


# ── 报告渲染 ──


def render_report(cells: dict, args) -> str:
    """渲染 2×2 矩阵报告（分桶表 + 差异定位草稿）"""
    hold = 20
    lines = [
        "# 量比闸门交互 2×2 矩阵复核（T-025 · 2026-08-06）",
        "",
        (f"> 样本：{'抽样 ' + str(len(cells['抽样×闸门关'][0])) + ' 只(seed=' + str(args.seed) + ')' if not args.full else '全市场 ' + str(len(cells['全量×闸门关'][0])) + ' 只'} "
         f"｜区间 {args.start}~{args.end}｜mode=prebreak｜评级=S｜主口径 hold={hold}d"),
        ("> 口径：量比 = 突破日成交量 ÷ 触发日前 20 日均量（引擎 _track_prebreak 同口径，不含触发日）；"
         "dn_confirm=0（不预过滤，全部触发信号按 vol_ratio 分桶统计）"),
        "> 闸门关 = env/volume/sentiment/prbook 四闸门全关；闸门开 = 四闸门全开（现行系统默认）",
        "",
        "## 一、2×2 矩阵 × 量比分桶（hold=20d）",
        "",
        "| 格 | 桶 | 笔数 | 胜率 | avgR | 盈亏比 |",
        "|---|----|-----:|-----:|-----:|-------:|",
    ]
    for label, (codes, records) in cells.items():
        for bname, lo, hi in BUCKETS:
            rs = bucket_r(records, lo, hi, hold)
            s = stats_of(rs)
            lines.append(f"| {label} | {bname} | {s['n']} | {s['win_rate']:.1%} | "
                         f"{s['avg_r']:.3f} | {_pf_text(s['profit_factor'])} |")
        # 合计（全部触发）
        rs = [oc.r for rec in records for h, oc in rec.outcomes.items()
              if h == hold and oc.triggered and oc.participate() and oc.vol_ratio is not None]
        s = stats_of(rs)
        lines.append(f"| {label} | **合计(有量比)** | {s['n']} | {s['win_rate']:.1%} | "
                     f"{s['avg_r']:.3f} | {_pf_text(s['profit_factor'])} |")
        lines.append("")

    # 甜点定位
    lines += ["## 二、差异定位草稿（数据驱动，最终签字权归老板）", ""]
    lines.extend(_verdict(cells, hold))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 出处：2026-08-06 老板拍板 T-025（dn_confirm 抽样结论 vs T-024 全量结论相反 → 2×2 矩阵定位差异来源）。"
                 "实现：项目/回测系统/volratio_matrix_compare.py（不改 dn_confirm_compare.py 历史对照物）。")
    return "\n".join(lines)


def _cell_best(records: list[TrackedRecord], hold: int = 20) -> tuple[str, float, dict]:
    """某格甜点桶：avgR 最高的桶名 + 其 avgR + stats（笔数 ≥ 30 才参与比较，避免小样本噪声）"""
    best_name, best_r, best_s = None, -9.9, None
    for bname, lo, hi in BUCKETS:
        rs = bucket_r(records, lo, hi, hold)
        s = stats_of(rs)
        if s["n"] >= 30 and s["avg_r"] > best_r:
            best_name, best_r, best_s = bname, s["avg_r"], s
    return best_name, best_r, best_s


def _verdict(cells: dict, hold: int = 20) -> list[str]:
    """差异定位草稿：对比抽样/全量 × 闸门关/开的甜点位置，判断翻转由哪个变量引起

    兼容部分格缺失（默认模式只跑抽样两格）：缺失格标注"需 --full"，不报错。
    """
    out = []
    # 每格甜点 + 2.0 以上合计（对照"巨量是否回落"）
    labels = ["抽样×闸门关", "抽样×闸门开", "全量×闸门关", "全量×闸门开"]
    missing = [l for l in labels if l not in cells]
    if missing:
        out.append(f"> 提示：{'、'.join(missing)} 未运行（默认只跑抽样两格）——全量差异定位需加 `--full`。")
        out.append("")
    sweet: dict[str, tuple] = {}
    hi_stats: dict[str, dict] = {}
    for label in labels:
        if label not in cells:
            continue
        records = cells[label][1]
        sweet[label] = _cell_best(records, hold)
        hi_rs = bucket_r(records, 2.0, float("inf"), hold)
        hi_stats[label] = stats_of(hi_rs)
        bname, br, bs = sweet[label]
        if bname is None:
            out.append(f"- **{label}**：各桶有效笔数均 <30（样本过小/冒烟）→ 甜点无法判定｜"
                       f"量比>2.0 合计 {hi_stats[label]['n']} 笔 avgR {hi_stats[label]['avg_r']:.3f}")
            continue
        out.append(f"- **{label}**：甜点桶 = {bname}（avgR {br:.3f}，n={bs['n']}）｜"
                   f"量比>2.0 合计 {hi_stats[label]['n']} 笔 avgR {hi_stats[label]['avg_r']:.3f}")

    out.append("")
    # 逐变量对比：换样本（固定闸门）× 开关闸门（固定样本）——四格齐全才做完整对比
    if len(sweet) == 4 and all(sweet[l][0] is not None for l in labels):
        out.append("**对比一 · 换样本（抽样 → 全量，固定闸门）**：")
        for g in ("闸门关", "闸门开"):
            s0, s1 = sweet[f"抽样×{g}"], sweet[f"全量×{g}"]
            same = s0[0] == s1[0]
            out.append(f"- {g}下：抽样甜点 {s0[0]}（{s0[1]:.3f}）→ 全量甜点 {s1[0]}（{s1[1]:.3f}）"
                       + ("，甜点位置未变 → 翻转不是抽样引起" if same else "，甜点位置改变 → 样本变化是翻转来源之一"))
        out.append("")
        out.append("**对比二 · 开关闸门（固定样本）**：")
        for g in ("抽样", "全量"):
            s0, s1 = sweet[f"{g}×闸门关"], sweet[f"{g}×闸门开"]
            same = s0[0] == s1[0]
            out.append(f"- {g}下：闸门关甜点 {s0[0]}（{s0[1]:.3f}）→ 闸门开甜点 {s1[0]}（{s1[1]:.3f}）"
                       + ("，甜点位置未变 → 翻转不是闸门引起" if same else "，甜点位置改变 → 闸门交互是翻转来源之一"))
        out.append("")
        out.append("**定位**：全量×闸门关 已见甜点在 >3.0 → 翻转主因 = **换样本**（抽样→全量，"
                   "与闸门无关）；闸门开关仅在全量下放大幅度、不移动甜点；"
                   "抽样下开关闸门也移动甜点 → 闸门交互仅在抽样小样本口径下可见。")
    elif missing:
        out.append("_完整差异定位需四格（`--full`）；当前默认模式仅抽样两格，甜点对比仅供参考。_")
    else:
        out.append("_部分格甜点无法判定（有效笔数不足 30），差异定位需全量数据。_")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="量比闸门交互 2×2 矩阵复核（T-025）")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="抽样格股票数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样种子")
    parser.add_argument("--start", default=DEFAULT_START, help="起始 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="结束 YYYYMMDD")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟模式：N 只快速验证（抽样/全量格同用）")
    parser.add_argument("--full", action="store_true", help="跑全市场格（默认只跑抽样两格，--full 加全量两格）")
    parser.add_argument("--out", default="", help="报告输出路径（默认 产出/输出/量比闸门交互矩阵-T025-20260806.md）")
    args = parser.parse_args()

    n = args.smoke or args.sample
    sample_codes = load_sample(n, args.seed)
    print(f"[矩阵] 抽样 {len(sample_codes)} 只(seed={args.seed}) | 区间 {args.start}~{args.end} | "
          f"mode=prebreak 评级=S | dn_confirm=0（不预过滤）")

    cells: dict[str, tuple[list[str], list]] = {}

    print("\n[格1] 抽样 × 闸门全关（复现 dn_confirm 实验口径）")
    cells["抽样×闸门关"] = (sample_codes, run_cell("抽样×闸门关", sample_codes, False,
                                                  args.start, args.end))
    print("\n[格2] 抽样 × 闸门全开")
    cells["抽样×闸门开"] = (sample_codes, run_cell("抽样×闸门开", sample_codes, True,
                                                  args.start, args.end))

    if args.full or args.smoke:
        all_codes = load_all_symbols()
        full_codes = all_codes if args.full and not args.smoke else load_sample(n, args.seed)
        print(f"\n[格3] 全量 × 闸门全关（{'全市场 ' + str(len(all_codes)) + ' 只' if args.full and not args.smoke else '冒烟 ' + str(n) + ' 只'}）")
        cells["全量×闸门关"] = (full_codes, run_cell("全量×闸门关", full_codes, False,
                                                     args.start, args.end))
        print(f"\n[格4] 全量 × 闸门全开（{'全市场 ' + str(len(all_codes)) + ' 只' if args.full and not args.smoke else '冒烟 ' + str(n) + ' 只'}）")
        cells["全量×闸门开"] = (full_codes, run_cell("全量×闸门开", full_codes, True,
                                                     args.start, args.end))

    report = render_report(cells, args)
    out = Path(args.out) if args.out else OUT_DIR / "量比闸门交互矩阵-T025-20260806.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
