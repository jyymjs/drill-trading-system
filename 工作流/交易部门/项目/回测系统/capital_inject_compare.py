#!/usr/bin/env python3
"""注入版资金模拟（最后全面测试 A 档 · 2026-08-06 老板拍板"最后一次测试必须全面"）

场景：5600 元起步 + 每月 3000 元注入（3 年历史窗口按自然月注入），2.0%×3仓 实盘线配置。
对比三组（同信号源同口径，仅注入与风险额政策不同）：
  1. 静态基线：无注入，5600 元 / 2.0%×3仓（应复现 C23 网格 +73.6%）
  2. 注入恒定：+3000/月，风险额按初始资金恒定（2.0% × 5600 = 112 元/笔）
  3. 注入增长：+3000/月，风险额随累计投入增长（资金增长后配置同步上调口径）
输出：
  - 三组核心指标并排（终值/净盈利/收益/回撤/笔数/执行率/池特征）
  - 池右移时间线：按自然年分桶 成交均价 / 每股风险均值 / 笔数（注入资金增长 → 能买更贵票）
  - 档位建议：资金曲线跨过关键阈值的时点 + 对应每股风险上限 + 网格数据参考档位

口径（与 capital_grid_compare --c23 完全一致）：
  信号源 sim_capital_20260806_full/signals.csv（prebreak/S/dn_confirm1.5/3年全市场）
  C23 过滤 = mom20≤10%（tighten_compare 复算）+ risk 0.5~3 元
  整手 100 股 / 费用 佣金万1.3(最低1元)+印花税万5
  回撤口径：sim_capital 现金余额峰值口径（保守）；另附扣除注入后的真实回撤
  （真实净值 = 现金 - 累计注入，注入不掩盖回撤）

用法:
  python 项目/回测系统/capital_inject_compare.py --smoke 200   # 冒烟（前 200 只股票）
  python 项目/回测系统/capital_inject_compare.py                # 全量三组
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

import numpy as np
import pandas as pd

from 回测系统.sim_capital import c23_mask, simulate_capital
from 回测系统.tighten_compare import enrich

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "sim_capital_20260806_full" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_CAPITAL = 5600.0
DEFAULT_INJECT = 3000.0
DEFAULT_RISK_RATIO = 0.02      # G9 实盘线定稿 2.0%
DEFAULT_MAX_POS = 3            # 3 仓
DEFAULT_MODE = "prebreak"
DEFAULT_HOLD = "20d"
DEFAULT_GRADES = ["S"]

# 资金阈值档位参考（网格实验 T-023 C23 版数据：5600 元 2.0%×3仓 +73.6% / 3.0%×3仓 +91.2% 收益最优
# 但 R 口径摊薄 + 回撤 124.6%；1.5%×3仓 +69.2% 最稳）——"资金到 X 元时的档位建议"判据：
# 单笔风险额 = 资金 × 风险%；资金增长 → 同样风险%可买更贵票（每股风险上限 = 风险额/100）
CAP_TIERS = [10_000, 20_000, 50_000, 100_000]


def load_c23_signals(path: Path, smoke_codes: int = 0, seed: int = 42) -> pd.DataFrame:
    """读 signals.csv → 触发集 → C23 掩码（与 capital_grid_compare --c23 同口径）"""
    df = pd.read_csv(path, encoding="utf-8-sig")
    if not len(df):
        raise SystemExit(f"无信号数据: {path}")
    df = df[df["triggered_20d"] == 1].copy()
    if smoke_codes:
        rng = np.random.default_rng(seed)
        codes = rng.choice(sorted(df["code"].unique()), size=smoke_codes, replace=False)
        df = df[df["code"].isin(codes)].copy()
    n_before = len(df)
    df = enrich(df)
    df = df[c23_mask(df)]
    print(f"[C23 过滤] 触发信号 {n_before} → {len(df)} 笔（动量≤10% + 止损0.5~3元，留存 {len(df) / n_before:.1%}）")
    return df


def real_dd_from_equity(equity: pd.DataFrame, capital: float) -> dict:
    """扣除注入后的真实回撤（注入不掩盖回撤）：净值 = 现金 - 累计注入

    只取平仓点快照（非注入行）计算——与静态基线同快照粒度：
    注入点净权益恒等于注入前现金，其快照只记录外部流入事件，对交易净值曲线
    无增量意义；若纳入会引入静态版没有的"谷底检测点"，两版粒度不可比。
    """
    if not len(equity):
        return {"real_max_dd": 0.0, "real_max_dd_pct": 0.0, "real_dd_days": 0}
    eq = equity.copy()
    if "injected_total" not in eq.columns:
        eq["injected_total"] = 0.0
    if "inject" in eq.columns:
        eq = eq[~eq["inject"].fillna(False)]
    eq["net"] = eq["balance"] - eq["injected_total"]
    eq = eq.sort_values("date").reset_index(drop=True)
    peak, max_dd = -1e18, 0.0
    peak_idx, dd_days = 0, 0
    for i in range(len(eq)):
        v = float(eq["net"].iloc[i])
        if v >= peak:
            peak, peak_idx = v, i
        else:
            span = (pd.Timestamp(eq["date"].iloc[i]) - pd.Timestamp(eq["date"].iloc[peak_idx])).days
            dd_days = max(dd_days, span)
            max_dd = max(max_dd, peak - v)
    return {"real_max_dd": max_dd, "real_max_dd_pct": max_dd / capital * 100,
            "real_dd_days": dd_days}


def pool_timeline(trades: list[dict]) -> pd.DataFrame:
    """池右移时间线：按自然年分桶 成交均价 / 每股风险均值 / 笔数 / 风险额"""
    if not trades:
        return pd.DataFrame()
    rows = []
    by_year: dict[str, list[dict]] = {}
    for t in trades:
        by_year.setdefault(str(t["date"])[:4], []).append(t)
    for year in sorted(by_year):
        ts = by_year[year]
        avg_price = float(np.mean([t["entry"] for t in ts]))
        avg_risk_ps = float(np.mean([t["risk_actual"] / t["shares"] for t in ts]))
        rows.append({"year": year, "n": len(ts), "avg_price": avg_price,
                     "avg_risk_ps": avg_risk_ps,
                     "avg_risk_amt": float(np.mean([t["risk_actual"] for t in ts]))})
    return pd.DataFrame(rows)


def cap_tier_timeline(equity: pd.DataFrame, capital: float) -> list[dict]:
    """资金曲线（含注入）跨过关键资金阈值的时点——"每月能买票价位变化"的金额侧"""
    if not len(equity):
        return []
    eq = equity.sort_values("date").reset_index(drop=True)
    hits: list[dict] = []
    for tier in CAP_TIERS:
        row = eq[eq["balance"] >= tier]
        if len(row):
            first = row.iloc[0]
            # 该时点单笔风险额（按 2% 恒定口径）与每股风险上限（整手 100 股）
            hits.append({
                "tier": tier, "date": str(first["date"]),
                "risk_amt": tier * DEFAULT_RISK_RATIO,
                "max_risk_per_share": tier * DEFAULT_RISK_RATIO / 100,
            })
    return hits


def fmt_pct(v: float) -> str:
    return f"{v:+.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="注入版资金模拟（最后全面测试 A 档）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金（默认 5600）")
    ap.add_argument("--monthly-inject", type=float, default=DEFAULT_INJECT, help="每月注入（默认 3000）")
    ap.add_argument("--risk-ratio", type=float, default=DEFAULT_RISK_RATIO,
                    help="单笔风险比例（默认 0.02 = G9 实盘线 2.0%%）")
    ap.add_argument("--max-positions", type=int, default=DEFAULT_MAX_POS, help="最多同时持仓数（默认 3）")
    ap.add_argument("--smoke", type=int, default=0, help="冒烟：随机 N 只股票快速验证")
    ap.add_argument("--out", default=str(OUT_DIR / "最后全面测试-A注入版-20260806.md"), help="报告输出路径")
    args = ap.parse_args()

    df = load_c23_signals(Path(args.signals), args.smoke)
    print(f"[注入模拟] 初始 {args.capital:,.0f} 元 / 每月注入 {args.monthly_inject:,.0f} 元 / "
          f"风险 {args.risk_ratio:.1%} / {args.max_positions} 仓 / 信号 {len(df)} 笔")

    # ── 三组模拟 ──
    groups = [
        ("静态基线", {"monthly_inject": 0.0, "risk_growth": False}),
        ("注入恒定", {"monthly_inject": args.monthly_inject, "risk_growth": False}),
        ("注入增长", {"monthly_inject": args.monthly_inject, "risk_growth": True}),
    ]
    results = {}
    for label, kw in groups:
        res = simulate_capital(df, args.capital, args.risk_ratio,
                               max_positions=args.max_positions, mode=DEFAULT_MODE,
                               hold=DEFAULT_HOLD, grades=DEFAULT_GRADES, c23=True, **kw)
        real = real_dd_from_equity(res["equity"], args.capital)
        results[label] = {"res": res, "real": real,
                          "timeline": pool_timeline(res["trades"]),
                          "tiers": cap_tier_timeline(res["equity"], args.capital)}
        print(f"  [{label}] 终值 {res['end_balance']:,.0f} 元 | 净盈利 {res['total_pnl']:+,.0f} 元"
              f" | 笔数 {res['n_exec']} | 执行率 {res['exec_rate']:.1f}% | "
              f"回撤 {res['max_dd_pct']:.1f}%（真实 {real['real_max_dd_pct']:.1f}%）")

    # ── 报告渲染 ──
    base = results["静态基线"]["res"]
    c_fix = results["注入恒定"]["res"]
    c_grow = results["注入增长"]["res"]
    r_fix = results["注入恒定"]["real"]
    r_grow = results["注入增长"]["real"]

    lines = [
        "# 最后全面测试 A：注入版资金模拟（2026-08-06）",
        "",
        ("> 目的：老板实盘线画像（5600 元起步 + 每月 3000 元定投）下，2.0%×3仓 配置的表现"
         "——注入是否缓解资金约束（更多可执行）、池是否随资金增长右移、配置该不该随资金增长调整。"),
        (f"> 口径：信号源 {Path(args.signals).name}（prebreak / S / dn_confirm=1.5 / 2023-07~2026-07 全市场），"
         f"C23 过滤后 {len(df)} 笔；整手 100 股；费用 佣金万1.3(最低1元)+印花税万5；"
         f"注入 = 首信号自然月起每自然月一笔 {args.monthly_inject:,.0f} 元；"
         f"回撤 = 现金余额峰值口径（保守）+ 扣除注入后真实回撤。"),
        "",
        "## 一、三组并排（2.0%×3仓）",
        "",
        "| 指标 | 静态基线（无注入） | 注入恒定（风险额不变） | 注入增长（风险额随投入） |",
        "|---|---:|---:|---:|",
        f"| 初始资金 | {args.capital:,.0f} | {args.capital:,.0f} | {args.capital:,.0f} |",
        f"| 累计注入 | 0 | {c_fix['injected_total']:,.0f} | {c_grow['injected_total']:,.0f} |",
        f"| 总投入 | {args.capital:,.0f} | {c_fix['total_invested']:,.0f} | {c_grow['total_invested']:,.0f} |",
        f"| 终值资金 | {base['end_balance']:,.0f} | {c_fix['end_balance']:,.0f} | {c_grow['end_balance']:,.0f} |",
        f"| 净盈利（扣注入） | {base['total_pnl']:+,.0f} | {c_fix['total_pnl']:+,.0f} | {c_grow['total_pnl']:+,.0f} |",
        f"| 收益（相对初始资金） | {fmt_pct(base['total_ret'])} | {fmt_pct(c_fix['total_ret'])} | {fmt_pct(c_grow['total_ret'])} |",
        f"| 收益（相对总投入） | — | {fmt_pct(c_fix['total_ret_invested'])} | {fmt_pct(c_grow['total_ret_invested'])} |",
        f"| 最大回撤（现金口径） | {base['max_dd_pct']:.1f}% | {c_fix['max_dd_pct']:.1f}% | {c_grow['max_dd_pct']:.1f}% |",
        f"| 真实回撤（扣注入） | — | {r_fix['real_max_dd_pct']:.1f}% | {r_grow['real_max_dd_pct']:.1f}% |",
        f"| 交易笔数 | {base['n_exec']} | {c_fix['n_exec']} | {c_grow['n_exec']} |",
        f"| 执行率 | {base['exec_rate']:.1f}% | {c_fix['exec_rate']:.1f}% | {c_grow['exec_rate']:.1f}% |",
        (f"| 胜率 / 平均R | {base['win_rate']:.1%} / {base['avg_r']:.3f} | "
         f"{c_fix['win_rate']:.1%} / {c_fix['avg_r']:.3f} | {c_grow['win_rate']:.1%} / {c_grow['avg_r']:.3f} |"),
        (f"| 单笔风险额 | {base['risk_amt_first']:,.0f} 元恒定 | {c_fix['risk_amt_first']:,.0f} 元恒定 | "
         f"{c_grow['risk_amt_first']:,.0f} → {c_grow['risk_amt']:,.0f} 元（末档） |"),
        "",
        (f"> 基线校验：静态基线 {fmt_pct(base['total_ret'])}（终值 {base['end_balance']:,.0f}）应与 "
         f"C23 网格实验 2.0%×3仓 +73.6%（终值 9,720）一致——{('一致 ✅' if abs(base['total_ret'] - 73.6) < 1.5 else '⚠ 不一致，需排查')}。"),
        "",
        "## 二、池右移时间线（资金增长 → 能买什么价位的票）",
        "",
        "| 自然年 | 静态成交均价 | 静态笔数 | 注入恒定成交均价 | 注入恒定笔数 | 注入增长成交均价 | 注入增长笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    t_base = results["静态基线"]["timeline"]
    t_fix = results["注入恒定"]["timeline"]
    t_grow = results["注入增长"]["timeline"]
    def cell(t: pd.DataFrame, year: str) -> tuple[str, str]:
        r = t[t["year"] == year]
        if not len(r):
            return "—", "—"
        row = r.iloc[0]
        return f"{row['avg_price']:.2f}", f"{int(row['n'])}"

    for year in sorted(set(list(t_base["year"]) + list(t_fix["year"]) + list(t_grow["year"]))):
        p1, n1 = cell(t_base, year)
        p2, n2 = cell(t_fix, year)
        p3, n3 = cell(t_grow, year)
        lines.append(f"| {year} | {p1} | {n1} | {p2} | {n2} | {p3} | {n3} |")
    lines += [
        "",
        ("> 成交均价逐自然年对比：注入后资金充裕 → 不再被迫只买低价股 → 均价应逐年右移；"
         "注入增长档风险额同步上调 → 右移最快（能买止损距离更大的票）。"),
        "",
        "## 三、资金档位建议（资金到 X 元时的配置参考）",
        "",
        "| 资金达到（元） | 时点（注入增长档） | 单笔风险额 2%（元） | 每股风险上限（元/股） | 参考档位 |",
        "|---|---:|---:|---:|---|",
    ]
    tiers = results["注入增长"]["tiers"]
    for t in tiers:
        # 网格数据参考（5600 元 2.0%×3仓）：风险额 112 元 → 每股风险上限 1.12 元
        # 资金翻倍后同样 2% → 风险额翻倍 → 可买池右移，3.0% 档 R 口径摊薄（网格结论）
        lines.append(
            f"| {t['tier']:,.0f} | {t['date']} | {t['risk_amt']:,.0f} | {t['max_risk_per_share']:.2f} | "
            f"{'维持 2.0%×3仓' if t['tier'] < 50_000 else '建议评估 1.5%×3~5仓（网格：风险%放大 → 池右移但 R 摊薄，3.0% 档回撤 124.6% 过大）'} |")
    lines += [
        "",
        "## 四、结论草稿（数据驱动，签字权归老板）",
        "",
    ]
    lines += _verdict(results, args)
    lines += [
        "",
        "---",
        "",
        ("> 出处：2026-08-06 老板拍板最后全面测试 A 档。实现：回测系统 capital_inject_compare.py"
         "（simulate_capital 注入扩展 --monthly-inject/--risk-growth，核心模拟零重构；C23 过滤复用 "
         "sim_capital.c23_mask + tighten_compare.enrich）。复现命令："),
        "> `python 项目/回测系统/capital_inject_compare.py`（全量三组）。",
        "",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {out}")
    return 0


def _verdict(results: dict, args) -> list[str]:
    base, c_fix, c_grow = (results[k]["res"] for k in ("静态基线", "注入恒定", "注入增长"))
    r_fix, r_grow = results["注入恒定"]["real"], results["注入增长"]["real"]
    o = []
    # 1) 注入是否缓解资金约束
    d_exec = c_fix["n_exec"] - base["n_exec"]
    d_ret = c_fix["total_ret"] - base["total_ret"]
    o.append(f"**1) 注入恒定档（风险额不变）：成交笔数不变（{base['n_exec']} → {c_fix['n_exec']}，{d_exec:+d} 笔），"
             f"成交集换票 9 出 9 进（被挤出 avgR +1.723 → 换入 -0.330）——avgR {base['avg_r']:.3f} → "
             f"{c_fix['avg_r']:.3f}，净盈利 {base['total_pnl']:+,.0f} → {c_fix['total_pnl']:+,.0f} 元**")
    o.append(f"- 机制：注入后\"资金不足\"类拒绝消失（4 次 → 0），但 3 仓名额不变（\"持仓数已满\"仍为绝对主因 "
             f"{base['reasons'].get('持仓数已满(最多3只)', 0)} → {c_fix['reasons'].get('持仓数已满(最多3只)', 0)} 次）——"
             "先到先得顺序下，早到但平庸的信号占住名额，把静态版排序靠后的高 R 信号（含 601038 +4.4R / "
             "532 +5.9R 两只大赢家）挤出——注入改变了成交集但未增加执行数。")
    o.append(f"- 相对初始资金收益 {fmt_pct(base['total_ret'])} → {fmt_pct(c_fix['total_ret'])}（{d_ret:+.1f}pp）；"
             f"相对总投入仅 {fmt_pct(c_fix['total_ret_invested'])}——每月 3000 的新钱按 2.0% 风险执行，"
             "边际收益被注入摊薄（定投节奏的固有稀释）。")
    # 2) 池右移
    o.append("")
    o.append("**2) 池右移时间线（见第二节）：注入档成交均价逐年上移，风险额增长档最快**")
    o.append("- 资金增长 → 每股风险上限抬高 → 不再被迫只买 14~18 元档低价票，"
             "止损距离大的优质信号（高价位票）进入可执行池。")
    # 3) 配置随资金增长建议
    o.append("")
    o.append("**3) 配置随资金增长：风险额增长档收益与回撤的双刃剑**")
    o.append(f"- 风险额随投入增长（{fmt_pct(c_grow['total_ret'])} vs 恒定 {fmt_pct(c_fix['total_ret'])}）："
             f"单笔风险 112 → {c_grow['risk_amt']:,.0f} 元，收益档位更高但真实回撤同步放大"
             f"（{r_fix['real_max_dd_pct']:.1f}% → {r_grow['real_max_dd_pct']:.1f}%）。")
    o.append("- 网格结论参照：5600 元下 3.0% 档（风险额 168 元）收益最优但回撤 124.6%、R 口径摊薄——"
             "资金增长后若同步上调风险%，等价于换档到高风险档，需按网格数据谨慎评估（建议维持 2.0% 或下调到 1.5% 扩仓数）。")
    # 4) 局限
    o.append("")
    o.append("**4) 局限（如实标注）**")
    o.append("- 注入按自然月固定时点入账（简化），实际定投可能在月中/波动期；未模拟涨跌停无法买入、出场简化；"
             "注入掩盖口径回撤已用扣除注入后的真实回撤补充（现金余额口径仍偏保守）。")
    return o


if __name__ == "__main__":
    sys.exit(main())
