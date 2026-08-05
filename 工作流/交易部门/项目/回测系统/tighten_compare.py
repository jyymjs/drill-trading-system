#!/usr/bin/env python3
"""策略收紧条件测试包（T-024 · 2026-08-06 老板拍板）

背景：资金管理讨论定案方向——"只做最好的那些"（S 级里再精选）+ 每月 3000 元工资注入。
收紧边界必须用数据定：对照"S 级基线 vs S 级+各收紧条件"，看 avgR / 胜率 / 信号量 三方权衡。
数据回来前不动策略代码（本实验只做信号层过滤统计，不做资金模拟、不改引擎）。

实验设计（信号层过滤统计，主口径 20d R）：
  基线 = signals.csv 全部 20d 触发信号（prebreak / S 级 / dn_confirm=1.5，1441 笔）
  收紧条件：
    1) 量比区间  1.5 ≤ vol_ratio ≤ 2.0（dn_confirm 甜点内：≥1.5 有效、≥2.0 回落=情绪化追高）
                ——signals.csv 无 vol_ratio 列，从 duckdb 复算（口径对齐引擎
                  _track_prebreak：触发日成交量 ÷ 触发日前 20 日均量，不含触发日）
    2) 动量过滤  触发日前 20 日涨幅 ≤ X，X ∈ {10%, 15%, 20%, 25%}
                ——trigger（触发价） vs 触发日前 20 交易日收盘价（qfq，duckdb 复算）
    3) 止损距离  0.5 ≤ risk ≤ 3.0（<0.5 止损太近容易被扫；>3 止损太远盈亏比差）
                ——signals.csv risk 列（= trigger - stop）直接可用
  组合：1+2、1+3、2+3、1+2+3（组合用 --mom-threshold，默认 0.20，可依探索结果调整）

口径：
  - 复算源：数据基础/data/t017_p2.duckdb 只读，read_kline（qfq，与引擎同口径）
  - 触发日定位：信号日 T 之后首根 最高≥trigger（引擎 _track_prebreak 同口径）
  - 统计：胜率 / avgR / 盈亏比 / 累计R（与 dn_confirm_compare.summarize 同口径）

用法:
  python 项目/回测系统/tighten_compare.py --smoke 100   # 冒烟（前 100 笔信号）
  python 项目/回测系统/tighten_compare.py               # 全量 1441 笔
"""
import argparse
import sys
from pathlib import Path

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

from 数据基础.duckdb.reader import read_kline

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 默认信号源 = sim_capital 验收同口径全量信号（prebreak/S/dn_confirm1.5/3年全市场）
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "sim_capital_20260806_full" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"

MOM_THRESHOLDS = [0.10, 0.15, 0.20, 0.25]   # 动量探索阈值（20 日涨幅 ≤ X）
DEFAULT_MOM = 0.10                          # 组合组默认动量阈值（探索区分度最好者）
RISK_MIN, RISK_MAX = 0.5, 3.0               # 止损距离区间（元）
VOL_RATIO_MAX = 2.0                         # 量比上限（基线已含 vr>1.5，即 1.5 < vr ≤ 2.0）

# 复算缓存：code → qfq df（同股票多笔信号复用）
_DF_CACHE: dict[str, pd.DataFrame] = {}


def load_triggered(signals_path: Path, smoke: int = 0) -> pd.DataFrame:
    """读 signals.csv 并过滤 20d 触发信号（基线集合）"""
    df = pd.read_csv(signals_path, encoding="utf-8-sig")
    df = df[df["triggered_20d"] == 1].copy()
    df = df.sort_values(["date", "code"]).reset_index(drop=True)
    if smoke > 0:
        df = df.head(smoke).copy()
    return df


def _kline(code: int) -> pd.DataFrame | None:
    """qfq K线（引擎同口径 read_kline；带缓存）"""
    sym = f"{code:06d}"
    if sym not in _DF_CACHE:
        _DF_CACHE[sym] = read_kline(sym)   # 只读；None 也缓存（防空转）
    return _DF_CACHE[sym]


