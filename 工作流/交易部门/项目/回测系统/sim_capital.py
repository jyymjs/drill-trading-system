#!/usr/bin/env python3
"""R-009 模块2：资金约束回测（5600 元 + 100股整手 + 单笔风险 1.5%）

对回测 signals 逐笔模拟真实资金执行：
  - 单持仓顺序（一笔 20d 持仓结束再开下一笔）
  - 可买检查：买入金额 ≤ 可用资金（整手 100 股）；每股风险 ≤ 单笔风险额/100
  - 费用：佣金万1.3（双边）+ 印花税万5（卖出）
  - 输出：可执行率 + 不可买原因分布 + 真实资金曲线 + 对比回测理论值

用法:
    python 项目/回测系统/sim_capital.py [--signals 路径] [--capital 5600] [--risk-ratio 0.015]
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 交易部根路径注入（复用回测 main.py 方式）
_HERE = os.path.dirname(os.path.abspath(__file__))   # 项目/回测系统
sys.path.insert(0, os.path.dirname(_HERE))            # 项目/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 交易部根

import pandas as pd

DEFAULT_SIGNALS = os.path.join("项目", "output", "backtest", "20230701_20260804", "signals.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=DEFAULT_SIGNALS)
    ap.add_argument("--capital", type=float, default=5600.0, help="初始资金（默认 5600）")
    ap.add_argument("--risk-ratio", type=float, default=0.015, help="单笔风险比例（默认 1.5%）")
    ap.add_argument("--mode", default="prebreak", choices=["normal", "prebreak"])
    ap.add_argument("--hold", default="20d", choices=["5d", "10d", "20d"])
    args = ap.parse_args()

    df = pd.read_csv(args.signals, encoding="utf-8-sig")
    sub = df[(df["mode"] == args.mode) & (df[f"triggered_{args.hold}"] == 1)].copy()
    sub = sub.sort_values("date")
    max_date = str(sub["date"].max())[:10]  # 数据末交易日（持仓未完成判定）
    if not len(sub):
        print("无触发信号")
        return 1

    risk_amt = args.capital * args.risk_ratio          # 单笔风险额（元）
    max_risk_per_share = risk_amt / 100                 # 每股风险上限（整手 100 股）
    _entry_col, exit_col = f"entry_{args.hold}", f"exit_{args.hold}"
    r_col = f"r_{args.hold}"

    balance = args.capital
    trades = []
    reasons = {}
    last_exit = None
    peak = args.capital
    max_dd = 0.0

    for _, row in sub.iterrows():
        date = str(row["date"])[:10]
        if last_exit and date < last_exit:
            continue  # 单持仓：上一笔未结束
        price = float(row["close"])
        risk_ps = float(row.get("risk", 0) or 0)

        # 可买股数：资金上限与风险上限取 min，整手（100股）向下取整
        if risk_ps <= 0:
            shares = 0
        else:
            shares = int(min(balance // price, risk_amt // risk_ps) / 100) * 100
        if shares < 100:
            if risk_ps > 0 and risk_amt // risk_ps < 100:
                reasons[f"每股风险{risk_ps:.2f}超限(>{risk_amt/100:.2f})"] = \
                    reasons.get(f"每股风险{risk_ps:.2f}超限(>{risk_amt/100:.2f})", 0) + 1
            else:
                reasons["资金不足"] = reasons.get("资金不足", 0) + 1
            continue
        cost = price * shares

        # 出场日期合法性校验（坏行/数据末尾未完成持仓跳过——避免单持仓检查失效）
        ex_d = str(row[f"exit_date_{args.hold}"])
        if not ex_d or len(ex_d) < 8 or not ex_d[:4].isdigit():
            reasons["出场日期异常(坏数据行)"] = reasons.get("出场日期异常(坏数据行)", 0) + 1
            continue
        if ex_d[:10] >= max_date:
            reasons["持仓未完成(数据末尾)"] = reasons.get("持仓未完成(数据末尾)", 0) + 1
            continue

        # 成交（整手，费用已含）
        from 分析决策.风控.capital import calc_trade_fee
        fee_in = calc_trade_fee(cost)
        balance -= cost + fee_in
        exit_price = float(row[exit_col])
        proceed = exit_price * shares
        fee_out = calc_trade_fee(proceed)
        balance += proceed - fee_out
        last_exit = ex_d[:10]
        r = float(row[r_col])
        trades.append({"date": date, "code": row["code"], "grade": row["grade"],
                       "entry": price, "exit": exit_price, "r": r, "shares": shares,
                       "pnl": (exit_price - price) * shares - fee_in - fee_out})
        peak = max(peak, balance)
        max_dd = max(max_dd, peak - balance)

    n_all = len(sub)
    n_exec = len(trades)
    exec_rate = n_exec / n_all * 100 if n_all else 0
    total_pnl = balance - args.capital
    avg_r = sum(t["r"] for t in trades) / n_exec if n_exec else 0

    # ── 版式输出 ──
    W = 74
    line = "-" * W
    print(line)
    print("资金约束回测报告（R-009 模块2）".center(W))
    print(line)
    print(f"  初始资金        {args.capital:>10,.2f} 元")
    print(f"  单笔风险        {risk_amt:>10,.2f} 元（{args.risk_ratio:.1%}）")
    print(f"  每股风险上限    {max_risk_per_share:>10,.2f} 元（整手100股）")
    print(f"  模式/持有       {args.mode} / {args.hold}")
    print(line)
    print(f"  信号总数        {n_all:>10,}")
    print(f"  实际可执行      {n_exec:>10,}（{exec_rate:.1f}%）")
    for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    不可买原因      {reason}: {cnt}")
    print(line)
    print(f"  终值资金        {balance:>10,.2f} 元（{total_pnl:+,.2f} 元 / {total_pnl/args.capital*100:+.1f}%）")
    print(f"  最大回撤        {max_dd:>10,.2f} 元（{max_dd/args.capital*100:.1f}%）")
    print(f"  实际交易数      {n_exec:>10,} 笔")
    print(f"  实际平均R       {avg_r:>10.3f}")
    print(line)
    print("  对比：回测全样本平均R ≈ 0.5（prebreak/20d，理论值，不可执行信号已剔除）")
    print("  说明：单持仓顺序模拟；未重叠持仓；费用已含（佣金万1.3+印花税万5）")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
