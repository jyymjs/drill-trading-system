#!/usr/bin/env python3
"""总资产口径回撤重算（2026-08-06 老板拍板）

背景：sim_capital 回撤按"现金余额峰值"口径计算——买入时现金骤减但持仓市值
不计入，回撤被系统性放大（保守口径）。本脚本重算**总资产口径**（现金 + 持仓
市值，持仓按每日 qfq 收盘价估值）的真实回撤，用于判断 C23 方案回撤可接受度。

口径说明（重点）：
- 旧口径（sim_capital 现状）：资金曲线 = 起点 + 每笔平仓后快照（纯现金余额）；
  回撤 = 现金峰值→谷底最大跌幅（% = 金额/初始资金）+ 回撤时长
  （最近峰值→当前谷底的最长自然日跨度）。
- 新口径（本脚本）：逐日总资产 = 当日现金余额 + Σ(持仓股数 × 当日 qfq 收盘价)。
  - 现金流事件与 simulate_capital 完全一致：入场日扣 entry×股数+费用、出场日
    收 exit_price×股数-费用（费用 calc_trade_fee 同口径）；同一日先平后开。
  - 持仓区间 [入场日, 出场日)：入场日收盘起按市值计入（当日扣款后），出场日
    收盘已按出场价兑现现金、不再计入市值；停牌日持仓用最近可得收盘（前值填充），
    个股无任何行情数据时兜底用入场价。
  - 回撤算法与旧口径完全一致（只换资产定义）：金额 = 历史峰值→谷底最大跌幅；
    主百分比 = 金额/初始资金（与旧口径可比）；另附占峰值（业界标准，参考）；
    时长 = 最近峰值→当前谷底最长自然日跨度。
- 估值价：duckdb qfq 前复权收盘（read_kline，与引擎/回测同口径）。
- 信号与 C23 过滤口径：与 c23_capital_compare 完全一致（mom20 复算同
  tighten_compare：trigger / 触发日前第 20 根 qfq 收盘 - 1）。

sim_capital 核心逻辑零改动（本脚本只复用其 trades 输出做独立重算）。

用法:
  python 项目/回测系统/capital_dd_recalc.py --smoke 50      # 自检（前 50 笔触发信号）
  python 项目/回测系统/capital_dd_recalc.py                 # 全量两方案对比
  python 项目/回测系统/capital_dd_recalc.py --duckdb <库路径>  # 隔离 worktree 跑需显式指定
"""
import argparse
import datetime as _dt
import sys
from collections import defaultdict
from pathlib import Path

# 路径注入（与 main.py 同法：支持从 项目/ 或 交易部门根 任意层级启动）
_HERE = Path(__file__).resolve().parent           # 项目/回测系统
_ROOT = _HERE.parent.parent                       # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

from 分析决策.风控.capital import calc_trade_fee
from 回测系统.c23_capital_compare import c23_mask
from 回测系统.sim_capital import simulate_capital
from 回测系统.tighten_compare import load_triggered
from 数据基础.duckdb.config import DB_PATH
from 数据基础.duckdb.reader import read_kline

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "sim_capital_20260806_full" / "signals.csv"
OUT_DIR = _ROOT / "产出" / "输出"
DEFAULT_CAPITAL = 5600.0
DEFAULT_MOM = 0.10          # C23 动量阈值（与 c23_capital_compare 一致，T-024 探索最优）

# ── mom20 复算（tighten_compare 同口径，db_path 可注入）──
_KLINE_CACHE: dict[str, pd.DataFrame] = {}


def _kline(code: int, db_path: str) -> pd.DataFrame | None:
    """qfq K线（read_kline 同口径；带缓存）"""
    sym = f"{code:06d}"
    if sym not in _KLINE_CACHE:
        _KLINE_CACHE[sym] = read_kline(sym, db_path=db_path)   # 只读；None 也缓存（防空转）
    return _KLINE_CACHE[sym]


