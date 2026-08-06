#!/usr/bin/env python3
"""排序定案后测试：基线(time) vs 新基线(risk_mid) 分布诊断（2026-08-06 老板拍板）

输出 = 蒙特卡洛双件套标准（复刻级文本版式 + 净值曲线图版式）：
  - 两组资金层（生产链路：half_phase 0.5R 分步 + delay2 确认）：
      基线 time（113 笔，对照实验 +88.8% 复现校验）
      新基线 risk_mid（118 笔，+107.3%——T-032 排序实验定案标准）
  - 每组：10000 次蒙卡（seed=2024）→ 复刻级报告（标准 5 板块 + 扩展 7 板块）
    + 净值曲线图（黑底路径堆叠）
  - 新旧对照表（收益/avgR/盈利概率/去尾判定——**依赖大赢家是否改善**为关键结论）

口径：
  - 信号源 backtest_final_20260806/signals.csv（514 笔触发）+ delay2 出场重算
    （confirm_replay 同链路）+ K 线 duckdb 只读
  - 成交 R = pnl / risk_actual（含费，monte_carlo_c23.capital_trade_r 同口径）
  - 金额换算 = 平均单笔风险额（risk_actual 均值）；回撤 = R×单笔风险（总资产口径，铁律）
  - 破产线 = 初始 5600 × 25% = 1400 元

用法:
  python 项目/回测系统/sort_dist_compare.py --smoke 60   # 自检（前 60 笔触发）
  python 项目/回测系统/sort_dist_compare.py              # 全量（2 组 × 10000 次）
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from 分析决策.跟踪.monte_carlo import simulate
from 回测系统.confirm_replay import load_kline_cache, make_confirm_fn, rebuild_exit_for_mode
from 回测系统.monte_carlo_c23 import capital_trade_r, summary
from 回测系统.monte_carlo_chart import plot_equity_paths
from 回测系统.monte_carlo_style import render_scenario_report
from 回测系统.regime_segment_compare import attach_regime
from 回测系统.sim_capital import simulate_capital

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "backtest_final_20260806" / "signals.csv"
CAPITAL = 5600.0
RISK_RATIO = 0.02
MAX_POS = 3
N_SIM = 10000
REGIME_LABEL = {"bull": "牛市", "bear": "熊市", "sideways": "震荡"}


def run_mode(sig: pd.DataFrame, klines: dict, order: str, smoke: int | None
             ) -> dict:
    """单模式：生产链路资金模拟 → 成交 R → 蒙卡 → 文本/图表双件套"""
    sim = simulate_capital(sig, CAPITAL, RISK_RATIO, max_positions=MAX_POS,
                           mode="prebreak", hold="20d", grades=["S"],
                           half_phase=True, confirm_fn=make_confirm_fn("delay2"),
                           same_day_order=order)
    trades = sim["trades"]
    rs = capital_trade_r(trades)
    avg_risk = float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0
    print(f"[{order}] 成交 {len(rs)} 笔 | 收益 {sim['total_ret']:+.1f}% | "
          f"avgR {float(np.mean(rs)):+.3f} | 单笔风险均值 {avg_risk:.2f} 元")
    mc = simulate([{"r_multiple": r} for r in rs], n_simulations=N_SIM,
                  fee_per_trade_r=0.0)   # R 已含费，不重复扣
    return {"sim": sim, "rs": rs, "mc": mc, "avg_risk": avg_risk, "trades": trades}


def segment_mc(trades: list[dict], sig: pd.DataFrame) -> dict | None:
    """资金层成交 R 按信号日市场状态分段蒙卡（扩展板块 MARKET REGIME）"""
    try:
        df = attach_regime(sig.copy())
        reg_by_date = dict(zip(df["date"].astype(str).str[:10], df["regime"]))
    except Exception:  # noqa: BLE001
        return None
    from 分析决策.跟踪.monte_carlo import simulate as _sim
    out = {}
    for regime, label in REGIME_LABEL.items():
        rs = [float(t["pnl"]) / float(t["risk_actual"])
              for t in trades if reg_by_date.get(str(t["date"])[:10]) == regime
              and float(t["risk_actual"]) > 0]
        if len(rs) < 5:
            continue
        mc = _sim([{"r_multiple": r} for r in rs], n_simulations=N_SIM,
                  fee_per_trade_r=0.0)
        fin = mc["final_equities"]
        out[regime] = {"label": label, "prob": float(np.mean(fin > 0)) * 100,
                       "median": float(np.median(fin)),
                       "worst5": float(np.percentile(fin, 5))}
    return out or None


def render_compare(res: dict, smoke: int | None) -> str:
    b, n = res["time"], res["risk_mid"]
    out = [
        "# 排序定案测试：基线 vs 新基线（蒙特卡洛双件套标准 · 2026-08-06 老板拍板）",
        "",
        f"> 场景：当日多个可买入候选时选谁——T-032 排序实验定案标准 = **每股风险居中**"
        "（|每股风险−1.5| 升序，+107.3% vs 时间先到先得 +88.8%）。",
        f"> 信号源：backtest_final_20260806/signals.csv（{'自检前 ' + str(smoke) if smoke else '514 笔触发'}"
        " · prebreak/20d · S 级）｜生产链路：half_phase 0.5R 分步 + delay2 确认",
        f"> 模拟：每组 {N_SIM:,} 次有放回重抽样（seed=2024）；成交 R = pnl/risk_actual（含费）；"
        f"金额换算 = 平均单笔风险额；破产线 1,400 元（初始 25%）。",
        f"> ⚠️ **样本量如实标注**：资金层成交 基线 {len(b['rs'])} 笔 / 新基线 {len(n['rs'])} 笔 / "
        "3 年——低于严肃测试标准（1000+ 笔/8-10 年），中期验证级；虚拟盘线双轨持续积累。",
        f"> 输出 = 双件套标准（复刻级文本版式 + 净值曲线图版式，记忆 monte-carlo-report-style.md）；"
        "回撤一律总资产口径（铁律）。",
        "",
        "## 一、新旧对照（关键结论：依赖大赢家是否改善）",
        "",
        "| 指标 | 基线（time） | 新基线（risk_mid） | 变化 |",
        "|---|---:|---:|---|",
        f"| 总收益 | {b['sim']['total_ret']:+.1f}% | {n['sim']['total_ret']:+.1f}% | "
        f"{n['sim']['total_ret'] - b['sim']['total_ret']:+.1f}pp |",
        f"| 成交笔数 | {len(b['rs'])} | {len(n['rs'])} | {len(n['rs']) - len(b['rs'])} |",
        f"| 成交 avgR | {float(np.mean(b['rs'])):+.3f} | {float(np.mean(n['rs'])):+.3f} | "
        f"{float(np.mean(n['rs'])) - float(np.mean(b['rs'])):+.3f} |",
        f"| 盈利概率(≥0R) | {b['mc']['prob_profit']:.1%} | {n['mc']['prob_profit']:.1%} | |",
        f"| 单笔风险均值 | {b['avg_risk']:.2f} 元 | {n['avg_risk']:.2f} 元 | |",
        "",
        "### 去尾稳定性对照（依赖大赢家判定）",
        "",
        "| 去掉最大收益 | 基线 avgR | 基线判定 | 新基线 avgR | 新基线判定 |",
        "|---|---:|---|---:|",
    ]
    from 回测系统.monte_carlo_dist import tail_stability
    tb = tail_stability(b["rs"])
    tn = tail_stability(n["rs"])
    for row_b, row_n in zip(tb, tn):
        lb = "基准" if row_b["pct"] == 0.0 else f"Top {row_b['pct']:.0%}（{row_b['n_trim']}笔）"
        out.append(f"| {lb} | {row_b['avg_r']:+.3f} | {'依赖大赢家' if row_b['crashed'] else '稳定'} | "
                   f"{row_n['avg_r']:+.3f} | {'依赖大赢家' if row_n['crashed'] else '稳定'} |")
    out += [
        "",
        "> 判定口径：去尾后 avgR ≤0 或相对全量下降 ≥50% → 依赖大赢家。",
        "",
        "## 二、复刻级版式报告（标准 5 板块 + 扩展 7 板块）",
        "",
        "### 基线（time · 时间先到先得）",
        "",
        "```",
    ]
    out.append(render_scenario_report(b["mc"], CAPITAL, b["avg_risk"], rs=b["rs"],
                                      regimes=b.get("regimes")))
    out += ["```", "", "### 新基线（risk_mid · 每股风险居中）", "", "```"]
    out.append(render_scenario_report(n["mc"], CAPITAL, n["avg_risk"], rs=n["rs"],
                                      regimes=n.get("regimes")))
    out += [
        "```",
        "",
        "## 三、净值曲线图（黑底路径堆叠，双件套标准②）",
        "",
        "- 基线：`蒙特卡洛-净值曲线-排序定案-基线.png`",
        "- 新基线：`蒙特卡洛-净值曲线-排序定案-新基线.png`",
        "",
        "## 四、结论（数据驱动 · 签字权归老板）",
        "",
        "> 由主对话根据本表综合判定排序标准是否替换；替换后 +107.3% 为新基线（sim_capital "
        "same_day_order='risk_mid' 已参数化，执行卡默认启用）。",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    ap.add_argument("--smoke", type=int, default=None, help="自检：只处理前 N 笔触发")
    ap.add_argument("--out", default=None, help="报告输出路径")
    args = ap.parse_args()

    fin = pd.read_csv(args.signals, encoding="utf-8-sig")
    tr = fin[(fin["mode"] == "prebreak") & (fin["triggered_20d"] == 1)]
    if args.smoke:
        tr = tr.head(args.smoke)
    print(f"触发样本: {len(tr)} 笔")
    print("加载 K 线（只读 duckdb，缓存）…")
    klines = load_kline_cache([str(c) for c in tr["code"].unique()])
    print(f"K 线命中 {len(klines)} 只")
    print("delay2 出场重算…")
    sig, _ = rebuild_exit_for_mode(tr, klines, "delay2", mode="prebreak", hold="20d")

    res = {o: run_mode(sig, klines, o, args.smoke) for o in ("time", "risk_mid")}
    # 分段蒙卡（新基线，扩展板块）
    res["risk_mid"]["regimes"] = segment_mc(res["risk_mid"]["trades"], sig)

    report = render_compare(res, args.smoke)
    out_path = args.out or str(_ROOT / "产出" / "输出" /
                               "蒙特卡洛-复刻版式-排序定案-20260806.md")
    if args.smoke:
        out_path = str(Path(out_path).with_name(Path(out_path).stem + "_smoke" + Path(out_path).suffix))
                               "蒙特卡洛-复刻版式-排序定案-20260806.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"报告 → {out_path}")

    # 净值曲线图（双件套标准②）
    for order, tag in (("time", "基线"), ("risk_mid", "新基线")):
        r = res[order]
        p = plot_equity_paths(r["mc"], CAPITAL, r["avg_risk"],
                              out_path=_ROOT / "产出" / "输出" /
                              f"蒙特卡洛-净值曲线-排序定案-{tag}.png")
        print(f"净值曲线图({tag}) → {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())