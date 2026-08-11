#!/usr/bin/env python3
"""R-054 B：四层面出场体系 vs 基线对照（2026-08-11 · 交易部审核 P1-5/6/7 吸收）

问题：C5 简化版（低点×0.99）已证无效（−79.6R/误伤 83.9%）；exit_manager 精细版
（1R 平保 + 移动获利三要素 + TTP 36% + 主动出场）未回测。本脚本对照：
  基线（tracking 关版：止损 + hold 末收盘） vs 开版（evaluate_exit 四层面逐日评估）
定案范围 = "四层面体系整体是否值得作为模拟线执行规则"（审核 P1-5 允许明确定案范围）。

口径（审核 P1-6 吸收）：
  - prebreak 触发笔（triggered_20d==1），触发价进场，hold=20
  - 开版逐日 evaluate_exit：df 传触发日切片（P0-3 契约），pos.highest_price 跨日自维护
    （P0-4：极值持久化由脚本承担），stop_update 落 current_stop（层面2/3/4 只升不降）
  - 成本：_trade_cost（佣金万1.3+印花税万5）同基线；R 分母 = 原始每股风险（trigger−stop）
  - 误伤 = 开版提前出场（stopped/主动/止盈）且基线未出场 且 基线 R>0
  - 同源自检（P1-7）：关版模式（仅止损）用 tracking.track_signal 重算 vs CSV r_20d
    （容差 价 0.01 / R 1e-3，同 C5）
判定：20d 累计 R 净变化（开−关）>0 且误伤率 ≤50% → 接入（模拟线层面2/3/4 全部落库）
"""
from __future__ import annotations

import argparse
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
from 回测系统.tracking import Signal, _find_signal_index, _trade_cost, track_signal  # noqa: E402

# 用 T8 复跑信号集（2026-08-11 数据更新+xdxr 修正后同源重跑）——基线 r_20d 与
# 当前库数据口径一致，自检才能通过（R-043 旧信号集数据截止 08-10，复权已变）
SIG = _ROOT / "产出" / "输出" / "backtest_r43_t2_T8" / "signals.csv"
HOLD = 20


def load_signal_df() -> pd.DataFrame:
    sig = pd.read_csv(SIG, dtype={"code": str})
    return sig[sig["triggered_20d"] == 1]


def selfcheck(sig_df: pd.DataFrame) -> list[str]:
    """同源自检：tracking 关版重算 vs CSV r_20d（容差价 0.01/R 1e-3）"""
    fails = []
    checked = 0
    for _, row in sig_df.iterrows():
        df = read_kline(row["code"], shared=True)
        if df is None or df.empty:
            continue
        oc = track_signal(Signal(code=row["code"], date=pd.Timestamp(row["date"]),
                                 mode="prebreak", grade=row["grade"], scores={},
                                 close=row["close"], trigger=row["trigger"],
                                 stop=row["stop"], risk=row["risk"]),
                          df, HOLD, enable_cost=True, phase_in=True)
        if not oc.triggered:
            continue
        r_recalc = oc.r if oc.r is not None else 0.0
        if abs(r_recalc - row["r_20d"]) > 1e-3 or abs(oc.exit_price - row["exit_20d"]) > 0.01:
            fails.append(f"{row['code']} {row['date']}: 重算 {r_recalc:.4f}/{oc.exit_price} "
                         f"vs CSV {row['r_20d']:.4f}/{row['exit_20d']}")
        checked += 1
    return fails, checked


