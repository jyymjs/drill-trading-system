#!/usr/bin/env python3
"""R-056 止损体系逐日精确审计（2026-08-11 · 老板质疑"结论有真实数据支撑吗"）

对 r54_trail_compare 的补验：P2 网格"100% 跌破"是窗口末单次近似，本脚本用
**逐日 evaluate_exit 语义**（真实执行路径：拐点出现即上移止损）重验，并统计
移动获利/平保/主动出场/TTP 各规则的触发 → 出场路径 → 砍肉量化。

口径（与 r54 同框架）：r43_t2_T8 信号集 1,149 笔触发、触发价进、hold=20、
窗口=信号日后 20 交易日、df 触发日切片（P0-3）、成本开启、R 分母=原始风险。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402

from 数据基础.duckdb.reader import read_kline  # noqa: E402
from 分析决策.风控.exit_manager import Position, evaluate_exit  # noqa: E402
from 分析决策.分析.indicators import half_position_confirm_delay2  # noqa: E402
from 回测系统.tracking import Signal, _find_signal_index, _trade_cost, track_signal  # noqa: E402

SIG = _ROOT / "产出" / "输出" / "backtest_r43_t2_T8" / "signals.csv"
HOLD = 20


def audit() -> dict:
    sig = pd.read_csv(SIG, dtype={"code": str})
    trig = sig[sig["triggered_20d"] == 1]
    stats = {
        "confirmed": 0,
        "trail_trigger": 0, "trail_hit_3d": 0, "trail_hit_later": 0, "trail_survive": 0,
        "trail_breakeven_dummy": 0,       # 止损位 = 成本价+1分（兜底 = 重复平保）
        "breakeven_trigger": 0,           # 平保触发笔
        "active_trigger": 0,              # 主动出场触发笔
        "ttp_trigger": 0,                 # TTP 触发笔
        "trail_loss": 0.0,                # 移动获利笔的砍肉损失（基线R - 实际R）
        "trail_base_r": 0.0, "trail_act_r": 0.0,
    }
    samples = []
    for _, row in trig.iterrows():
        df = read_kline(row["code"], shared=True)
        if df is None or df.empty:
            continue
        dates = df["日期"].astype(str).str[:10].values
        highs = df["最高"].astype(float).values
        lows = df["最低"].astype(float).values
        closes = df["收盘"].astype(float).values
        sig_idx = _find_signal_index(df, pd.Timestamp(row["date"]))
        if sig_idx is None:
            continue
        trig_idx = next((j for j in range(sig_idx + 1, min(sig_idx + 1 + HOLD, len(df)))
                         if highs[j] >= row["trigger"]), None)
        if trig_idx is None:
            continue
        entry = float(row["trigger"])
        stop = float(row["stop"])
        risk = float(row["risk"])
        end = min(sig_idx + 1 + HOLD, len(df))
        v = half_position_confirm_delay2(df, entry, stop, trig_idx + 1, max_idx=end)
        if v["reject"] or v["stopped"]:
            continue
        stats["confirmed"] += 1
        used = v["conf_idx_used"]
        pos = Position(symbol=row["code"], direction="long", market="stock",
                       entry_price=entry, initial_stop=stop, current_stop=stop,
                       volume=100, grade_at_entry=str(row["grade"]))
        pos.highest_price = entry
        pos.lowest_price = entry
        exit_price, exit_date, exit_reason = None, None, ""
        trail_fired_day = None
        trail_stop_val = None
        for j in range(used + 1, end):
            day_df = df.iloc[trig_idx:j + 1]
            pos.highest_price = max(pos.highest_price, float(highs[j]))
            pos.lowest_price = min(pos.lowest_price, float(lows[j]))
            ev = evaluate_exit(pos, day_df)
            # 移动获利触发记录（层面3 reason）
            if ev["reason"].startswith("移动获利") and trail_fired_day is None:
                trail_fired_day = j
                trail_stop_val = ev["stop_update"]
                stats["trail_trigger"] += 1
                if ev["stop_update"] is not None and \
                        abs(ev["stop_update"] - round(entry + 0.01, 2)) < 0.011:
                    stats["trail_breakeven_dummy"] += 1
            if ev["reason"].startswith("平价保护"):
                stats["breakeven_trigger"] += 1
            if ev["reason"].startswith("主动出场"):
                stats["active_trigger"] += 1
            if ev["reason"].startswith("追踪获利"):
                stats["ttp_trigger"] += 1
            if ev["stop_update"] and ev["stop_update"] > pos.current_stop:
                pos.current_stop = ev["stop_update"]
            if ev["should_exit"]:
                exit_price = ev["exit_price"] or float(closes[j])
                exit_date = str(dates[j])[:10]
                exit_reason = ev["reason"]
                break
        if exit_price is None:
            exit_price = float(closes[min(end - 1, len(closes) - 1)])
            exit_date = str(dates[min(end - 1, len(dates) - 1)])[:10]
            exit_reason = "hold_end"
        cost = _trade_cost(entry, exit_price, True, 1.0)
        r = (exit_price - entry - cost) / risk if risk > 0 else 0.0
        # 移动获利笔的砍肉量化
        if trail_fired_day is not None:
            # 触发后 3 日内是否被打（低 ≤ 新止损）
            win3 = lows[trail_fired_day + 1:min(trail_fired_day + 4, end)]
            hit3 = bool(len(win3) and win3.min() <= trail_stop_val) if trail_stop_val else False
            if hit3:
                stats["trail_hit_3d"] += 1
            else:
                stats["trail_hit_later"] += 1
            base_r = float(row["r_20d"])
            stats["trail_base_r"] += base_r
            stats["trail_act_r"] += r
            stats["trail_loss"] += base_r - r
            if len(samples) < 6:
                samples.append((row["code"], str(row["date"])[:10], entry,
                                trail_stop_val, hit3, base_r, r, exit_reason[:18]))
    return stats, samples


def main() -> int:
    stats, samples = audit()
    n = stats["confirmed"]
    print(f"确认补仓: {n} 笔")
    print(f"移动获利触发（逐日语义）: {stats['trail_trigger']} 笔"
          f" | 其中兜底成本位+1分 {stats['trail_breakeven_dummy']} 笔"
          f" ({stats['trail_breakeven_dummy']/max(stats['trail_trigger'],1):.0%})")
    tt = stats["trail_trigger"]
    if tt:
        print(f"  触发后 3 日内被打: {stats['trail_hit_3d']} ({stats['trail_hit_3d']/tt:.0%})"
              f" | 3 日后才打/未打: {stats['trail_hit_later']}")
        print(f"  移动获利笔砍肉: 基线总R {stats['trail_base_r']:+.1f} → 实际 {stats['trail_act_r']:+.1f}"
              f" | 损失 {stats['trail_loss']:+.1f}R")
    print(f"平保触发: {stats['breakeven_trigger']} 笔 | 主动出场: {stats['active_trigger']} 笔"
          f" | TTP: {stats['ttp_trigger']} 笔")
    print("\n样例 (code/信号日/进场/新止损/3日被打/基线R/实际R/出场):")
    for s in samples:
        print(f"  {s[0]} {s[1]} 进{s[2]:.2f} 止损{s[3]} 打={s[4]} 基线{s[5]:+.2f}R → {s[6]:+.2f}R [{s[7]}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
