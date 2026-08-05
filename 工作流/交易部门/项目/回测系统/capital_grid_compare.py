#!/usr/bin/env python3
"""资金配置参数网格实验（T-023 · 2026-08-06 老板拍板）

背景：资金约束回测结论——5600 元 / 单笔风险 1.5% / 2 持仓 / 整手 100 股 → -14.9%。
根因不是策略：资金约束把可执行股票池挤向低每股风险的低价股（均价 7.96 元，
avgR -0.118），好信号被挡在门外（未成交信号 avgR +0.898）。
→ 网格实验：单笔风险 {1.5, 2, 3, 5%} × 持仓数 {2, 3, 5} = 12 组配置，找最优资金配置。

口径（与 sim_capital 验收一致）：
  - 信号源：prebreak S 级 / dn_confirm 1.5 / entry_20d 触发价 / 2023-07~2026-07 全市场
    （默认 = 产出/输出/sim_capital_20260806_full/signals.csv）
  - 初始资金 5600 元 / 整手 100 股 / 费用 佣金万1.3（最低1元）+ 印花税万5
  - 买入侧印花税误扣 = 已知保守口径，12 组统一，不做修复
每组指标：终值/收益%/胜率/avgR/盈亏比/最大回撤/回撤时长/笔数/年化笔数/100笔节奏/
          执行率/可执行池特征（成交股票均价、每股风险均值、被拒原因分布、未成交信号avgR）
未成交信号 avgR 口径：先按 simulate_capital 同过滤（mode/triggered_{h}d/grades），
          再按 (date, code) 差集剔除成交行，剩余信号集 r_20d 均值（未触发信号不参与）

用法:
  python 项目/回测系统/capital_grid_compare.py \
      --start 20240101 --end 20240630 --risk-list 1.5,3 --pos-list 2,5   # 冒烟（2 组）
  python 项目/回测系统/capital_grid_compare.py                              # 全量 12 组（V1 基线网格）
  python 项目/回测系统/capital_grid_compare.py --c23                        # C23 版 12 组全量
"""
import argparse
import datetime as _dt
import random
import sys
from pathlib import Path

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

# c23_mask 单一来源 = sim_capital（T-024 拍板口径，勿在脚本内复制）
from 回测系统.sim_capital import c23_mask, simulate_capital
from 回测系统.tighten_compare import enrich

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 默认信号源 = sim_capital 验收同口径全量信号（prebreak/S/dn_confirm1.5/3年全市场）
DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "sim_capital_20260806_full" / "signals.csv"
DEFAULT_CAPITAL = 5600.0
DEFAULT_HOLD = "20d"
DEFAULT_GRADES = ["S"]
DEFAULT_MODE = "prebreak"
DEFAULT_START = "20230701"
DEFAULT_END = "20260731"
OUT_DIR = _ROOT / "产出" / "输出"

# 网格：单笔风险 % × 持仓数（12 组）
RISK_LIST = [1.5, 2.0, 3.0, 5.0]
POS_LIST = [2, 3, 5]


def _norm_date(s: str) -> str:
    """'20240101' 或 '2024-01-01' → '2024-01-01'（与 signals.date 列可比）"""
    s = str(s).strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s[:10]


def load_signals(path: Path, start: str, end: str, smoke_codes: int | None,
                 seed: int = 42) -> pd.DataFrame:
    """读 signals.csv → 按区间裁剪 → 可选按股票池缩小（冒烟）"""
    df = pd.read_csv(path, encoding="utf-8-sig")
    if not len(df):
        raise SystemExit(f"无信号数据: {path}")
    d0, d1 = _norm_date(start), _norm_date(end)
    df = df[df["date"].astype(str).str[:10].between(d0, d1)].copy()
    if smoke_codes:
        rng = random.Random(seed)
        codes = rng.sample(sorted(df["code"].unique()), smoke_codes)
        df = df[df["code"].isin(codes)].copy()
    if not len(df):
        raise SystemExit(f"区间 {start}~{end} 裁剪后无信号（请检查 --start/--end 或 --smoke）")
    return df