def run_open(sig_df: pd.DataFrame) -> list[dict]:
    """开版：0.5R 确认（delay2，同基线）→ 确认后四层面逐日评估（基线=止损+hold收盘）。
    对照变量纯净：确认 reject/stopped 路径两版同 R，只确认补仓后的出场不同（P1-5）。"""
    from 分析决策.分析.indicators import half_position_confirm_delay2

    results = []
    for _, row in sig_df.iterrows():
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
        # 窗口与基线同口径：信号日后 hold 交易日（基线 _track_prebreak end=t+hold，
        # 不是触发日后——此前用 trig_idx+1+HOLD 导致开版持有期更长，对照不公平）
        end = min(sig_idx + 1 + HOLD, len(df))
        # 0.5R 确认（delay2，窗口内；同基线 _phase_in_track 语义）
        verdict = half_position_confirm_delay2(df, entry, stop, trig_idx + 1, max_idx=end)
        used = verdict["conf_idx_used"]
        used_safe = min(used, len(dates) - 1)          # delay2 二判可返回窗口末边界
        if verdict["stopped"]:
            exit_price, exit_date, exit_reason = stop, str(dates[used_safe])[:10], "止损(确认期)"
            stopped = True
        elif verdict["reject"]:
            exit_price = float(verdict["close"])
            exit_date = str(dates[used_safe])[:10]
            exit_reason = "收线未确认平仓(同基线)"
            stopped = False
        else:
            # 确认补仓 → 四层面逐日评估（替换基线 hold 收盘）
            pos = Position(symbol=row["code"], direction="long", market="stock",
                           entry_price=entry, initial_stop=stop, current_stop=stop,
                           volume=100, grade_at_entry=str(row["grade"]))
            pos.highest_price = entry
            pos.lowest_price = entry
            exit_price, exit_date, exit_reason = None, None, ""
            stopped = False
            for j in range(used + 1, end):
                day_df = df.iloc[trig_idx:j + 1]      # 触发日切片（P0-3 契约）
                pos.highest_price = max(pos.highest_price, float(highs[j]))
                pos.lowest_price = min(pos.lowest_price, float(lows[j]))
                v = evaluate_exit(pos, day_df)
                if v["stop_update"] and v["stop_update"] > pos.current_stop:
                    pos.current_stop = v["stop_update"]   # 层面2/3/4 只升不降落库
                if v["should_exit"]:
                    exit_price = v["exit_price"] or float(closes[j])
                    exit_date = str(dates[j])[:10]
                    exit_reason = v["reason"]
                    stopped = "止损" in exit_reason or "层面1" in exit_reason
                    break
            if exit_price is None:
                exit_price = float(closes[min(end - 1, len(closes) - 1)])
                exit_date = str(dates[min(end - 1, len(dates) - 1)])[:10]
                exit_reason = "hold_end(四层面)"
        cost = _trade_cost(entry, exit_price, True, 1.0)
        r = (exit_price - entry - cost) / risk if risk > 0 else 0.0
        results.append({"code": row["code"], "date": row["date"], "entry": entry,
                        "exit": exit_price, "exit_date": exit_date, "reason": exit_reason,
                        "r": r, "r_base": float(row["r_20d"]),
                        "stopped": stopped, "exit_stop": stop})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck-only", action="store_true")
    ap.add_argument("--out", default="产出/输出/实验/r54")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sig_df = load_signal_df()
    print(f"触发笔: {len(sig_df)}")
    fails, checked = selfcheck(sig_df)
    print(f"[自检] 重算 {checked} 笔 | 不一致 {len(fails)}")
    if fails:
        print("❌ 自检失败（基线重算与 CSV 不一致）——禁止继续：")
        for f in fails[:5]:
            print(f"  {f}")
        return 2
    print("✅ 自检通过（tracking 关版重算与 CSV r_20d 一致）")
    if args.selfcheck_only:
        return 0

    res = run_open(sig_df)
    import statistics as st

    rs_open = [r["r"] for r in res]
    rs_base = [r["r_base"] for r in res]
    n = len(rs_open)
    sum_open, sum_base = sum(rs_open), sum(rs_base)
    win_open = sum(1 for r in rs_open if r > 0) / n
    win_base = sum(1 for r in rs_base if r > 0) / n
    # 误伤：开版提前出场（非 hold_end）且基线未出场（r_base 非止损路径≈基线的
    # stopped 列——用基线 stopped_20d）且基线 R>0
    early = [r for r in res if r["reason"] != "hold_end"
             and not r["stopped"]]
    # 精确误伤需基线 stopped_20d 列
    sig_map = {f"{r['code']}_{r['date']}": r for _, r in sig_df.iterrows()}
    hurt, early_n = 0, 0
    for r in res:
        base = sig_map.get(f"{r['code']}_{r['date']}")
        if base is None:
            continue
        base_stopped = bool(base["stopped_20d"])
        if r["reason"] != "hold_end" and not base_stopped:
            early_n += 1
            if base["r_20d"] > 0:
                hurt += 1
    hurt_rate = hurt / early_n if early_n else 0.0
    dd_open = _max_dd(rs_open)
    dd_base = _max_dd(rs_base)

    lines = [
        "# R-054 B：四层面出场体系 vs 基线对照（2026-08-11）",
        "",
        f"- 样本：r43_t2 触发笔 {n}（prebreak S 级，hold 20d，触发价进，成本开启）",
        f"- 自检：tracking 关版重算 {checked} 笔一致 ✅（P1-7）",
        "",
        "| 指标 | 基线（止损+hold收盘） | 开版（四层面） | 净变化 |",
        "|---|---|---|---|",
        f"| 累计 R | {sum_base:+.1f} | {sum_open:+.1f} | {sum_open - sum_base:+.1f} |",
        f"| 平均 R | {st.mean(rs_base):+.4f} | {st.mean(rs_open):+.4f} | {st.mean(rs_open) - st.mean(rs_base):+.4f} |",
        f"| 胜率 | {win_base:.1%} | {win_open:.1%} | {win_open - win_base:+.1%} |",
        f"| 最大回撤(R曲线) | {dd_base:.1f}R | {dd_open:.1f}R | {dd_open - dd_base:+.1f}R |",
        "",
        f"- 提前出场样本 {early_n} 笔（基线未止损）| 误伤（基线本会赢）{hurt} 笔 | **误伤率 {hurt_rate:.1%}**",
        "",
        "**判定**（C5 同规则：累计 R 净变化>0 且误伤率≤50% → 接入）：",
    ]
    ok = (sum_open - sum_base) > 0 and hurt_rate <= 0.5
    lines.append(f"- {'✅ 接入：四层面体系整体有效，模拟线层面2/3/4 全量落库' if ok else '❌ 不接入：四层面整体无效，模拟线维持现状（仅层面2 平保可单独评估）'}")
    report = "\n".join(lines) + "\n"
    (out_dir / "r54_trail_compare.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


def _max_dd(rs: list[float]) -> float:
    cur = peak = 0.0
    dd = 0.0
    for r in rs:
        cur += r
        peak = max(peak, cur)
        dd = max(dd, peak - cur)
    return dd


if __name__ == "__main__":
    raise SystemExit(main())