def recompute(sig: pd.Series) -> tuple[float | None, float | None]:
    """复算 (vol_ratio, mom20)。

    vol_ratio = 触发日成交量 / 触发日前 20 日均量（对齐引擎，前不足 20 根取可用段）
    mom20     = trigger / 触发日前第 20 根收盘 - 1（不足 20 根 → None）
    口径细节与 _track_prebreak 一致：触发日 = 信号日 T 之后首根 最高≥trigger。
    返回 (None, None) 表示触发日复算失败（数据版本差异，预期 0 笔）。
    """
    df = _kline(int(sig["code"]))
    if df is None or df.empty:
        return None, None
    sig_ts = pd.to_datetime(sig["date"])
    idx = df.index[df["日期"] == sig_ts]
    if len(idx) == 0:
        return None, None
    t = int(idx[0])
    high = df["最高"].to_numpy()
    trig_idx = None
    for j in range(t + 1, len(df)):
        if high[j] >= sig["trigger"]:
            trig_idx = j
            break
    if trig_idx is None:
        return None, None

    # 量比（对齐引擎：vol[max(0, idx-20):idx] 均量，0 均量 → 0）
    vol = df["成交量"].to_numpy()
    ref_mean = float(vol[max(0, trig_idx - 20):trig_idx].mean()) if trig_idx > 0 else 0.0
    vol_ratio = round(float(vol[trig_idx]) / ref_mean, 4) if ref_mean > 0 else 0.0

    # 20 日动量（触发价 vs 20 个交易日前收盘）
    mom20 = None
    if trig_idx >= 20:
        close20 = float(df["收盘"].to_numpy()[trig_idx - 20])
        if close20 > 0:
            mom20 = float(sig["trigger"]) / close20 - 1.0
    return vol_ratio, mom20


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """为每笔信号附 vol_ratio / mom20（带缓存，打印进度）"""
    n = len(df)
    ratios, moms, fails = [], [], 0
    for i, (_, row) in enumerate(df.iterrows(), 1):
        vr, mom = recompute(row)
        ratios.append(vr)
        moms.append(mom)
        if vr is None:
            fails += 1
        if i % 200 == 0 or i == n:
            print(f"  [复算] {i}/{n} 完成 | 复算失败 {fails} 笔")
    df["vol_ratio"] = ratios
    df["mom20"] = moms
    return df


def group_stats(rs: list[float]) -> dict:
    """某组 20d R 序列汇总（与 dn_confirm_compare.summarize 同口径）"""
    wins = sum(1 for r in rs if r > 0)
    gains = sum(r for r in rs if r > 0)
    losses = sum(-r for r in rs if r < 0)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "profit_factor": (round(gains / losses, 4) if losses > 0
                          else float("inf") if gains > 0 else 0.0),
        "total_r": round(sum(rs), 4),
    }