def recompute_mom20(sig: pd.Series, db_path: str) -> float | None:
    """复算 mom20 = trigger / 触发日前第 20 根 qfq 收盘 - 1

    触发日定位与 tighten_compare.recompute 一致：信号日 T 之后首根 最高≥trigger。
    返回 None 表示复算失败（数据版本差异，预期 0 笔）。
    """
    df = _kline(int(sig["code"]), db_path)
    if df is None or df.empty:
        return None
    sig_ts = pd.to_datetime(sig["date"])
    idx = df.index[df["日期"] == sig_ts]
    if len(idx) == 0:
        return None
    t = int(idx[0])
    high = df["最高"].to_numpy()
    trig_idx = None
    for j in range(t + 1, len(df)):
        if high[j] >= sig["trigger"]:
            trig_idx = j
            break
    if trig_idx is None or trig_idx < 20:
        return None
    close20 = float(df["收盘"].to_numpy()[trig_idx - 20])
    if close20 <= 0:
        return None
    return float(sig["trigger"]) / close20 - 1.0


def enrich_mom20(df: pd.DataFrame, db_path: str) -> pd.DataFrame:
    """为每笔信号附 mom20（带缓存，打印进度）"""
    moms, fails = [], 0
    n = len(df)
    for i, (_, row) in enumerate(df.iterrows(), 1):
        m = recompute_mom20(row, db_path)
        moms.append(m)
        if m is None:
            fails += 1
        if i % 200 == 0 or i == n:
            print(f"  [mom20 复算] {i}/{n} | 失败 {fails} 笔")
    df["mom20"] = moms
    return df


# ── 总资产曲线重建 ──

def _prev_trade_day(kline: pd.DataFrame, d: pd.Timestamp) -> pd.Timestamp | None:
    """该股 K线中 < d 的最近交易日（起点定位用）"""
    dates = pd.to_datetime(kline["日期"])
    m = dates < d
    return dates[m].iloc[-1] if m.any() else None


