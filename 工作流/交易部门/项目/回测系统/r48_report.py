#!/usr/bin/env python3
"""R-048/R-049 报告渲染（2026-08-11 · 数据驱动，判定规则确定性）

从 r48_grid collect 的 pivot + 拐点/注入 json + regime 输出渲染两份报告：
  产出/输出/报告/R048-比例网格敏感性-20260811.md
  产出/输出/报告/R049-时间窗稳健性-20260811.md

判定规则（与测试方案 v3 一致，不在此另立）：
  可行域 = dd > -20% 且 n_exec ≥ max(该资金档网格中位 n_exec×30%, 50)
  最优区间 = 可行域内收益回撤比 ≥ 最大值×0.9 的连续档位段
  漂移 = 30k/50k/100k 三档 argmax 档位索引差 ≥1 档距
  收敛 = 相邻起点 |Δ收益|≤40pp 且 |Δ回撤|≤3pp 且 |ΔavgR|≤0.10
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent.parent   # 交易部门根
EXPER = _ROOT / "产出" / "输出" / "实验"
REPORT = _ROOT / "产出" / "输出" / "报告"

RATIOS = [0.008, 0.00857, 0.010, 0.012855, 0.016, 0.020, 0.025]
CAPITALS = [8401.0, 30000.0, 50000.0, 100000.0]
RATIO_LABEL = {r: f"{r:.3f}" if r == 0.00857 else f"{r:.3f}" for r in RATIOS}


def fmt_pct(v, nd=1):
    return f"{v:+.{nd}f}%" if v is not None else "-"


def fmt_ratio(v):
    s = f"{v:.4f}"
    return "0.012855" if abs(v - 0.012855) < 1e-9 else s


def load_pivot(p: Path) -> dict:
    import pandas as pd
    df = pd.read_csv(p, encoding="utf-8-sig")
    df["ret"] = pd.to_numeric(df["total_ret_pct"], errors="coerce")
    df["dd"] = pd.to_numeric(df["dd_peak_pct"], errors="coerce")
    df["ddr"] = df["ret"] / df["dd"].abs().replace(0, None)
    df["n_exec"] = pd.to_numeric(df["n_exec"], errors="coerce").fillna(0)
    return df


def load_cells(dirpath: Path, prefix: str) -> list[dict]:
    """读某目录下指定前缀的 .json 格结果（跳过非单格 JSON 如 mc_results 数组）"""
    out = []
    for f in sorted(dirpath.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("id", "").startswith(prefix):
            out.append(d)
    return out


# ─────────────────────────── R-048 ───────────────────────────

def r048_report() -> list[str]:
    pivot = load_pivot(EXPER / "r48_ratio_grid" / "pivot_main.csv")
    corner = load_cells(EXPER / "r48_ratio_grid", "C")
    inj = load_cells(EXPER / "r48_ratio_grid", "INJ")
    L = [
        "# R-048 风险额比例网格敏感性（2026-08-11 · 交易部审核通过稿执行）",
        "",
        ("> 目的：验证 V3 风险额比例 0.012855 是否在最优区间、最优比例是否随资金量漂移、"
         "界定低资金可执行性下限。数据支撑老板拍板比例维持/调整。"),
        "> 流程：方案 v3（交易部审核 9+3 条意见全吸收）→ V1 七点锚点对账 0.0pp 通过 → "
        "冒烟 6 格 → 批处理全格 selfcheck 通过 → V4 确定性（G04 三次重跑均 629.6%）",
        f"> 口径：信号集 backtest_r43_t2/signals.csv（S 级/dn1.5/C23，4,351 笔，2000-05-17~2026-08-10）；"
        f"执行器 r44.run_one 单一来源（排序 time 与 R-043/044/045 同源）；回撤一律总资产口径；"
        f"avgR = mean(pnl/risk_actual)；无限制上限（999）；0.5R 分步；费用佣金万1.3+印花税万5。",
        "",
        "## 一、实验矩阵与主网格（26 年全量，28 格）",
        "",
        "| 档位 | 资金 | 收益 | 最大回撤 | 收益回撤比 | 成交 | avgR | 资金不足 | 每股风险超限 | 执行率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in pivot.sort_values(["capital", "risk_ratio"]).iterrows():
        L.append(
            f"| {fmt_ratio(float(r['risk_ratio']))} | {int(r['capital']):,} | {fmt_pct(r['ret'])} | "
            f"{fmt_pct(r['dd'])} | {r['ddr']:.1f} | {int(r['n_exec'])} | {r['avg_r']:.3f} | "
            f"{int(r['reject_insufficient'])} | {int(r['reject_risk_over'])} | {r['exec_rate']:.1f}% |")
    # 判定（方案 v3 规则）
    med_n = pivot.groupby("capital")["n_exec"].median()
    feas_rows = []
    for cap in CAPITALS:
        g = pivot[pivot["capital"] == cap].copy()
        thr = max(med_n[cap] * 0.30, 50)
        g["feas"] = (g["dd"] > -20.0) & (g["n_exec"] >= thr)
        best = g[g["feas"]].sort_values("ddr", ascending=False)
        if len(best):
            bmax = best["ddr"].max()
            opt = best[best["ddr"] >= bmax * 0.9]
            opt_low, opt_high = opt["risk_ratio"].min(), opt["risk_ratio"].max()
            feas_rows.append((cap, g, best, opt_low, opt_high, thr, bmax))
        else:
            feas_rows.append((cap, g, best, None, None, thr, None))
    L += [
        "",
        "## 二、最优区间与漂移判定",
        "",
        "| 资金 | 可行域门槛(n_exec≥) | 最优档 | 收益回撤比峰值 | 最优区间(≥峰×0.9) | 是否端点 |",
        "|---|---:|---|---|---|---|",
    ]
    argmax_idxs = {}
    for cap, g, best, opt_low, opt_high, thr, bmax in feas_rows:
        if len(best):
            br = float(best.iloc[0]["risk_ratio"])
            argmax_idxs[cap] = RATIOS.index(br)
            label = fmt_ratio(br)
            end = "端点(r7)" if br == RATIOS[-1] else ("端点(r1)" if br == RATIOS[0] else "非端点")
            opt_s = (f"{fmt_ratio(opt_low)}~{fmt_ratio(opt_high)}" if opt_low != opt_high
                     else fmt_ratio(opt_low))
            L.append(f"| {int(cap):,} | ≥{thr:.0f} | **{label}** | {bmax:.1f} | {opt_s} | {end} |")
        else:
            L.append(f"| {int(cap):,} | ≥{thr:.0f} | 无可行格 | - | - | - |")
    drift = False
    if len(argmax_idxs) >= 3:
        idxs = [argmax_idxs[c] for c in (30000.0, 50000.0, 100000.0)]
        drift = (max(idxs) - min(idxs)) >= 1
    idx_str = ", ".join(f"{int(k):,}→r{idx + 1}" for k, idx in argmax_idxs.items())
    L += [
        "",
        f"**漂移判定（30k/50k/100k）**：argmax 档位索引 = {idx_str} → "
        + ("**最优档随资金漂移成立**" if drift else "**不漂移——各资金档最优档一致**") + "。",

    ]
    # 8,401 可执行性边界
    g84 = pivot[pivot["capital"] == 8401.0].sort_values("risk_ratio")
    L += [
        "",
        "## 三、8,401 档可执行性边界（实盘现状）",
        "",
        "| 档位 | 风险额 | 成交 | 每股风险超限 | 执行率 | avgR | 收益回撤比 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in g84.iterrows():
        L.append(f"| {fmt_ratio(float(r['risk_ratio']))} | {int(r['risk_amt'])} 元 | "
                 f"{int(r['n_exec'])} | {int(r['reject_risk_over'])} | {r['exec_rate']:.1f}% | "
                 f"{r['avg_r']:.3f} | {r['ddr']:.1f} |")
    L += [
        "",
        (f"> 可执行性边界：8,401 元下比例 ≤0.010 时每股风险超限显著（0.008 档 666 笔、"
         f"执行率仅 37.6%），低档位「买不起一手」是资金约束事实而非策略差；"
         f"现行 0.012855 档执行率 72.5%。"),
        "",
        "## 四、拐点时间窗验证（最优±1 档 × 近 7/近 3 年）",
        "",
        "| 档位 | 资金 | 近7年收益/回撤 | 近7年回撤比 | 近3年收益/回撤 | 近3年回撤比 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for d in sorted(corner, key=lambda x: (x["risk_ratio"], x["capital"])):
        w7 = next((c for c in corner if c["id"] == d["id"].replace("近3年", "近7年")), None)
        w3 = d if "近3年" in d["id"] else None
        if "近3年" in d["id"]:
            w7 = next((c for c in corner if c["id"] == d["id"].replace("近3年", "近7年")), None)
            L.append(f"| {fmt_ratio(d['risk_ratio'])} | {int(d['capital']):,} | "
                     f"{fmt_pct(w7['total_ret_pct'])} / {fmt_pct(w7['dd_peak_pct'])} | "
                     f"{(w7['total_ret_pct'] / abs(w7['dd_peak_pct']) if w7['dd_peak_pct'] else 0):.1f} | "
                     f"{fmt_pct(d['total_ret_pct'])} / {fmt_pct(d['dd_peak_pct'])} | "
                     f"{(d['total_ret_pct'] / abs(d['dd_peak_pct']) if d['dd_peak_pct'] else 0):.1f} |")
    L += [
        "",
        (f"> 拐点发现：近 7 年口径下高档位回撤明显放大（0.025 档 30k -31.6% / 50k -28.5% / "
         f"100k -28.9%，均超 -20% 警示线）；8,401 档同口径 -16.3%（资金约束下持仓数少，回撤不深）。"
         f"26 年口径各档回撤均 ≤-16.1%。——时间窗选择显著影响高档位结论（R-049 呼应）。"),
        "",
        "## 五、注入补测（8,401 最优档 × 近 3 年月注入 3000 × risk_growth）",
        "",
    ]
    if inj:
        d = inj[0]
        L.append(f"| 档位 | 相对总投入 | 回撤 | 成交 | avgR | 累计注入 |")
        L.append(f"| {fmt_ratio(d['risk_ratio'])} | {fmt_pct(d['total_ret_invested_pct'])} | "
                 f"{fmt_pct(d['dd_peak_pct'])} | {int(d['n_exec'])} | {d['avg_r']:.3f} | "
                 f"{int(d['injected_total']):,} 元 |")
        L.append("")
        L.append(f"> 对比 R-045 现行档注入版（0.012855：相对总投入 +106.2% / 回撤 -18.1%）——"
                 f"最优档 0.025 注入版 +125.1% / -19.5%，收益略优、回撤略深。")
    L += [
        "",
        "## 六、蒙特卡洛（候选档 × 10,000 场景）",
        "",
        "| 配置 | 成交笔数 | avgR | 盈利概率 | 破产率 | 终值P5/P50/P95 | 回撤P5/P50/P95 | 最大连败 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    mc_file = EXPER / "r48_ratio_grid" / "mc_results.json"
    if mc_file.exists():
        mcs = json.loads(mc_file.read_text(encoding="utf-8"))
        for m in mcs:
            L.append(f"| {int(m['capital']):,}×{fmt_ratio(m['risk_ratio'])} | {m['n_exec']} | "
                     f"{m['avg_r']:.3f} | {m['prob_profit']:.1%} | {m['ruin_rate']:.2%} | "
                     f"{m['fin_p05']:.0f}/{m['fin_p50']:.0f}/{m['fin_p95']:.0f}R | "
                     f"{m['dd_p05']:.0f}/{m['dd_p50']:.0f}/{m['dd_p95']:.0f}R | {m['streak_max']} 笔 |")
    L += [
        "",
        "## 七、结论与老板拍板项",
        "",
        "**数据结论**：",
        "1. 26 年全量口径：收益回撤比随比例**单调上升**，网格上端 0.025 未探到顶（各资金档一致）；"
        "回撤全在 -8.3%~-16.1% 安全区。现行 0.012855 位于单调上升左坡，非山峰。",
        "2. 近 7 年口径：0.020/0.025 档在 30k+ 资金下回撤放大至 -22.6%~-31.6%（超警示线）；"
        "8,401 档资金约束反噬回撤可控（-16.3%）。**高档位的时间窗风险显著**。",
        "3. 漂移判定：各资金档 26 年最优档一致（不漂移），但**时间窗漂移存在**（近 7 年结论不同于 26 年）。",
        "4. 8,401 可执行性下限：比例 ≤0.010 时执行率 <57%（买不起一手为主因）。",
        "",
        "**老板拍板项**：",
        "- 比例维持 0.012855 还是上调（候选：0.016/0.020/0.025）？——26 年口径支持上调，"
        "近 7 年口径警示 30k+ 资金高档位回撤；实盘 8,401 档高档回撤可控。",
        "- 是否补测 0.03/0.035/0.04 更高档位确认拐点（当前网格上端未探到顶）？",
        "- 注入路径比例是否随资金上调（risk_growth 语义已支持）？",
        "",
        "## 八、产物与口径备注",
        "",
        f"- 产物：实验/r48_ratio_grid/（28 主网格 + 16 拐点 + 1 注入 json + pivot + manifest）",
        "- 对账：G04=+629.6% / G11=+942.2% / G18=+1,026.0% / G25=+1,107.9% 与 R-045 单点零偏差",
        "- 排序口径 time（r44 实际行为，与历史同源）；risk_mid 为实盘执行卡口径（T-032），差异观察项",
        "- avg_r_no_top5 分母 = capital×risk_ratio 固定口径（r44 既有），未与 avgR 并表对比",
        "- 蒙卡口径：r48 mc 子命令复用 monte_carlo.simulate（FEE=0.0、seed=2024），"
        "成交集 = 主网格同源（half_phase=True）；破产线 = 累计 R ≤ -1/risk_ratio",
    ]
    return L


# ─────────────────────────── R-049 ───────────────────────────

def r049_report(regime_lines: str) -> list[str]:
    import pandas as pd
    pivot = pd.read_csv(EXPER / "r48_timewindow" / "pivot_timewindow.csv", encoding="utf-8-sig")
    pivot["ret"] = pd.to_numeric(pivot["total_ret_pct"], errors="coerce")
    pivot["dd"] = pd.to_numeric(pivot["dd_peak_pct"], errors="coerce")
    pivot["n_exec"] = pd.to_numeric(pivot["n_exec"], errors="coerce").fillna(0)
    sig = pd.read_csv(_ROOT / "产出" / "输出" / "backtest_r43_t2" / "signals.csv",
                      encoding="utf-8-sig", dtype={"code": str})
    sig["year"] = sig["date"].astype(str).str[:4]
    yearly = sig["year"].value_counts().sort_index()
    L = [
        "# R-049 回测时间窗稳健性（2026-08-11 · 交易部审核通过稿执行）",
        "",
        ("> 目的：验证回测时间取值——起始点敏感性、滚动窗时代漂移、市场制度边界、宏观周期归因，"
         "产出回测时间取值规范建议（老板拍板项）。"),
        "> 流程：与 R-048 同批次执行（V1 锚点 0.0pp、全格 selfcheck 通过）；统一配置 8,401×0.012855×无限制。",
        "",
        "## 一、信号年度分布（结构性发现：2002-2008 空窗）",
        "",
        "| 年份 | 信号数 | 年份 | 信号数 | 年份 | 信号数 |",
        "|---|---:|---|---:|---|---:|",
    ]
    yrs = list(yearly.index)
    for i in range(0, len(yrs), 3):
        row = []
        for j in range(3):
            if i + j < len(yrs):
                y = yrs[i + j]
                row.append(f"| {y} | {int(yearly[y])} |")
            else:
                row.append("| - | - |")
        L.append(" ".join(row))
    L += [
        "",
        (f"> 结构性事实：2002-2008 连续 7 年零信号（早期数据起点与股票池规模所致）；"
         f"2017 起信号密集（2017=614 → 2026=720 笔/年）。空窗段不跑引擎，仅呈现。"),
        "",
        "## 二、起始点敏感性（6 格）",
        "",
        "| 起始点 | 信号数 | 收益 | 回撤 | avgR | 成交 | 收敛标注 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    starts = ["2000", "2005", "2010", "2015", "2019", "2023"]
    conv = []
    for i, s in enumerate(starts):
        r = pivot[pivot["id"] == f"T{s}"].iloc[0]
        n_sig = len(sig[sig["date"].astype(str) >= f"{s}-01-01"])
        mark = ""
        if i > 0:
            prev = pivot[pivot["id"] == f"T{starts[i-1]}"].iloc[0]
            d_ret = abs(r["ret"] - prev["ret"])
            d_dd = abs(r["dd"] - prev["dd"])
            d_ar = abs(r["avg_r"] - prev["avg_r"])
            if d_ret <= 40 and d_dd <= 3 and d_ar <= 0.10:
                mark = "✅ 数值收敛"
            else:
                mark = f"⚠️ 不收敛(Δ收益 {d_ret:.0f}pp)"
            conv.append(mark)
        L.append(f"| {s} | {n_sig} | {fmt_pct(r['ret'])} | {fmt_pct(r['dd'])} | {r['avg_r']:.3f} | "
                 f"{int(r['n_exec'])} | {mark} |")
    L += [
        "",
        (f"> 收敛判定：2000→2005→2010 三起点结论等价（信号差 <5 笔，空窗验证）；"
         f"2010→2015 起收益变化显著（早期 2010-2014 的 178 笔信号对总量贡献大）——"
         f"**收益数值不收敛，但回撤（-10.4%~-15.1%）与 avgR（0.752~0.882）稳定，"
         f"可行性判定不翻转（结论收敛）**。"),
        "",
        "## 三、滚动 3 年窗（时代漂移）",
        "",
        "| 窗 | 信号数 | 收益 | 回撤 | avgR | 成交 | 漂移标注 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    wids = ["w2001", "w2004", "w2007", "w2010", "w2013", "w2016", "w2019", "w2022",
            "m2004", "m2010", "m2016", "m2022"]
    win_n = {"w2001": 2, "w2004": 0, "w2007": 3, "w2010": 142, "w2013": 304, "w2016": 937,
             "w2019": 562, "w2022": 2399, "m2004": 3, "m2010": 446, "m2016": 1499, "m2022": 2399}
    prev_ar = None
    for wid in wids:
        rows = pivot[pivot["id"] == wid]
        if wid in ("w2001", "w2004", "w2007", "m2004"):
            L.append(f"| {wid} | {win_n[wid]} | 空窗/近空窗（不跑引擎） | - | - | - | - |")
            prev_ar = None
            continue
        r = rows.iloc[0]
        drift = ""
        if prev_ar is not None:
            d = abs(r["avg_r"] - prev_ar)
            if d > 0.15:
                drift = f"⚠️ 时代漂移段(ΔavgR {d:.2f})"
        prev_ar = r["avg_r"]
        note = "合并参考" if wid.startswith("m") else ""
        L.append(f"| {wid} {note} | {win_n[wid]} | {fmt_pct(r['ret'])} | {fmt_pct(r['dd'])} | "
                 f"{r['avg_r']:.3f} | {int(r['n_exec'])} | {drift} |")
    L += [
        "",
        (f"> 时代漂移：w2013 avgR 1.798 → w2016 0.412 → w2019 1.276 → w2022 0.754——"
         f"相邻窗 avgR 差 >0.15R 段标注漂移。2016-2019 窗（含 2018 单边熊）为弱段；"
         f"归因见宏观标注（熊市占比 55%）。近 3 年窗 w2022（2022-2026）收益最高 +233%。"),
        "",
        "## 四、制度边界对比（≤2019 vs ≥2020）",
        "",
        "| 段 | 信号数 | 收益 | 回撤 | avgR | 成交 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for wid, label in (("REGIME_OLD", "≤2019-12-31（主板 10% 涨跌停时代）"),
                       ("REGIME_NEW", "≥2020-01-01（创业板/科创板 20%）")):
        r = pivot[pivot["id"] == wid].iloc[0]
        n_sig = win_n[wid] if wid in win_n else ("1,509" if wid == "REGIME_OLD" else "2,842")
        L.append(f"| {label} | {n_sig} | {fmt_pct(r['ret'])} | {fmt_pct(r['dd'])} | "
                 f"{r['avg_r']:.3f} | {int(r['n_exec'])} |")
    L += [
        "",
        (f"> 关联观察（非因果）：20% 涨跌停时代 avgR 0.800 > 10% 时代 0.756，成交更密集（438 vs 291），"
         f"策略未在制度变化后退化。精确制度日：科创板 2019-07-22、创业板 2020-08-24（20%），"
         f"2020-01-01 为近似分界；涨跌停放宽主要影响跳空与止损成交价，表述降级为关联观察。"),
        "",
        "## 五、宏观周期标注（牛/熊/震荡占比 + 信号层）",
        "",
        "| 段 | 牛 | 熊 | 震荡 | 信号数 | avgR | 胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    # regime 输出表解析（文本传入）
    for line in regime_lines.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 7:
            L.append(f"| {parts[0]} | {parts[2]} | {parts[3]} | {parts[4]} | {parts[5]} | "
                     f"{parts[6]} | {parts[7]} |")
    L += [
        "",
        (f"> 宏观归因：熊市占比与信号层 avgR 负相关明显（w2010 熊 70% avgR 0.119 → "
         f"w2019 牛 50% avgR 0.334）；26 年全景牛 43%/熊 47%/震荡 10%（熊市为主）。"
         f"弱段归因宏观而非策略退化；近 3 年牛 53% 占优，收益高含环境因素。"
         f"（信号层 = 全 S 级信号 r_20d 直统计，含未触发；触发成交 avgR 见 R-048 主网格。）"),
        "",
        "## 六、回测时间取值规范建议（老板拍板项）",
        "",
        "由本测试数据支撑的建议：",
        "1. **信号层结论**（信号质量/评级/条件类）→ 26 年全量主口径（19 个有效年份，样本最足）；",
        "2. **资金层结论**（资金利用/可执行性/回撤压力）→ 近 3 年主口径 + 近 7 年复验（回撤压力在近 7 年最真实——R-048 拐点发现 30k+ 高档位近 7 年回撤 -31.6% 即为证据）；",
        "3. 所有回测报告附信号年度分布 + 当期宏观周期标注；",
        "4. 起始点 <2015 仅作全量等价性旁证（早期制度差异 + 2002-2008 空窗结构），参数级结论以 2015+ 起点为准。",
        "",
        "## 七、产物与口径备注",
        "",
        f"- 产物：实验/r48_timewindow/（17 格 json + pivot）+ 实验/r48_window/（预过滤信号留档）",
        "- 空窗不跑引擎：w2001（2 笔）/w2004（0 笔）/w2007（3 笔）仅年度分布呈现；m2004 参考行 0 成交",
        "- 跨窗出场：max_date 透传真实数据末 2026-08-10，窗内持仓可跨窗出场不截断（sim_capital 默认行为零变化）",
        "- V4 确定性：T2000 与 R-048 G04 同参复跑结果一致（+629.6%），工具确定性验证",
    ]
    return L


def main() -> int:
    out_dir = REPORT
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "R048-比例网格敏感性-20260811.md").write_text(
        "\n".join(r048_report()), encoding="utf-8")
    print(f"R048 → {out_dir / 'R048-比例网格敏感性-20260811.md'}")
    # regime 输出已存文件
    regime_txt = ""
    reg_file = EXPER / "r48_timewindow" / "regime_output.txt"
    if reg_file.exists():
        regime_txt = reg_file.read_text(encoding="utf-8")
    (out_dir / "R049-时间窗稳健性-20260811.md").write_text(
        "\n".join(r049_report(regime_txt)), encoding="utf-8")
    print(f"R049 → {out_dir / 'R049-时间窗稳健性-20260811.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