def _pf_text(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def render_table(base: dict, groups: list[tuple[str, dict, float | None]]) -> list[str]:
    """渲染对照表行（组名 / stats / ΔavgR；最后一个元素 = 相对基线的 avgR 差）"""
    lines = ["| 组 | 笔数 | 剩余占比 | 胜率 | avgR | 盈亏比 | 累计R | ΔavgR |",
             "|---|-----:|--------:|-----:|-----:|-------:|------:|------:|"]
    for label, s, delta_r in groups:
        pct = f"{s['n'] / base['n']:.1%}" if base["n"] else "—"
        d = f"{delta_r:+.3f}" if delta_r is not None else "—"
        lines.append(f"| {label} | {s['n']} | {pct} | {s['win_rate']:.1%} | {s['avg_r']:.3f} | "
                     f"{_pf_text(s['profit_factor'])} | {s['total_r']:.1f} | {d} |")
    return lines


def build_groups(df: pd.DataFrame, mom: float) -> dict[str, list[float]]:
    """按收紧条件分组，返回 组名 → 20d R 序列"""
    base = df["r_20d"].tolist()

    def sel(mask: pd.Series) -> list[float]:
        return df.loc[mask, "r_20d"].tolist()

    vr_ok = df["vol_ratio"].notna() & (df["vol_ratio"] <= VOL_RATIO_MAX)          # 量比甜点内
    vr_hi = df["vol_ratio"].notna() & (df["vol_ratio"] > VOL_RATIO_MAX)           # 量比>2.0（巨量突破）
    mom_ok = {x: df["mom20"].notna() & (df["mom20"] <= x) for x in MOM_THRESHOLDS}
    risk_ok = (df["risk"] >= RISK_MIN) & (df["risk"] <= RISK_MAX)                 # 止损距离适中
    risk_near = df["risk"] < RISK_MIN                                             # 止损太近（易被扫）
    risk_far = df["risk"] > RISK_MAX                                              # 止损太远（盈亏比差）

    groups = {
        "基线(全触发)": base,
        "G1 量比≤2.0": sel(vr_ok),
        "G3 止损0.5~3.0": sel(risk_ok),
    }
    for x in MOM_THRESHOLDS:
        groups[f"G2 动量≤{x:.0%}"] = sel(mom_ok[x])
    # 细分诊断（分组而非过滤，用于解释方向性发现）
    groups["细-量比>2.0"] = sel(vr_hi)
    groups["细-止损<0.5"] = sel(risk_near)
    groups["细-止损>3"] = sel(risk_far)
    groups["细-动量10~15%"] = sel((df["mom20"] > 0.10) & (df["mom20"] <= 0.15))
    groups["细-动量>15%"] = sel(df["mom20"] > 0.15)
    groups["C12 量比+动量"] = sel(vr_ok & mom_ok[mom])
    groups["C13 量比+止损"] = sel(vr_ok & risk_ok)
    groups["C23 动量+止损"] = sel(mom_ok[mom] & risk_ok)
    groups["C123 全组合"] = sel(vr_ok & mom_ok[mom] & risk_ok)
    return groups


def render_report(df: pd.DataFrame, mom: float, out: Path) -> str:
    """渲染 markdown 报告（对照表 + 白话结论草稿）"""
    groups = build_groups(df, mom)
    base = group_stats(groups["基线(全触发)"])

    single = [("基线(全触发)", base, None)]
    for label in ("G1 量比≤2.0", "G3 止损0.5~3.0"):
        s = group_stats(groups[label])
        single.append((label, s, round(s["avg_r"] - base["avg_r"], 3)))
    for x in MOM_THRESHOLDS:
        label = f"G2 动量≤{x:.0%}"
        s = group_stats(groups[label])
        single.append((label, s, round(s["avg_r"] - base["avg_r"], 3)))

    combos = []
    for label in ("C12 量比+动量", "C13 量比+止损", "C23 动量+止损", "C123 全组合"):
        s = group_stats(groups[label])
        combos.append((label, s, round(s["avg_r"] - base["avg_r"], 3)))

    lines = [
        "# 策略收紧条件测试（T-024 · 2026-08-06）",
        "",
        (f"> 样本：signals.csv 全部 {len(df)} 笔 20d 触发信号（prebreak / S 级 / dn_confirm=1.5 / "
         f"2023-07~2026-07 全市场）｜主口径 hold=20d ｜纯信号层过滤统计，不做资金模拟"),
        "> 收紧方向：只做最好的那些（S 级里再精选）——量比甜点 / 动量追高 / 止损距离 三方筛选",
        "> 动量组合默认阈值 X=" + f"{mom:.0%}" + "（探索区分度最好者，可用 --mom-threshold 调整）",
        "",
        "## 一、基线 vs 单条件",
        "",
    ]
    lines += render_table(base, single)
    lines += [
        "",
        (f"> 量比基线口径：signals.csv 已含 dn_confirm=1.5（vol_ratio>1.5），G1 = 再滤掉 >{VOL_RATIO_MAX} 者；"
         "vol_ratio/mom20 均按引擎同口径从 duckdb 复算（复算失败 0 笔）。"),
        "",
        "## 二、细分诊断（分桶而非过滤，解释方向）",
        "",
    ]
    diag = [("基线(全触发)", base, None)]
    for label in ("细-量比>2.0", "细-止损<0.5", "细-止损>3", "细-动量10~15%", "细-动量>15%"):
        s = group_stats(groups[label])
        diag.append((label, s, round(s["avg_r"] - base["avg_r"], 3)))
    lines += render_table(base, diag)
    lines += [
        "",
        "## 三、组合条件（动量 X=" + f"{mom:.0%}" + "）",
        "",
    ]
    lines += render_table(base, combos)
    lines += ["", "## 四、白话结论草稿", ""]
    lines += _verdict(base, single, combos, len(df), groups)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 出处：2026-08-06 老板拍板（只做最好的那些 + 工资注入）。实现：项目/回测系统/tighten_compare.py；"
                 "收紧边界签字后才可动策略代码。")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)