def build_total_asset_curve(trades: list[dict], capital: float,
                            kline_fn) -> pd.DataFrame:
    """逐日总资产曲线 = 现金余额 + Σ(持仓股数 × 当日 qfq 收盘价)

    Args:
        trades: simulate_capital 输出的逐笔成交（date/exit_date/code/shares/
            entry/exit_price 字段）
        capital: 初始资金（元）
        kline_fn: callable(code) → DataFrame(日期, 收盘) 升序 或 None
            （qfq 收盘，与引擎同口径）

    Returns:
        DataFrame: date / cash / market_value / total_asset（逐日，含起点）
        终点总资产 == 旧口径终值（end_balance）——现金流事件与 simulate_capital
        完全一致，可作自检。
    """
    if not trades:
        return pd.DataFrame(columns=["date", "cash", "market_value", "total_asset"])

    # 1) 现金流事件（费用 calc_trade_fee 同口径：佣金万1.3最低1元 + 印花税万5无条件计）
    entries: list[tuple[pd.Timestamp, float, int, str]] = []   # (日, 扣款, 股数, code)
    exits: list[tuple[pd.Timestamp, float, int, str]] = []     # (日, 收款, 股数, code)
    for t in trades:
        cost = t["entry"] * t["shares"]
        fee_in = calc_trade_fee(cost)
        proceed = t["exit_price"] * t["shares"]
        fee_out = calc_trade_fee(proceed)
        entries.append((pd.Timestamp(str(t["date"])), cost + fee_in,
                        int(t["shares"]), str(t["code"])))
        exits.append((pd.Timestamp(str(t["exit_date"])), proceed - fee_out,
                      int(t["shares"]), str(t["code"])))

    # 2) 交易日历 = 涉及股票 K线日期并集 + 全部事件日期（防停牌日事件丢失）
    codes = sorted({str(t["code"]) for t in trades})
    kl: dict[str, pd.DataFrame | None] = {c: kline_fn(c) for c in codes}
    cal: set[pd.Timestamp] = set()
    for c in codes:
        k = kl.get(c)
        if k is not None and len(k):
            cal.update(pd.to_datetime(k["日期"]).tolist())
    for d, _, _, _ in entries + exits:
        cal.add(d)
    min_entry = min(e[0] for e in entries)
    max_event = max(d for d, _, _, _ in entries + exits)   # 模拟期终点 = 最后事件日
    first_code = entries[0][3]
    start = _prev_trade_day(kl.get(first_code), min_entry) if kl.get(first_code) is not None else None
    if start is None:
        start = min_entry - pd.Timedelta(days=1)
    # 曲线范围 = [起点, 最后事件日]（与旧口径 equity 曲线范围一致；无事件期间不延伸）
    cal = sorted(d for d in cal if start <= d <= max_event)
    if not cal or cal[0] != start:
        cal = [start] + cal

    # 3) 每股收盘序列（asof 前值填充：停牌取最近可得收盘，与 searchsorted 同语义）
    close_series: dict[str, pd.Series] = {}
    for c in codes:
        k = kl.get(c)
        if k is not None and len(k):
            close_series[c] = pd.Series(k["收盘"].to_numpy(dtype=float),
                                        index=pd.to_datetime(k["日期"]))

    def px_on(code: str, d: pd.Timestamp) -> float | None:
        s = close_series.get(code)
        if s is None:
            return None
        v = s.asof(d)   # ≤ d 的最近收盘（升序索引）
        return None if pd.isna(v) else float(v)

    exits_by: dict[pd.Timestamp, list[tuple[str, float, int]]] = defaultdict(list)
    entries_by: dict[pd.Timestamp, list[tuple[str, float, int]]] = defaultdict(list)
    for d, amt, sh, c in exits:
        exits_by[d].append((c, amt, sh))
    for d, amt, sh, c in entries:
        entries_by[d].append((c, amt, sh))
    # 兜底价 = 该股最近一次入场价（个股无行情数据时用）
    entry_px: dict[str, float] = {}
    for t in trades:
        entry_px[str(t["code"])] = float(t["entry"])

    # 4) 逐日估值（同一日先平后开，对齐 simulate_capital 循环顺序）
    cash = capital
    holdings: dict[str, int] = {}
    rows: list[dict] = []
    for d in cal:
        for c, amt, sh in exits_by.get(d, []):
            cash += amt
            holdings[c] = holdings.get(c, 0) - sh
            if holdings.get(c, 0) <= 0:
                holdings.pop(c, None)
        for c, amt, sh in entries_by.get(d, []):
            cash -= amt
            holdings[c] = holdings.get(c, 0) + sh
        mv = 0.0
        for c, sh in holdings.items():
            px = px_on(c, d)
            if px is None:
                px = entry_px.get(c, 0.0)
            mv += sh * px
        rows.append({"date": d, "cash": round(cash, 2),
                     "market_value": round(mv, 2),
                     "total_asset": round(cash + mv, 2)})
    return pd.DataFrame(rows)


def max_drawdown(curve: pd.DataFrame, initial: float) -> dict:
    """最大回撤（与 sim_capital 同算法，输入换成逐日总资产曲线）

    - 金额 = 历史峰值 → 谷底最大跌幅
    - 主百分比 = 金额 / 初始资金（与旧口径同算法，保证可比）
    - 附：占峰值百分比（业界标准，参考口径）
    - 时长 = 最近峰值 → 当前谷底的最长自然日跨度（旧算法移植）
    """
    if curve.empty:
        return {"max_dd": 0.0, "max_dd_pct": 0.0, "max_dd_pct_peak": 0.0,
                "dd_days": 0, "peak_date": None, "trough_date": None,
                "end_total": round(initial, 2)}
    rows = curve.sort_values("date").reset_index(drop=True)
    dates = rows["date"].tolist()
    vals = rows["total_asset"].astype(float).tolist()
    peak, peak_date = initial, dates[0]
    max_dd, trough_val, trough_date = 0.0, peak, dates[0]
    dd_peak, dd_peak_date = peak, dates[0]   # 最大回撤段起点（峰值/峰值日）
    dd_days, recent_peak_idx = 0, 0
    for i, v in enumerate(vals):
        if v >= peak:
            peak, peak_date = v, dates[i]
            recent_peak_idx = i
        else:
            dd = peak - v
            if dd > max_dd:
                max_dd, trough_val, trough_date = dd, v, dates[i]
                dd_peak, dd_peak_date = peak, peak_date
            span = (dates[i] - dates[recent_peak_idx]).days
            dd_days = max(dd_days, span)
    return {
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / initial * 100, 2) if initial else 0.0,
        "max_dd_pct_peak": round(max_dd / dd_peak * 100, 2) if dd_peak else 0.0,
        "dd_days": dd_days,
        "peak_date": str(dd_peak_date)[:10],
        "trough_date": str(trough_date)[:10],
        "trough_val": round(trough_val, 2),
        "end_total": round(vals[-1], 2),
    }