def pool_features(trades: list[dict], sub: pd.DataFrame, hold: int, mode: str = "prebreak",
                  grades: list[str] | None = None) -> dict:
    """可执行池特征：成交均价 / 每股风险均值 / 未成交信号 avgR

    未成交集口径（与 simulate_capital 内部完全一致）：
      1) 先按 mode == prebreak 且 triggered_{h}d == 1 过滤（未触发信号不参与，其 r=0 混入会稀释）
      2) grades 非空时追加 grade ∈ grades 过滤
      3) 再按 (date, code) 差集剔除成交行 → 剩余 = 触发但被资金约束拒绝的信号
    """
    grades = grades or []
    h = int(str(hold).replace("d", ""))
    sig = sub[(sub["mode"] == mode) & (sub[f"triggered_{h}d"] == 1)]
    if grades:
        sig = sig[sig["grade"].isin(grades)]
    avg_price = float(pd.Series([t["entry"] for t in trades]).mean()) if trades else 0.0
    avg_risk_ps = float(pd.Series([t["risk_actual"] / t["shares"]
                                   for t in trades]).mean()) if trades else 0.0
    r_col = f"r_{h}d"
    if len(sig) and len(trades) < len(sig):
        keys = sig["date"].astype(str).str[:10] + "|" + sig["code"].astype(str)
        traded = {f"{t['date']}|{t['code']}" for t in trades}
        rejected = sig[~keys.isin(traded)]
        rej_avg_r = float(rejected[r_col].mean()) if len(rejected) else 0.0
    else:
        rej_avg_r = 0.0
    return {"avg_price": avg_price, "avg_risk_ps": avg_risk_ps, "rej_avg_r": rej_avg_r}


def run_group(df: pd.DataFrame, capital: float, risk_pct: float, max_positions: int,
              hold: str = DEFAULT_HOLD, mode: str = DEFAULT_MODE,
              grades: list[str] | None = None) -> tuple[dict, dict]:
    """跑一组资金模拟，返回 (res, pool)"""
    res = simulate_capital(df, capital, risk_pct / 100.0, max_positions=max_positions,
                           mode=mode, hold=hold, grades=grades)
    pool = pool_features(res["trades"], df, int(hold.replace("d", "")),
                         mode=mode, grades=grades)
    return res, pool


# V1 网格（T-023）收益最优组——C23 版对照基准（数据来自 产出/输出/网格实验-资金配置-20260806.md）
V1_BEST = {"label": "1.5%×5仓", "total_ret": 25.5, "end_balance": 7026.0,
           "win_rate": 0.400, "avg_r": 0.227, "max_dd_pct": 103.9, "n_exec": 180}


