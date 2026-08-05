#!/usr/bin/env python3
"""R-009 模块2·资金约束回测（2026-08-06 老板拍板升级：多持仓参数化 + 完整报告指标）

对回测 signals 逐笔模拟真实资金执行（A股·仅做多）：
  - 多持仓并发：最多同时持仓 max_positions 只（默认 2，老板实盘约束画像 2026-08-05 定；
    传 1 = 旧版单持仓顺序行为）
  - 可买检查：买入金额 ≤ 可用现金；整手 100 股向下取整；
    每股风险 = entry - stop ≤ 单笔风险额/100（单笔风险额 = 初始资金 × 风险比例，恒定）
  - 费用：佣金万1.3（双边，最低1元）+ 印花税万5（卖出）
  - 输出：资金曲线（每笔平仓后已实现净值）+ 总收益/最大回撤（金额+%+回撤时长）+
    交易笔数/年化笔数/平均持有天数（交易日）+ 胜率/平均R/盈亏比 +
    单笔风险执行检查（是否恒 ≤ 单笔风险额）+ 100 笔节奏预估（按实际信号频率）

用法:
    python 项目/回测系统/sim_capital.py [--signals 路径] [--capital 5600] [--risk-ratio 0.015]
        [--max-positions 2] [--mode prebreak] [--hold 20d] [--grades S A B]
        [--out-csv 资金曲线.csv]
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 交易部根路径注入（复用回测 main.py 方式）
_HERE = os.path.dirname(os.path.abspath(__file__))   # 项目/回测系统
sys.path.insert(0, os.path.dirname(_HERE))            # 项目/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 交易部根

import numpy as np
import pandas as pd

DEFAULT_SIGNALS = os.path.join("项目", "output", "backtest", "20230701_20260804", "signals.csv")


def _hold_label(hold: str) -> int:
    """'20d' → 20；容忍 '20' 写法"""
    return int(str(hold).replace("d", ""))


def simulate_capital(df: pd.DataFrame, capital: float, risk_ratio: float,
                     max_positions: int = 2, mode: str = "prebreak",
                     hold: str = "20d", grades: list[str] | None = None) -> dict:
    """资金约束逐笔模拟（核心逻辑，可单测）

    Args:
        df: signals.csv 全量（需 mode/code/date/grade/close/stop/risk/entry_/exit_ 列）
        capital: 初始资金（元）
        risk_ratio: 单笔风险比例（初始资金 × 比例 = 单笔风险额，恒定不变）
        max_positions: 最多同时持仓数（≥1；1 = 单持仓顺序）
        mode: normal / prebreak
        hold: 观察窗（'20d'）
        grades: 只做哪些评级（None=全部；默认由 CLI 传，老板约束=只做 S）

    Returns:
        dict: 摘要指标 + trades（逐笔成交）+ equity（资金曲线 DataFrame）
    """
    grades = grades or []
    h = _hold_label(hold)
    sub = df[(df["mode"] == mode) & (df[f"triggered_{h}d"] == 1)]
    if grades:
        sub = sub[sub["grade"].isin(grades)]
    sub = sub.sort_values(["date", "code"]).copy()
    max_date = str(sub["date"].max())[:10] if len(sub) else ""  # 数据末交易日（持仓未完成判定）

    risk_amt = capital * risk_ratio                     # 单笔风险额（元，恒定）
    max_risk_per_share = risk_amt / 100                  # 每股风险上限（整手 100 股）
    _entry_col, exit_col = f"entry_{h}d", f"exit_{h}d"
    exit_date_col, r_col = f"exit_date_{h}d", f"r_{h}d"

    from 分析决策.风控.capital import calc_trade_fee

    balance = capital          # 现金（买入扣款/卖出回款，平仓后 = 总资产已实现值）
    positions: list[dict] = []  # 活跃持仓（到期才平，日线粒度）
    trades: list[dict] = []     # 已平仓成交
    equity: list[dict] = []     # 资金曲线（起点快照 + 每笔平仓后快照）
    reasons: dict[str, int] = {}
    peak, max_dd = capital, 0.0
    if len(sub):
        equity.append({"date": str(sub["date"].iloc[0])[:10], "balance": round(capital, 2)})

    for _, row in sub.iterrows():
        date = str(row["date"])[:10]
        # 1) 先平到期持仓（exit_date ≤ 当前信号日 → 以该日成交价出场）
        for p in [p for p in positions if p["exit_date"] <= date]:
            proceed = p["exit_price"] * p["shares"]
            fee_out = calc_trade_fee(proceed)
            balance += proceed - fee_out
            trades.append({**p, "exit_date": p["exit_date"], "pnl": p["pnl"]})
            equity.append({"date": p["exit_date"], "balance": round(balance, 2)})
            peak = max(peak, balance)
            max_dd = max(max_dd, peak - balance)
        positions = [p for p in positions if p["exit_date"] > date]

        # 2) 持仓数上限
        if len(positions) >= max_positions:
            reasons[f"持仓数已满(最多{max_positions}只)"] = \
                reasons.get(f"持仓数已满(最多{max_positions}只)", 0) + 1
            continue

        # 成交价 = 引擎入场价（entry_{h}d 列）：prebreak=触发价 / normal=信号日收盘——
        # 与 R 口径一致（R = (exit - entry)/risk，引擎基于触发价计算）。
        # 2026-08-06 修复：旧版用信号日 close 成交，突破日大阳线收盘远离触发价，
        # 导致 18/79 笔 R 与金额盈亏符号矛盾（触发价买/收盘价买差异）。
        price = float(row[_entry_col])
        risk_ps = float(row.get("risk", 0) or 0)
        # 3) 可买股数：资金上限与风险上限取 min，整手（100股）向下取整
        if risk_ps <= 0:
            shares = 0
        else:
            shares = int(min(balance // price, risk_amt // risk_ps) / 100) * 100
        if shares < 100:
            if risk_ps > 0 and risk_amt // risk_ps < 100:
                reasons[f"每股风险{risk_ps:.2f}超限(>{max_risk_per_share:.2f})"] = \
                    reasons.get(f"每股风险{risk_ps:.2f}超限(>{max_risk_per_share:.2f})", 0) + 1
            else:
                reasons["资金不足"] = reasons.get("资金不足", 0) + 1
            continue
        cost = price * shares

        # 4) 出场日期合法性校验（坏行/数据末尾未完成持仓跳过）
        ex_d = str(row[exit_date_col])
        if not ex_d or len(ex_d) < 8 or not ex_d[:4].isdigit():
            reasons["出场日期异常(坏数据行)"] = reasons.get("出场日期异常(坏数据行)", 0) + 1
            continue
        if ex_d[:10] >= max_date:
            reasons["持仓未完成(数据末尾)"] = reasons.get("持仓未完成(数据末尾)", 0) + 1
            continue

        # 5) 成交（整手，费用已含）
        fee_in = calc_trade_fee(cost)
        balance -= cost + fee_in
        exit_price = float(row[exit_col])
        fee_out = calc_trade_fee(exit_price * shares)
        positions.append({
            "date": date, "code": row["code"], "grade": row["grade"],
            "entry": price, "exit_price": exit_price, "exit_date": ex_d[:10],
            "r": float(row[r_col]), "shares": int(shares),
            "risk_actual": round(risk_ps * shares, 2),   # 单笔实际风险（每股风险×股数）
            "pnl": round((exit_price - price) * shares - fee_in - fee_out, 2),
        })

    # 模拟结束：数据末尾仍持仓的按已记录成交平掉（不开新仓）
    for p in positions:
        proceed = p["exit_price"] * p["shares"]
        fee_out = calc_trade_fee(proceed)
        balance += proceed - fee_out
        trades.append({**p, "pnl": p["pnl"]})
        equity.append({"date": p["exit_date"], "balance": round(balance, 2)})

    # ── 指标汇总 ──
    n_all = len(sub)
    n_exec = len(trades)
    exec_rate = n_exec / n_all * 100 if n_all else 0
    total_pnl = balance - capital
    total_ret = total_pnl / capital * 100 if capital else 0.0
    rs = [t["r"] for t in trades]
    avg_r = sum(rs) / n_exec if n_exec else 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / n_exec if n_exec else 0.0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    # 回撤时长（已实现净值口径，起点=首个信号日锚点）：从最近峰值到当前净值的最长自然日跨度
    dd_days = 0
    if len(equity) >= 2:
        eq = pd.DataFrame(equity).sort_values("date").reset_index(drop=True)
        peak_idx = 0
        for i in range(len(eq)):
            if eq["balance"].iloc[i] >= eq["balance"].iloc[: i + 1].max():
                peak_idx = i
            elif eq["balance"].iloc[i] < eq["balance"].iloc[: i + 1].max():
                span = (pd.Timestamp(eq["date"].iloc[i]) - pd.Timestamp(eq["date"].iloc[peak_idx])).days
                dd_days = max(dd_days, span)

    # 平均持有天数（交易日口径）+ 年化 + 100 笔节奏
    hold_days = []
    for t in trades:
        try:
            hold_days.append(np.busday_count(str(t["date"]), str(t["exit_date"])))
        except ValueError:
            hold_days.append(0)
    avg_hold = float(np.mean(hold_days)) if hold_days else 0.0

    dates_all = sub["date"].astype(str)
    if len(dates_all):
        n_days = max(1, np.busday_count(dates_all.min()[:10], str(max_date)))  # 首信号~末交易日
        years = max(n_days / 252.0, 1e-9)
        per_year = n_exec / years if years else 0.0
        months_for_100 = 100 / per_year * 12 if per_year else float("inf")
    else:
        n_days, per_year, months_for_100 = 0, 0.0, float("inf")

    return {
        "capital": capital, "risk_amt": risk_amt, "max_risk_per_share": max_risk_per_share,
        "max_positions": max_positions, "mode": mode, "hold": h, "grades": grades,
        "n_all": n_all, "n_exec": n_exec, "exec_rate": exec_rate,
        "reasons": reasons,
        "end_balance": balance, "total_pnl": total_pnl, "total_ret": total_ret,
        "max_dd": max_dd, "max_dd_pct": max_dd / capital * 100 if capital else 0.0,
        "dd_days": dd_days,
        "avg_r": avg_r, "win_rate": win_rate, "profit_factor": profit_factor,
        "avg_hold_days": avg_hold, "per_year": per_year, "months_for_100": months_for_100,
        "risk_exec": {
            "min": min((t["risk_actual"] for t in trades), default=0.0),
            "max": max((t["risk_actual"] for t in trades), default=0.0),
            "mean": float(np.mean([t["risk_actual"] for t in trades])) if trades else 0.0,
            "over_risk_amt": sum(1 for t in trades if t["risk_actual"] > risk_amt),
            "risk_amt": risk_amt,
        },
        "trades": trades,
        "equity": pd.DataFrame(equity),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=DEFAULT_SIGNALS)
    ap.add_argument("--capital", type=float, default=5600.0, help="初始资金（默认 5600，老板约束）")
    ap.add_argument("--risk-ratio", type=float, default=0.015, help="单笔风险比例（默认 1.5%，老板约束）")
    ap.add_argument("--max-positions", type=int, default=2,
                    help="最多同时持仓数（默认 2，老板实盘约束；1=旧版单持仓顺序）")
    ap.add_argument("--mode", default="prebreak", choices=["normal", "prebreak"])
    ap.add_argument("--hold", default="20d", choices=["5d", "10d", "20d"])
    ap.add_argument("--grades", nargs="+", default=["S"], help="只做评级（默认 S，老板约束）")
    ap.add_argument("--out-csv", default=None, help="资金曲线 CSV 输出路径（可选）")
    args = ap.parse_args()

    df = pd.read_csv(args.signals, encoding="utf-8-sig")
    if not len(df):
        print("无信号数据")
        return 1
    res = simulate_capital(df, args.capital, args.risk_ratio,
                           max_positions=args.max_positions, mode=args.mode,
                           hold=args.hold, grades=args.grades)
    if not res["n_exec"]:
        print("无触发信号")
        return 1
    if args.out_csv:
        res["equity"].to_csv(args.out_csv, index=False, encoding="utf-8-sig")
        print(f"资金曲线 → {args.out_csv}")

    # ── 版式输出 ──
    W = 78
    line = "-" * W
    r = res
    print(line)
    print("模拟实盘回测·资金约束（2026-08-06 老板拍板口径）".center(W))
    print(line)
    print(f"  初始资金        {r['capital']:>10,.2f} 元 | 单笔风险 {r['risk_amt']:,.0f} 元（{args.risk_ratio:.1%}）"
          f" | 持仓上限 {r['max_positions']} 只 | 评级 {'/'.join(r['grades'])} | {r['mode']}/{r['hold']}d")
    print(line)
    print(f"  信号总数        {r['n_all']:>10,}")
    print(f"  实际可执行      {r['n_exec']:>10,}（{r['exec_rate']:.1f}%）")
    for reason, cnt in sorted(r["reasons"].items(), key=lambda x: -x[1]):
        print(f"    不可买原因      {reason}: {cnt}")
    print(line)
    print(f"  终值资金        {r['end_balance']:>10,.2f} 元（{r['total_pnl']:+,.2f} 元 / {r['total_ret']:+.1f}%）")
    print(f"  最大回撤        {r['max_dd']:>10,.2f} 元（{r['max_dd_pct']:.1f}%），最长回撤时长 {r['dd_days']} 天")
    print(f"  交易笔数        {r['n_exec']:>10,} 笔 | 年化 {r['per_year']:.1f} 笔 | 平均持有 {r['avg_hold_days']:.1f} 交易日")
    print(f"  胜率 / 平均R    {r['win_rate']:.1%} / {r['avg_r']:.3f}")
    if r["profit_factor"] == float("inf"):
        print("  盈亏比(金额)    ∞（无亏损笔）")
    else:
        print(f"  盈亏比(金额)    {r['profit_factor']:.2f}")
    print(f"  100笔节奏预估   {r['months_for_100']:.1f} 个月（约 {r['months_for_100'] / 12:.1f} 年）")
    print(f"  单笔风险执行    min {r['risk_exec']['min']:.2f} / mean {r['risk_exec']['mean']:.2f} / "
          f"max {r['risk_exec']['max']:.2f} 元 | 超 {r['risk_amt']:.0f} 元违规 {r['risk_exec']['over_risk_amt']} 笔")
    print(line)
    print("  说明：整手100股；费用已含（佣金万1.3最低1元+印花税万5卖出）；资金曲线=每笔平仓后已实现净值")
    print("  说明：单笔风险额 = 初始资金 × 风险比例（恒定，不随净值浮动）")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