# ── CLI ──

def run_one(df: pd.DataFrame, capital: float, risk_ratio: float, max_positions: int,
            db_path: str) -> tuple[dict, dict]:
    """单方案：simulate_capital（旧口径）+ 总资产口径重算（新口径）"""
    res = simulate_capital(df, capital, risk_ratio, max_positions=max_positions,
                           mode="prebreak", hold="20d", grades=["S"])
    curve = build_total_asset_curve(res["trades"], capital,
                                    lambda c: _kline(int(c), db_path))
    new_dd = max_drawdown(curve, capital)
    # 自检：新口径曲线终点应等于旧口径终值（现金流事件同源）
    delta = abs(new_dd["end_total"] - res["end_balance"])
    print(f"  [自检] 曲线终点 {new_dd['end_total']:,.2f} vs 旧口径终值 {res['end_balance']:,.2f}"
          f"（差 {delta:.4f}）")
    if delta > 0.5:
        print("  [警告] 新口径现金流与 simulate_capital 不一致，请核查！")
    return res, new_dd


def render_report(base_res, base_new, c23_res, c23_new, args) -> str:
    """渲染新旧口径回撤对比报告（markdown）"""
    br, bn = base_res, base_new
    cr, cn = c23_res, c23_new
    lines = [
        "# 真实回撤重算——总资产口径（现金 + 持仓市值 · 2026-08-06 老板拍板）",
        "",
        (f"> 日期：{_dt.datetime.now().astimezone().date().isoformat()} · 背景：sim_capital "
         "回撤按现金余额峰值口径（买入时现金骤减但持仓市值不计入），回撤被系统性放大；"
         "本次以总资产口径（现金 + Σ持仓×当日 qfq 收盘）重算真实回撤，供 C23 方案回撤可接受度判断。"),
        (f"> 资金配置：{args.capital:,.0f} 元 × 单笔风险 {args.risk_ratio:.1%} × 持仓上限 "
         f"{args.max_positions} 只｜评级 S｜prebreak/20d｜整手 100 股｜费用 佣金万1.3(最低1元)+印花税万5"),
        (f"> 信号源：{Path(args.signals).name}（{'全量' if not args.smoke else f'自检前 {args.smoke} 笔触发'}｜"
         "prebreak / S / dn_confirm=1.5 / 2023-07~2026-07）｜C23 过滤 = mom20 ≤ "
         f"{args.mom_threshold:.0%}（tighten_compare 同口径复算）+ risk 0.5~3 元"),
        "",
        "## 一、口径说明",
        "",
        ("- **旧口径（sim_capital 验收口径）**：资金曲线 = 起点 + 每笔平仓后快照（纯现金余额），"
         "持仓市值不计入。买入瞬间现金骤减 → 回撤系统性放大（保守口径）。"),
        ("- **新口径（本次重算）**：逐日总资产 = 当日现金余额 + Σ(持仓股数 × 当日 qfq 收盘价)。"
         "现金流事件与 simulate_capital 完全一致（入场日扣款/出场日收款/同日先平后开），"
         "持仓区间 [入场日, 出场日) 按收盘估值，出场日按出场价兑现；停牌前值填充。"
         "回撤算法与旧口径一致（历史峰值→谷底最大跌幅 + 最近峰值→谷底最长自然日跨度），"
         "只换资产定义。"),
        "- **百分比口径**：主口径 = 金额/初始资金（与旧口径可比）；另附 金额/峰值（业界标准，参考）。",
        "",
        "## 二、新旧口径回撤对比",
        "",
        ("| 指标 | 现方案·旧口径 | 现方案·新口径 | C23·旧口径 | C23·新口径 |"),
        "|---|---:|---:|---:|---:|",
        f"| 终值资金（元） | {br['end_balance']:,.2f} | {bn['end_total']:,.2f} | {cr['end_balance']:,.2f} | {cn['end_total']:,.2f} |",
        f"| 最大回撤（元） | {br['max_dd']:,.2f} | {bn['max_dd']:,.2f} | {cr['max_dd']:,.2f} | {cn['max_dd']:,.2f} |",
        (
            f"| 回撤 %（占初始资金） | {br['max_dd_pct']:.1f}% | {bn['max_dd_pct']:.1f}% | "
            f"{cr['max_dd_pct']:.1f}% | {cn['max_dd_pct']:.1f}% |"
        ),
        (
            f"| 回撤 %（占峰值，参考） | — | {bn['max_dd_pct_peak']:.1f}% | — | {cn['max_dd_pct_peak']:.1f}% |"
        ),
        f"| 回撤时长（天） | {br['dd_days']} | {bn['dd_days']} | {cr['dd_days']} | {cn['dd_days']} |",
        (
            f"| 峰值日期 → 谷底日期 | — | {bn['peak_date']} → {bn['trough_date']} | — | "
            f"{cn['peak_date']} → {cn['trough_date']} |"
        ),
        f"| 总收益 | {br['total_ret']:+.1f}% | {br['total_ret']:+.1f}% | {cr['total_ret']:+.1f}% | {cr['total_ret']:+.1f}% |",
        f"| 成交笔数 | {br['n_exec']:,} | {br['n_exec']:,} | {cr['n_exec']:,} | {cr['n_exec']:,} |",
        "",
        "## 三、白话结论草稿",
        "",
    ]
    lines += _verdict(base_res, base_new, c23_res, c23_new)
    lines += [
        "",
        "## 四、可比性说明（12 组网格实验）",
        "",
        ("- T-023 网格实验 12 组（风险 1.5/2/3/5% × 持仓 2/3/5）与 2 仓/5 仓等历史数字仍为"
         "旧口径（现金余额），其「保守放大」性质与本报告一致——如需统一可比，可用本脚本"
         "（capital_dd_recalc.py）按同参数逐组重跑；本次仅重算 1.5%×3 仓两组（老板拍板范围）。"),
        ("- C23 资金约束对比（2026-08-06，产出/输出/C23资金约束对比-20260806.md）中 76.1%/70.9% "
         "为旧口径，本文为同场景新口径，两者不可直接混比。"),
        "",
        "---",
        "",
        ("> 出处：2026-08-06 老板拍板（总资产口径真实回撤重算）。实现：项目/回测系统/"
         "capital_dd_recalc.py；sim_capital 核心逻辑零改动（复用 trades 独立重算）；"
         "mom20 复算同 tighten_compare（duckdb qfq 只读）。替换与否签字权归老板。"),
    ]
    return "\n".join(lines)