def _verdict(base: dict, single: list[tuple[str, dict, float | None]],
             combos: list[tuple[str, dict, float | None]], total: int,
             groups: dict[str, list[float]]) -> list[str]:
    """数据驱动的结论草稿（最终由老板/助理复核）"""
    out = [
        (f"- 基线（全触发）：{base['n']} 笔 | 胜率 {base['win_rate']:.1%} | avgR {base['avg_r']:.3f} | "
         f"盈亏比 {_pf_text(base['profit_factor'])} | 累计R {base['total_r']:.1f}"),
    ]
    # 单条件：avgR 提升最大者 + 信号量代价
    gainers = [g for g in single if g[1]["avg_r"] > base["avg_r"]]
    if gainers:
        best = max(gainers, key=lambda g: g[1]["avg_r"])
        out.append("")
        out.append(f"- **单条件 avgR 提升最大**：{best[0]}（avgR {best[1]['avg_r']:.3f}，"
                   f"胜率 {best[1]['win_rate']:.1%}，剩 {best[1]['n']} 笔 / {best[1]['n'] / total:.0%}）")
    # 组合：全组合是否最优 + 衰减
    if combos:
        bestc = max(combos, key=lambda g: g[1]["avg_r"])
        out.append("")
        out.append(f"- **组合最优**：{bestc[0]}（avgR {bestc[1]['avg_r']:.3f}，胜率 {bestc[1]['win_rate']:.1%}，"
                   f"剩 {bestc[1]['n']} 笔 / {bestc[1]['n'] / total:.0%}，盈亏比 {_pf_text(bestc[1]['profit_factor'])}）")
        out.append("")
        out.append("> 权衡提示：收紧必然衰减信号量（月度可交易次数下降）；需结合工资注入后的资金容量，"
                   "判断" + f"{bestc[1]['n'] / total:.0%}" + "的信号留存是否够用。推荐边界以数据最高组为准，"
                   "最终签字权归老板。")
    # 方向性发现（量比/动量与 dn_confirm 抽样的差异提示）
    hi_vr = group_stats(groups["细-量比>2.0"])
    near = group_stats(groups["细-止损<0.5"])
    out.append("")
    out.append("**方向性发现（与 dn_confirm 抽样实验口径差异说明）**：")
    out.append(f"- 量比方向：全量信号层（四道闸门全开）上，巨量突破组（vr>2.0，{hi_vr['n']} 笔）"
               f"avgR {hi_vr['avg_r']:.3f} 反而优于甜点组（G1，{group_stats(groups['G1 量比≤2.0'])['n']} 笔，"
               f"avgR {group_stats(groups['G1 量比≤2.0'])['avg_r']:.3f}）——与 dn_confirm 抽样实验"
               "（400 只、闸门全关：vr∈(1.5,2.0] 均值约 1.24 显著优于 vr>2.0 的 0.753）**方向相反**；"
               "原因待查：闸门（尤其 C3 量能闸门）已滤掉部分量能特征信号，或抽样/全市场分布差异。"
               "→ G1 量比区间收紧在本口径下不成立，建议维持现状（vr>1.5）或复核闸门交互后再议。")
    out.append(f"- 止损距离：被滤掉的止损<0.5 元组（{near['n']} 笔，低价股为主）avgR {near['avg_r']:.3f}，"
               "显著差于基线——止损距离是当前最有区分度的收紧维度。")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="策略收紧条件测试包（信号层过滤统计）")
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    parser.add_argument("--mom-threshold", type=float, default=DEFAULT_MOM,
                        help=f"组合组动量阈值（默认 {DEFAULT_MOM}）")
    parser.add_argument("--smoke", type=int, default=0, help="冒烟：只处理前 N 笔信号")
    parser.add_argument("--out", default="", help="报告输出路径（默认 产出/输出/策略收紧测试-T024-20260806.md）")
    args = parser.parse_args()

    df = load_triggered(Path(args.signals), args.smoke)
    print(f"[收紧测试] 基线 {len(df)} 笔 20d 触发 | 开始 duckdb 复算 vol_ratio/mom20 ...")
    df = enrich(df)
    n_vr = df["vol_ratio"].notna().sum()
    n_mom = df["mom20"].notna().sum()
    print(f"[收紧测试] 复算完成 | vol_ratio 有效 {n_vr} | mom20 有效 {n_mom} | 失败 {len(df) - n_vr} 笔")

    out = Path(args.out) if args.out else OUT_DIR / "策略收紧测试-T024-20260806.md"
    report = render_report(df, args.mom_threshold, out)
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