def render_report(rows: list[dict], args) -> str:
    """渲染网格实验报告（markdown）：总览 + 池特征 + 分组边际 + 结论草稿"""
    capital = args.capital
    hold = int(DEFAULT_HOLD.replace("d", ""))
    c23 = bool(getattr(args, "c23", False))
    title = "资金配置参数网格实验 · C23 版回测报告（T-023 延伸）" if c23 \
        else "资金配置参数网格实验 · 回测报告（T-023）"
    background = (
        "背景：T-023 结论——V1 基线网格 1.5%×5仓 +25.5% 最优；C23（动量≤10% + 止损 0.5~3 元）"
        "已进策略且 1.5%×3仓 单点 +69.2%。本实验跑 C23 版 12 组全量，找 C23 下最优资金配置，"
        "并与 V1 网格结论对比。" if c23 else
        "背景：资金约束回测结论——5600 元 / 1.5% / 2 持仓 / 整手 → -14.9%，根因是资金约束把"
        "可执行池挤向低价股（均价 7.96 元、avgR -0.118），好信号被挡（未成交信号 avgR +0.898）。"
        "本实验扫描单笔风险×持仓数 12 组配置找最优。")
    sig_note = (
        f"信号源：{Path(args.signals).name}（{'全市场' if not args.smoke else f'冒烟 {args.smoke} 只'}｜"
        f"区间 {args.start}~{args.end}）｜mode={DEFAULT_MODE} 评级={'/'.join(args.grades)}｜"
        f"hold 主口径 {hold}d｜初始资金 {capital:,.0f} 元｜整手 100 股｜费用 佣金万1.3(最低1元)+印花税万5"
        "（买入侧印花税误扣为已知保守口径，各组统一）"
        + ("｜C23 过滤 = mom20 ≤ 10%（tighten_compare 复算）+ risk 0.5~3 元" if c23 else "")
    )
    lines = [
        f"# {title}",
        "",
        f"> 日期：{_dt.datetime.now().astimezone().date().isoformat()} · {background}",
        f"> {sig_note}",
        "",
        f"## 一、{len(rows)} 组总览（hold={hold}d）",
        "",
        ("| 组 | 风险% | 持仓数 | 终值(元) | 收益% | 胜率 | 平均R | 盈亏比 | 最大回撤% | 回撤时长(天) | "
         "笔数 | 年化笔数 | 100笔节奏(月) | 执行率 |"),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        r = row["res"]
        pf = "∞" if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}"
        lines.append(
            f"| {row['label']} | {row['risk_pct']:.1f}% | {row['max_positions']} | "
            f"{r['end_balance']:,.0f} | {r['total_ret']:+.1f}% | {r['win_rate']:.1%} | "
            f"{r['avg_r']:.3f} | {pf} | {r['max_dd_pct']:.1f}% | {r['dd_days']} | "
            f"{r['n_exec']} | {r['per_year']:.1f} | {r['months_for_100']:.1f} | {r['exec_rate']:.1f}% |")
    lines.append("")
    lines.append("> 口径说明：回撤/资金曲线沿用 sim_capital 验收口径（现金余额峰值追踪，"
                 "持仓期间现金扣至低位会被计为回撤，数值偏保守；12 组统一可比）。")
    lines.append("")

    # 池特征
    lines += ["## 二、可执行池特征（风险%放大是否改善池？）", "",
              ("| 组 | 风险% | 持仓数 | 成交均价(元) | 每股风险均值(元) | 未成交信号avgR | 被拒原因 TOP3 |"),
              "|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        p, r = row["pool"], row["res"]
        top = " / ".join(f"{k}×{v}" for k, v in
                         sorted(r["reasons"].items(), key=lambda x: -x[1])[:3]) or "—"
        lines.append(f"| {row['label']} | {row['risk_pct']:.1f}% | {row['max_positions']} | "
                     f"{p['avg_price']:.2f} | {p['avg_risk_ps']:.3f} | {p['rej_avg_r']:+.3f} | {top} |")
    lines.append("")
    lines.append("> 未成交信号 avgR 为触发但被资金约束挡在门外的信号集 20d 平均R；"
                 "成交均价/每股风险均值上升 + 未成交 avgR 高 → 池改善（能买更贵的股票）。")
    lines.append("")

    # 收益矩阵 + 池特征矩阵（分组边际）
    lines += ["## 三、分组边际矩阵", "", "**收益% 矩阵（行=单笔风险%，列=持仓数）**", "",
              "| 风险% \\ 持仓数 | 2 持仓 | 3 持仓 | 5 持仓 |", "|---:|---:|---:|---:|"]
    by = {(row["risk_pct"], row["max_positions"]): row for row in rows}
    for risk in RISK_LIST:
        if not any((risk, pos) in by for pos in POS_LIST):
            continue
        cells = [f"{by[(risk, pos)]['res']['total_ret']:+.1f}%" if (risk, pos) in by else "—"
                 for pos in POS_LIST]
        lines.append(f"| {risk:.1f}% | " + " | ".join(cells) + " |")
    lines.append("")
    lines += ["**成交均价（元）矩阵**", ""]
    lines += ["| 风险% \\ 持仓数 | 2 持仓 | 3 持仓 | 5 持仓 |", "|---:|---:|---:|---:|"]
    for risk in RISK_LIST:
        if not any((risk, pos) in by for pos in POS_LIST):
            continue
        cells = [f"{by[(risk, pos)]['pool']['avg_price']:.2f}" if (risk, pos) in by else "—"
                 for pos in POS_LIST]
        lines.append(f"| {risk:.1f}% | " + " | ".join(cells) + " |")
    lines.append("")

    # 结论草稿
    lines += ["## 四、结论（数据驱动草稿，最终签字权归老板）", ""]
    lines.extend(_verdict(rows))
    lines.append("")
    if c23:
        lines += ["## 五、C23 vs V1 网格对照（结论草稿）", ""]
        lines.extend(_verdict_c23(rows))
        lines.append("")
    lines.append("> 判定说明：最优组 = 收益/回撤综合；风险%放大后若成交均价与每股风险均值上升、"
                 "未成交信号 avgR 仍为正 → 池改善且收益提升 → 支持提高单笔风险；"
                 "持仓数边际 = 分散（回撤↓）vs 资金摊薄（单笔变小）权衡。"
                 "资金增长到何种水平配置应变的量化建议见分析正文（人工复核）。")
    lines.append("")
    lines.append("---")
    lines.append("")
    if c23:
        lines.append("> 出处：2026-08-06 老板拍板 T-023 资金配置网格实验（C23 版延伸）。实现：回测系统 "
                     "`capital_grid_compare.py --c23`（12 组 = 风险 {1.5,2,3,5%} × 持仓 {2,3,5}，"
                     "C23 过滤复用 sim_capital.c23_mask + tighten_compare.enrich，模拟复用 "
                     "sim_capital.simulate_capital，口径与 V1 网格一致）。")
    else:
        lines.append("> 出处：2026-08-06 老板拍板 T-023 资金配置网格实验。实现：回测系统 "
                     "`capital_grid_compare.py`（12 组 = 风险 {1.5,2,3,5%} × 持仓 {2,3,5}，"
                     "复用 sim_capital.simulate_capital，口径与其验收一致）。")
    return "\n".join(lines)


def _verdict(rows: list[dict]) -> list[str]:
    """数据驱动的结论草稿（最优组/池改善判定/持仓边际；第 4 条资金水平建议留人工）"""
    if len(rows) < 2:
        return ["_样本组不足，无法自动判定，请人工复核。_"]
    out: list[str] = []
    # 1) 最优组
    best = max(rows, key=lambda r: r["res"]["total_ret"])
    min_dd = min(rows, key=lambda r: r["res"]["max_dd_pct"])
    out.append(f"- **收益最优**：{best['label']}（风险 {best['risk_pct']:.1f}% × 持仓 "
               f"{best['max_positions']}）→ 终值 {best['res']['end_balance']:,.0f} 元 "
               f"（{best['res']['total_ret']:+.1f}%），回撤 {best['res']['max_dd_pct']:.1f}%，"
               f"胜率 {best['res']['win_rate']:.1%}，avgR {best['res']['avg_r']:.3f}，"
               f"{best['res']['n_exec']} 笔。")
    out.append(f"- **回撤最小**：{min_dd['label']}（{min_dd['res']['max_dd_pct']:.1f}%，"
               f"回撤时长 {min_dd['res']['dd_days']} 天）。")
    # 2) 风险%放大 → 池是否改善（固定持仓数，比较 1.5% vs 最高风险组）
    for pos in POS_LIST:
        r15 = next((r for r in rows if r["risk_pct"] == 1.5 and r["max_positions"] == pos), None)
        rmax = max((r for r in rows if r["max_positions"] == pos), key=lambda r: r["risk_pct"],
                   default=None)
        if r15 and rmax and rmax["risk_pct"] > 1.5:
            d_price = rmax["pool"]["avg_price"] - r15["pool"]["avg_price"]
            d_ret = rmax["res"]["total_ret"] - r15["res"]["total_ret"]
            d_rej = rmax["pool"]["rej_avg_r"] - r15["pool"]["rej_avg_r"]
            out.append(f"- 持仓 {pos}：风险 1.5%→{rmax['risk_pct']:.1f}%：成交均价 "
                       f"{r15['pool']['avg_price']:.2f}→{rmax['pool']['avg_price']:.2f} 元"
                       f"（{d_price:+.2f}），收益 {r15['res']['total_ret']:+.1f}%→"
                       f"{rmax['res']['total_ret']:+.1f}%（{d_ret:+.1f}pp），未成交 avgR "
                       f"{r15['pool']['rej_avg_r']:+.3f}→{rmax['pool']['rej_avg_r']:+.3f}"
                       f"（{d_rej:+.3f}）→ "
                       + ("**池改善且收益提升**" if d_price > 0 and d_ret > 0
                          else "池改善但收益未同步提升" if d_price > 0
                          else "池未见改善"))
    # 3) 持仓数边际（固定风险%，2→5）
    for risk in RISK_LIST:
        p2 = next((r for r in rows if r["risk_pct"] == risk and r["max_positions"] == 2), None)
        p5 = next((r for r in rows if r["risk_pct"] == risk and r["max_positions"] == 5), None)
        if p2 and p5:
            out.append(f"- 风险 {risk:.1f}%：持仓 2→5：收益 {p2['res']['total_ret']:+.1f}%→"
                       f"{p5['res']['total_ret']:+.1f}%（{p5['res']['total_ret'] - p2['res']['total_ret']:+.1f}pp），"
                       f"回撤 {p2['res']['max_dd_pct']:.1f}%→{p5['res']['max_dd_pct']:.1f}%，"
                       f"笔数 {p2['res']['n_exec']}→{p5['res']['n_exec']}")
    out.append("")
    out.append("> **当前 5600 元最优配置与资金增长后的配置变化建议**：由分析正文人工复核"
               "（自动草稿只出数据）。判据：单笔风险额 = 初始资金 × 风险%，资金翻倍后同样风险%"
               "可买翻倍价格区间的股票 → 池整体右移，需重新评估是否下调风险%或扩持仓数。")
    return out


def _verdict_c23(rows: list[dict]) -> list[str]:
    """C23 版对照结论：C23 最优 vs V1 最优（1.5%×5仓 +25.5%）；两规律是否被 C23 改变"""
    if len(rows) < 2:
        return ["_样本组不足，无法自动判定，请人工复核。_"]
    best = max(rows, key=lambda r: r["res"]["total_ret"])
    out = [
        (f"- **C23 版收益最优**：{best['label']}（风险 {best['risk_pct']:.1f}% × 持仓 "
         f"{best['max_positions']}）→ 终值 {best['res']['end_balance']:,.0f} 元"
         f"（{best['res']['total_ret']:+.1f}%），回撤 {best['res']['max_dd_pct']:.1f}%，"
         f"胜率 {best['res']['win_rate']:.1%}，avgR {best['res']['avg_r']:.3f}，"
         f"{best['res']['n_exec']} 笔。"),
        (f"- **vs V1 网格最优（{V1_BEST['label']}）**：收益 {V1_BEST['total_ret']:+.1f}% "
         f"→ {best['res']['total_ret']:+.1f}%（{best['res']['total_ret'] - V1_BEST['total_ret']:+.1f}pp），"
         f"终值 {V1_BEST['end_balance']:,.0f} → {best['res']['end_balance']:,.0f} 元，"
         f"胜率 {V1_BEST['win_rate']:.1%} → {best['res']['win_rate']:.1%}，"
         f"avgR {V1_BEST['avg_r']:.3f} → {best['res']['avg_r']:.3f}，"
         f"回撤 {V1_BEST['max_dd_pct']:.1f}% → {best['res']['max_dd_pct']:.1f}%，"
         f"笔数 {V1_BEST['n_exec']} → {best['res']['n_exec']}。"),
        "",
    ]
    # 持仓数规律（V1：持仓 2→5 收益普遍大幅提升 = 关键变量）：C23 下逐风险档方向
    pos_dirs: list[str] = []
    n_pos_up = 0
    for risk in RISK_LIST:
        p2 = next((r for r in rows if r["risk_pct"] == risk and r["max_positions"] == 2), None)
        p5 = next((r for r in rows if r["risk_pct"] == risk and r["max_positions"] == 5), None)
        if p2 and p5:
            d = p5["res"]["total_ret"] - p2["res"]["total_ret"]
            pos_dirs.append(f"风险{risk:.1f}%：{p2['res']['total_ret']:+.1f}→"
                            f"{p5['res']['total_ret']:+.1f}%（{d:+.1f}pp）")
            if d > 0:
                n_pos_up += 1
    pos_hold = "保持" if n_pos_up >= 3 else "改变/弱化"
    out.append(f"- **持仓数边际（V1 关键变量）**：C23 下持仓 2→5 "
               f"{' / '.join(pos_dirs)}——{n_pos_up}/4 档风险方向为正 → "
               f"**「持仓数关键」规律{pos_hold}**。")
    # 风险%规律（V1：风险%放大收益普遍下滑/无效）：C23 下固定持仓 1.5%→5%
    risk_dirs: list[str] = []
    n_risk_up = 0
    for pos in POS_LIST:
        r15 = next((r for r in rows if r["risk_pct"] == 1.5 and r["max_positions"] == pos), None)
        r50 = next((r for r in rows if r["risk_pct"] == 5.0 and r["max_positions"] == pos), None)
        if r15 and r50:
            d = r50["res"]["total_ret"] - r15["res"]["total_ret"]
            risk_dirs.append(f"持仓{pos}：{r15['res']['total_ret']:+.1f}→"
                             f"{r50['res']['total_ret']:+.1f}%（{d:+.1f}pp）")
            if d > 0:
                n_risk_up += 1
    risk_invalid = "保持" if n_risk_up == 0 else "被改变"
    risk_note = " / ".join(risk_dirs) if risk_dirs else "（无 5% 组，未比较）"
    out.append(f"- **风险%边际（V1 认为无效变量）**：C23 下 1.5%→5% "
               f"{risk_note} → **「风险%无效」规律{risk_invalid}**。")
    # 资金约束退化检查：同风险%下 5 仓与 3 仓成交集是否重合（C23 池右移 → 5 仓买不满）
    degen: list[str] = []
    for risk in RISK_LIST:
        r3 = next((r for r in rows if r["risk_pct"] == risk and r["max_positions"] == 3), None)
        r5 = next((r for r in rows if r["risk_pct"] == risk and r["max_positions"] == 5), None)
        if r3 and r5:
            t3 = {(t["date"], t["code"]) for t in r3["res"]["trades"]}
            t5 = {(t["date"], t["code"]) for t in r5["res"]["trades"]}
            if t3 and t3 == t5:
                degen.append(f"{risk:.1f}%×5仓≡×3仓（{len(t3)} 笔）")
    if degen:
        out.append("")
        out.append(f"- **资金约束退化提示**：{'、'.join(degen)} 成交集完全一致——"
                   "风险%放大 → 每股风险上限抬高，C23 池右移（成交均价 14~18 元）后"
                   "5600 元资金实际最多同时持 3 只，5 仓配置退化为 3 仓行为（5 仓未真正生效），"
                   "该档 5 仓数据不等同于有效 5 仓实验。")
    out.append("")
    out.append("> 对照口径：V1 最优组数据取自 2026-08-06 T-023 报告（同 5600 元 / 整手 / "
               "signals.csv / prebreak S 级 / 20d 口径）；C23 组仅信号集多一层 C23 过滤，"
               "模拟核心与口径完全一致，可直接并排对照。")
    return out


def main() -> int:
    today = _dt.datetime.now().astimezone().date().strftime("%Y%m%d")
    ap = argparse.ArgumentParser(description="资金配置参数网格实验（T-023）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金（默认 5600）")
    ap.add_argument("--start", default=DEFAULT_START, help="起始 YYYYMMDD（信号层裁剪，冒烟用）")
    ap.add_argument("--end", default=DEFAULT_END, help="结束 YYYYMMDD（信号层裁剪，冒烟用）")
    ap.add_argument("--risk-list", default="1.5,2,3,5", help="单笔风险%% 列表（逗号分隔）")
    ap.add_argument("--pos-list", default="2,3,5", help="持仓数列表（逗号分隔）")
    ap.add_argument("--smoke", type=int, default=0, help="冒烟：随机 N 只股票快速验证")
    ap.add_argument("--seed", type=int, default=42, help="冒烟抽样种子")
    ap.add_argument("--out", default=str(OUT_DIR / f"网格实验-资金配置-{today}.md"),
                    help="报告输出路径")
    ap.add_argument("--grades", nargs="+", default=DEFAULT_GRADES,
                    help="评级（默认 S，与 sim_capital 验收一致）")
    ap.add_argument("--c23", action="store_true",
                    help="C23 版：先按 C23 过滤（动量≤10%% + 止损距离 0.5~3 元，"
                         "sim_capital.c23_mask 同式同源，enrich 复算 mom20）再跑 12 组")
    args = ap.parse_args()

    df = load_signals(Path(args.signals), args.start, args.end, args.smoke, args.seed)
    if args.c23:
        # C23 过滤在触发信号集上完成（与 c23_capital_compare 同式：触发集 → enrich → c23_mask），
        # 过滤后 df 直接进 12 组循环；pool_features 差集基于过滤后信号集，与 simulate_capital 同口径
        df = df[df["triggered_20d"] == 1].copy()
        n_before = len(df)
        df = enrich(df)
        df = df[c23_mask(df)]
        print(f"[C23 过滤] 触发信号 {n_before} → {len(df)} 笔（动量≤10% + 止损0.5~3元，"
              f"留存 {len(df) / n_before:.1%}）")
    risks = [float(x) for x in args.risk_list.split(",")]
    poss = [int(x) for x in args.pos_list.split(",")]
    print(f"[资金网格] {'C23版 ' if args.c23 else 'T-023 '}| 信号 {len(df)} 行 | 区间 {args.start}~{args.end}"
          f"{'（冒烟 ' + str(args.smoke) + ' 只）' if args.smoke else ''} | "
          f"资金 {args.capital:,.0f} 元 | 组 {len(risks) * len(poss)} 个"
          f"（风险 {risks}% × 持仓 {poss}）")

    rows: list[dict] = []
    for risk in risks:
        for pos in poss:
            label = f"{risk:.1f}%×{pos}仓"
            print(f"  [组 {label}] risk={risk}% max_positions={pos} ...")
            res, pool = run_group(df, args.capital, risk, pos, grades=args.grades)
            rows.append({"label": label, "risk_pct": risk, "max_positions": pos,
                         "res": res, "pool": pool})
            print(f"    → 终值 {res['end_balance']:,.0f} 元（{res['total_ret']:+.1f}%）"
                  f" | {res['n_exec']} 笔 | 回撤 {res['max_dd_pct']:.1f}%")

    report = render_report(rows, args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