def _verdict(br, bn, cr, cn) -> list[str]:
    """数据驱动的白话结论草稿（最终由老板/助理复核）"""
    out = [
        (f"- **口径水分**：旧口径把「买入扣款但市值不计入」的瞬时现金缺口当作回撤，系统性放大。"
         f"现方案 76.1% → 新口径 {bn['max_dd_pct']:.1f}%（占初始），"
         f"C23 70.9% → {cn['max_dd_pct']:.1f}%（占初始）——旧口径中"
         f"约 {br['max_dd_pct'] - bn['max_dd_pct']:.1f}pp / {cr['max_dd_pct'] - cn['max_dd_pct']:.1f}pp "
         "是口径水分（持仓市值未计入造成）。"),
    ]
    out.append("")
    out.append(f"- **真实回撤**：新口径下现方案最大回撤 {bn['max_dd']:,.2f} 元"
               f"（{bn['max_dd_pct']:.1f}%，占峰值 {bn['max_dd_pct_peak']:.1f}%），"
               f"谷底 {bn['trough_date']}；C23 方案 {cn['max_dd']:,.2f} 元"
               f"（{cn['max_dd_pct']:.1f}%，占峰值 {cn['max_dd_pct_peak']:.1f}%），"
               f"谷底 {cn['trough_date']}。")
    out.append("")
    d_dd = cn["max_dd_pct"] - bn["max_dd_pct"]
    out.append(f"- **对 C23 决策影响**：新口径下 C23 回撤（{cn['max_dd_pct']:.1f}%）较现方案"
               f"（{bn['max_dd_pct']:.1f}%）{f'低 {abs(d_dd):.1f}pp' if d_dd < 0 else f'高 {d_dd:.1f}pp'}，"
               f"回撤时长 {bn['dd_days']} → {cn['dd_days']} 天；"
               + ("C23 在收益大幅领先（+69.2% vs +12.2%）的同时回撤也更小/相当，"
                  "回撤维度不构成否决理由。"
                  if cr["total_ret"] > br["total_ret"] and d_dd <= 0
                  else "C23 收益领先但回撤略高，需结合收益/回撤比人工判断。"))
    out.append("")
    out.append("> 判定提醒：%口径（占初始）与旧口径算法完全一致，可直接与历史数字比较方向；"
               "占峰值口径更贴近业界定义。最终可接受度与替换与否由老板签字。")
    return out


def main() -> int:
    today = _dt.datetime.now().astimezone().date().strftime("%Y%m%d")
    ap = argparse.ArgumentParser(description="总资产口径回撤重算（现金+持仓市值，C23 判断用）")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="signals.csv 路径")
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="初始资金（默认 5600）")
    ap.add_argument("--risk-ratio", type=float, default=0.02,
                    help="单笔风险比例（默认 2.0%%——G9 实盘线定稿参数 2026-08-06 老板拍板；"
                         "对照实验显式传旧值 0.015）")
    ap.add_argument("--max-positions", type=int, default=3, help="最多同时持仓数（默认 3）")
    ap.add_argument("--mom-threshold", type=float, default=DEFAULT_MOM,
                    help=f"C23 动量阈值（默认 {DEFAULT_MOM}，T-024 探索最优）")
    ap.add_argument("--duckdb", default=None,
                    help="duckdb 库路径（默认 交易部门/数据基础/data/t017_p2.duckdb；"
                         "隔离 worktree 不含数据文件，需显式指定主仓库路径）")
    ap.add_argument("--smoke", type=int, default=0, help="自检：只处理前 N 笔触发信号")
    ap.add_argument("--out", default=str(OUT_DIR / f"真实回撤重算-{today}.md"),
                    help="报告输出路径")
    args = ap.parse_args()

    db = args.duckdb or str(DB_PATH)
    if not Path(db).exists():
        print(f"[dd 重算] duckdb 不存在：{db}")
        print("          （隔离 worktree 不含数据文件，请 --duckdb 指定主仓库库路径）")
        return 1

    df = load_triggered(Path(args.signals), args.smoke)
    print(f"[dd 重算] 基线 {len(df)} 笔 20d 触发 | duckdb 复算 mom20（tighten_compare 同口径）...")
    df = enrich_mom20(df, db)
    n_ok = int(df["mom20"].notna().sum())
    print(f"[dd 重算] mom20 有效 {n_ok} 笔（失败 {len(df) - n_ok}）")
    df_c23 = df[c23_mask(df, args.mom_threshold)].copy()
    print(f"[dd 重算] C23 过滤后 {len(df_c23)} 笔（{len(df_c23) / len(df):.1%} 留存）")

    print("[dd 重算] 现方案：simulate_capital + 总资产口径重算 ...")
    base_res, base_new = run_one(df, args.capital, args.risk_ratio, args.max_positions, db)
    print("[dd 重算] C23 方案：simulate_capital + 总资产口径重算 ...")
    c23_res, c23_new = run_one(df_c23, args.capital, args.risk_ratio, args.max_positions, db)

    print(f"[dd 重算] 现方案 旧 {base_res['max_dd_pct']:.1f}% → 新 {base_new['max_dd_pct']:.1f}%"
          f"（{base_new['max_dd_pct_peak']:.1f}% 占峰值）| {base_new['dd_days']} 天")
    print(f"[dd 重算] C23 方案 旧 {c23_res['max_dd_pct']:.1f}% → 新 {c23_new['max_dd_pct']:.1f}%"
          f"（{c23_new['max_dd_pct_peak']:.1f}% 占峰值）| {c23_new['dd_days']} 天")

    report = render_report(base_res, base_new, c23_res, c23_new, args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n报告 → {out}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
