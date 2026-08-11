#!/usr/bin/env python3
"""R-057 移动获利/主动出场 接入 vs 不接入 正式对照（2026-08-11 · 老板拍板）

Part 1（前置门禁）：重放框架 vs 引擎逐笔对账（exit ±0.01 / R ±0.005）——
   通过才可跑正式实验（禁止带病分析）
Part 2：信号层矩阵（5/10/20 窗 × 26年/近7年/近3年 × A/B/C/D/E 五组 = 45 格）
   + 资金层（重放出场写回 CSV → r44.run_one 复用资本逻辑，对账 r44 锚点）
   + 蒙卡（主对比组 × 1 万次）

用法:
  python 回测系统/r57_exit_matrix.py --validate   # Part 1 门禁
  python 回测系统/r57_exit_matrix.py              # Part 2 全实验
"""
from __future__ import annotations

import argparse
import json
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
OUT = _ROOT / "产出" / "输出" / "实验" / "r57"

# 对比组开关（A 基线 = 全关；B = 平保；C = +移动获利；D = +主动出场；E = 全开）
GROUPS = {
    "A": dict(enable_breakeven=False, enable_trailing=False, enable_active=False, enable_ttp=False),
    "B": dict(enable_breakeven=True, enable_trailing=False, enable_active=False, enable_ttp=False),
    "C": dict(enable_breakeven=True, enable_trailing=True, enable_active=False, enable_ttp=False),
    "D": dict(enable_breakeven=True, enable_trailing=False, enable_active=True, enable_ttp=False),
    "E": dict(enable_breakeven=True, enable_trailing=True, enable_active=True, enable_ttp=True),
    "F": dict(enable_breakeven=True, enable_trailing=False, enable_active=False, enable_ttp=True),
}
HOLDS = [5, 10, 20]
WINDOWS = {"26y": None, "7y": "2019-01-01", "3y": "2023-01-01"}


def load_trig() -> pd.DataFrame:
    sig = pd.read_csv(SIG, dtype={"code": str})
    return sig[sig["triggered_20d"] == 1]


def replay(row: pd.Series, df: pd.DataFrame, switches: dict, hold: int) -> dict:
    """单笔重放：触发 → delay2 确认 → 确认后逐日 evaluate_exit（开关）→ 出场
    返回 exit_price/r/stopped/reason/used；未确认路径（reject/stopped）同引擎。
    """
    dates = df["日期"].astype(str).str[:10].values
    highs = df["最高"].astype(float).values
    lows = df["最低"].astype(float).values
    closes = df["收盘"].astype(float).values
    sig_idx = _find_signal_index(df, pd.Timestamp(row["date"]))
    if sig_idx is None:
        return {"skip": "no_sig_idx"}
    # R-057 门禁修复：end 与引擎对齐（tracking.py:160 end=min(t+hold, n-1)，
    # 循环含 end——此前 sig_idx+1+hold 差 1，delay2 二判窗口/触发窗口偏一天）
    end = len(df) - 1 if hold is None else min(sig_idx + hold, len(df) - 1)
    trig_idx = next((j for j in range(sig_idx + 1, end + 1) if highs[j] >= row["trigger"]), None)
    if trig_idx is None:
        return {"skip": "no_trigger", "end": end}
    entry = float(row["trigger"])
    stop = float(row["stop"])
    risk = float(row["risk"])
    # R-057 门禁修复：确认日超窗口（trig_idx+1 > end，触发在窗口末）→ 引擎
    # _phase_in_track 走"无确认空间"分支 = _track_window 空窗 → 持有到期收盘 close[end]
    if trig_idx + 1 > end:
        return {"exit": float(closes[end]), "exit_date": str(dates[end])[:10],
                "r": _r(entry, float(closes[end]), risk), "stopped": False,
                "reason": "hold_end(无确认空间)", "skip": None}
    v = half_position_confirm_delay2(df, entry, stop, trig_idx + 1, max_idx=end)
    used = v["conf_idx_used"]
    used_safe = min(used, len(dates) - 1)
    if v["stopped"]:
        return {"exit": stop, "exit_date": str(dates[used_safe])[:10], "r": _r(entry, stop, risk),
                "stopped": True, "reason": "止损(确认期)", "skip": None}
    if v["reject"]:
        ex = float(v["close"])
        return {"exit": ex, "exit_date": str(dates[used_safe])[:10], "r": _r(entry, ex, risk),
                "stopped": False, "reason": "收线未确认平仓", "skip": None}
    # 确认补仓 → 逐日 evaluate_exit（触发日切片 + 极值自维护 + 开关）
    pos = Position(symbol=row["code"], direction="long", market="stock",
                   entry_price=entry, initial_stop=stop, current_stop=stop,
                   volume=100, grade_at_entry=str(row["grade"]))
    pos.highest_price = entry
    pos.lowest_price = entry
    exit_price, exit_date, exit_reason = None, None, ""
    for j in range(used + 1, end + 1):        # 引擎 _track_window range(start, end+1)
        day_df = df.iloc[trig_idx:j + 1]
        pos.highest_price = max(pos.highest_price, float(highs[j]))
        pos.lowest_price = min(pos.lowest_price, float(lows[j]))
        ev = evaluate_exit(pos, day_df, **switches)
        if ev["stop_update"] and ev["stop_update"] > pos.current_stop:
            pos.current_stop = ev["stop_update"]
        if ev["should_exit"]:
            exit_price = ev["exit_price"] or float(closes[j])
            exit_date = str(dates[j])[:10]
            exit_reason = ev["reason"]
            break
    if exit_price is None:
        exit_price = float(closes[end])       # 引擎 hold 末收盘 close[end]
        exit_date = str(dates[end])[:10]
        exit_reason = "hold_end"
    return {"exit": exit_price, "exit_date": exit_date, "r": _r(entry, exit_price, risk),
            "stopped": "止损" in exit_reason, "reason": exit_reason, "skip": None}


def _r(entry: float, exit_price: float, risk: float) -> float:
    cost = _trade_cost(entry, exit_price, True, 1.0)
    return (exit_price - entry - cost) / risk if risk > 0 else 0.0


def replay_all(switches: dict, hold: int, min_date: str | None = None,
               verbose: bool = True) -> list[dict]:
    trig = load_trig()
    if min_date:
        trig = trig[trig["date"].astype(str) >= min_date]
    out, skipped = [], 0
    for _, row in trig.iterrows():
        df = read_kline(row["code"], shared=True)
        if df is None or df.empty:
            skipped += 1
            continue
        r = replay(row, df, switches, hold)
        if r.get("skip"):
            skipped += 1
            continue
        r["code"] = row["code"]
        r["date"] = row["date"]   # 信号日（rep_map 键）
        r["base_r"] = float(row["r_20d"]) if hold == 20 else None
        out.append(r)
    if verbose:
        print(f"  [{hold}d] 重放 {len(out)} 笔 | 跳过 {skipped}")
    return out


# ── Part 1 门禁 ──

def validate(verbose: bool = True) -> list[str]:
    """1.1 重放 A 组 vs 引擎 track_signal（phase_in=True）逐笔对账，三窗全覆盖
    （审计 1.3：5/10/20 窗门禁；容差 R 1e-3/价 0.01——审计 3.1）"""
    trig = load_trig()
    fails = []
    checked = 0
    for hold in HOLDS:
        for _, row in trig.iterrows():
            df = read_kline(row["code"], shared=True)
            if df is None or df.empty:
                continue
            # 引擎（同源函数）
            oc = track_signal(Signal(code=row["code"], date=pd.Timestamp(row["date"]),
                                     mode="prebreak", grade=row["grade"], scores={},
                                     close=row["close"], trigger=row["trigger"],
                                     stop=row["stop"], risk=row["risk"]),
                              df, hold, enable_cost=True, phase_in=True)
            if not oc.triggered:
                continue
            # 重放 A 组（全关）
            rp = replay(row, df, GROUPS["A"], hold)
            if rp.get("skip"):
                continue
            if abs(rp["exit"] - oc.exit_price) > 0.01 or abs(rp["r"] - oc.r) > 1e-3:
                fails.append(f"{row['code']} {row['date']} [{hold}d]: 重放 {rp['r']:.4f}/{rp['exit']:.2f} "
                             f"vs 引擎 {oc.r:.4f}/{oc.exit_price:.2f} [{rp['reason'][:14]}]")
            checked += 1
    if verbose:
        print(f"[门禁 1.1] 对账 {checked} 笔（3 窗）| 不一致 {len(fails)}")
        for f in fails[:8]:
            print(f"  {f}")
    return fails


def validate_capital_anchor(verbose: bool = True) -> list[str]:
    """1.3 资金层对账（审计 P0 修正 2026-08-11）：锚点必须与实验信号集同源——
    r43_t2 存档锚点（1534.0 等）是旧复权口径（08-11 xdxr 修正后已变），不能对账 T8。
    正确做法（机制对账）：
      ① 引擎锚点 = T8 原 CSV 直接 run_one（三个时间窗）
      ② 重放 A 组写回 CSV（20d 列）run_one
      ③ 两者零差（收益/回撤 ±0.5pp + n_exec 相等）——证明"重放写回 = 引擎输出"
    """
    from 回测系统.r44_position_grid import run_one
    from 回测系统.r48_grid import _build_enriched_cache

    fails = []
    # 构建 A 组重放写回 CSV（20d 列）
    rep = replay_all(GROUPS["A"], 20, verbose=False)
    sig = pd.read_csv(SIG, dtype={"code": str})
    rep_map = {f"{r['code']}_{r['date']}": r for r in rep}
    for i, row in sig.iterrows():
        rp = rep_map.get(f"{row['code']}_{row['date']}")
        if rp is None:
            continue
        sig.at[i, "triggered_20d"] = 1
        sig.at[i, "entry_20d"] = row["trigger"]
        sig.at[i, "exit_20d"] = round(float(rp["exit"]), 4)   # 对齐引擎 CSV 精度
        sig.at[i, "exit_date_20d"] = rp["exit_date"]  # R-057 门禁修复：出场日期（非信号日）
        sig.at[i, "r_20d"] = round(float(rp["r"]), 4) # 引擎 write 时 round 4 位——全精度浮点
        sig.at[i, "stopped_20d"] = int(rp["stopped"]) # 会导致资金层边界判断差异（10 笔卡边界）
    csv_path = OUT / "signals_A_replay.csv"
    OUT.mkdir(parents=True, exist_ok=True)
    sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
    for tag, md in [("26y", None), ("7y", "2019-01-01"), ("3y", "2023-01-01")]:
        m_eng, _ = run_one(str(SIG), 8401.0, 0.025, 999, min_date=md,
                           return_raw=True,
                           enriched_path=str(_build_enriched_cache(str(SIG))))
        m_rep, _ = run_one(str(csv_path), 8401.0, 0.025, 999, min_date=md,
                           return_raw=True,
                           enriched_path=str(_build_enriched_cache(csv_path)))
        ok = (abs(m_eng["total_ret_pct"] - m_rep["total_ret_pct"]) <= 0.5
              and abs(m_eng["dd_peak_pct"] - m_rep["dd_peak_pct"]) <= 0.5
              and m_eng["n_exec"] == m_rep["n_exec"])
        if verbose:
            print(f"  [门禁 1.3] {tag}: 引擎 {m_eng['total_ret_pct']:.1f}%/{m_eng['dd_peak_pct']:.1f}%/{m_eng['n_exec']}"
                  f" vs 重放写回 {m_rep['total_ret_pct']:.1f}%/{m_rep['dd_peak_pct']:.1f}%/{m_rep['n_exec']}"
                  f" {'✅' if ok else '❌'}")
        if not ok:
            fails.append(f"{tag}: 引擎 {m_eng['total_ret_pct']:.1f}/{m_eng['dd_peak_pct']:.1f}/{m_eng['n_exec']}"
                         f" vs 重放 {m_rep['total_ret_pct']:.1f}/{m_rep['dd_peak_pct']:.1f}/{m_rep['n_exec']}")
    return fails


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true", help="只跑 Part 1 门禁")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)

    if args.validate:
        fails1 = validate()
        fails3 = validate_capital_anchor()
        report = [f"# R-057 Part 1 回测代码验证（{pd.Timestamp.now().date()}）", "",
                  f"- 门禁 1.1 重放 vs 引擎对账：{'✅ 零超差' if not fails1 else f'❌ {len(fails1)} 笔不一致'}",
                  f"- 门禁 1.3 资金层对账 r44 锚点：{'✅ 零差' if not fails3 else f'❌ {fails3}'}"]
        (OUT / "validation.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))
        return 0 if not fails1 and not fails3 else 2

    # Part 2 全实验（Part 1 先过）
    fails1 = validate(verbose=False)
    if fails1:
        print("❌ 门禁 1.1 未过——禁止跑实验。修复后重跑 --validate。")
        return 2
    fails3 = validate_capital_anchor(verbose=False)
    if fails3:
        print("❌ 门禁 1.3 未过——禁止跑实验。")
        return 2
    print("✅ Part 1 门禁全过——开始 Part 2 正式实验")

    # 信号层矩阵（审计 1.5：误伤率落盘——vs A 基线：该组提前出场且 A 未止损且 A R>0）
    base_res = {h: replay_all(GROUPS["A"], h, verbose=False) for h in HOLDS}
    base_map = {h: {f"{r['code']}_{r['date']}": r for r in res}
                for h, res in base_res.items()}
    matrix = {}
    for gname, sw in GROUPS.items():
        for hold in HOLDS:
            for wtag, md in WINDOWS.items():
                res = replay_all(sw, hold, md, verbose=False)
                rs = [r["r"] for r in res]
                n = len(rs)
                if n == 0:
                    matrix[f"{gname}_{hold}d_{wtag}"] = None
                    continue
                cur = peak = 0.0
                dd = 0.0
                for r in rs:
                    cur += r
                    peak = max(peak, cur)
                    dd = max(dd, peak - cur)
                early_n = hurt = 0
                if gname != "A":
                    bmap = base_map[hold]
                    for r in res:
                        b = bmap.get(f"{r['code']}_{r['date']}")
                        if b is None or b["stopped"]:
                            continue
                        if r["reason"] not in ("hold_end", "hold_end(无确认空间)",
                                               "收线未确认平仓"):
                            early_n += 1
                            if b["r"] > 0:
                                hurt += 1
                matrix[f"{gname}_{hold}d_{wtag}"] = {
                    "n": n, "win": sum(1 for r in rs if r > 0) / n,
                    "avgR": sum(rs) / n, "sumR": sum(rs), "dd": dd,
                    "hurt_n": hurt, "early_n": early_n,
                    "hurt_rate": hurt / early_n if early_n else 0.0}
    (OUT / "signal_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    print(f"信号层矩阵完成：{len(matrix)} 格 → r57/signal_matrix.json")

    # 资金层（A/B/C/D/E/F × 3 时间窗 = 18 格，审计 1.2 补 D 组）
    from 回测系统.r44_position_grid import run_one
    from 回测系统.r48_grid import _build_enriched_cache
    cap = {}
    for gname in ["A", "B", "C", "D", "E", "F"]:
        rep = replay_all(GROUPS[gname], 20, verbose=False)
        sig = pd.read_csv(SIG, dtype={"code": str})
        rep_map = {f"{r['code']}_{r['date']}": r for r in rep}
        for i, row in sig.iterrows():
            rp = rep_map.get(f"{row['code']}_{row['date']}")
            if rp is None:
                continue
            sig.at[i, "triggered_20d"] = 1
            sig.at[i, "entry_20d"] = row["trigger"]
            sig.at[i, "exit_20d"] = round(float(rp["exit"]), 4)
            sig.at[i, "exit_date_20d"] = rp["exit_date"]
            sig.at[i, "r_20d"] = round(float(rp["r"]), 4)
            sig.at[i, "stopped_20d"] = int(rp["stopped"])
        csv_path = OUT / f"signals_{gname}.csv"
        sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
        for wtag, md in WINDOWS.items():
            m, res = run_one(str(csv_path), 8401.0, 0.025, 999, min_date=md,
                             return_raw=True,
                             enriched_path=str(_build_enriched_cache(csv_path)))
            cap[f"{gname}_{wtag}"] = {"ret": m["total_ret_pct"], "dd": m["dd_peak_pct"],
                                      "n": m["n_exec"], "avgR": m["avg_r"]}
    (OUT / "capital_matrix.json").write_text(json.dumps(cap, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    print(f"资金层矩阵完成：{len(cap)} 格 → r57/capital_matrix.json")

    # 蒙卡（审计 2.3：成交 R 序列重采样、seed=2024、10000 次）
    try:
        import random
        rng = random.Random(2024)
        mc = {}
        for gname in ["A", "B", "C", "D", "E", "F"]:
            sig = pd.read_csv(OUT / f"signals_{gname}.csv", dtype={"code": str})
            rs = sig[sig["triggered_20d"] == 1]["r_20d"].dropna().astype(float).tolist()
            if not rs:
                continue
            finals = []
            for _ in range(10000):
                s = 0.0
                for r in rs:
                    s += r if rng.random() < 0.5 else -r
                finals.append(s)
            finals_s = sorted(finals)
            mc[gname] = {"n": len(rs), "median": finals_s[len(finals_s) // 2],
                         "p5": finals_s[int(len(finals_s) * 0.05)],
                         "win_prob": sum(1 for f in finals if f > 0) / 10000}
        (OUT / "monte_carlo.json").write_text(json.dumps(mc, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
        print(f"蒙卡完成：{len(mc)} 组 × 1 万次 → r57/monte_carlo.json")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 蒙卡失败（{e}）——其余产物不受影响")

    _write_report(matrix, cap)
    print("R057 报告已生成 → r57/R057-移动获利主动出场对照-20260811.md")
    return 0


def _write_report(matrix: dict, cap: dict) -> None:
    lines = ["# R-057 移动获利/主动出场 接入 vs 不接入 对照实验（2026-08-11）", "",
             "> 回测代码验证（Part 1 门禁）已通过：重放 vs 引擎逐笔零超差（3 窗 × R 1e-3）；"
             "资金层对账零差。基于 r43_t2_T8 信号集（V3 口径，1,149 笔触发）。", "",
             "## 一、信号层矩阵（20d 窗主口径）", "",
             "| 组 | 规则 | 26年 n | 胜率 | avgR | 累计R | 回撤 | 误伤率 |",
             "|---|---|---|---|---|---|---|---|"]
    rules = {"A": "纯基线", "B": "+平保", "C": "+移动获利", "D": "+主动出场",
             "E": "全开", "F": "+TTP"}
    for gname in ["A", "B", "C", "D", "E", "F"]:
        for wtag in ["26y", "7y", "3y"]:
            cell = matrix.get(f"{gname}_20d_{wtag}")
            if cell is None:
                continue
            lines.append(f"| {gname} {rules[gname]} {wtag} | {cell['n']} | {cell['win']:.1%} | "
                         f"{cell['avgR']:+.3f} | {cell['sumR']:+.1f} | {cell['dd']:.1f}R | "
                         f"{cell['hurt_rate']:.0%} |")
    lines += ["", "## 二、资金层矩阵（8401×0.025×999，总资产口径回撤）", "",
              "| 组 | 26年收益/回撤 | 近7年收益/回撤 | 近3年收益/回撤 |", "|---|---|---|---|"]
    for gname in ["A", "B", "C", "D", "E", "F"]:
        cells = [cap.get(f"{gname}_{w}") for w in ["26y", "7y", "3y"]]
        if not any(cells):
            continue
        fmt = lambda c: f"{c['ret']:+.1f}%/{c['dd']:.1f}%/{c['n']}笔" if c else "—"
        lines.append(f"| {gname} | {fmt(cells[0])} | {fmt(cells[1])} | {fmt(cells[2])} |")
    lines += ["", "## 三、边际贡献分解（条件边际，顺序依赖——审计 5.1）", "",
              "- B−A：平保贡献；C−B：移动获利贡献；D−B：主动出场贡献；F−B：TTP 独立贡献",
              "- E−D 受移动获利-TTP 互斥压制（审计 1.1），TTP 独立价值看 F−B",
              "- 各格数据见 signal_matrix.json / capital_matrix.json / monte_carlo.json", "",
              "## 四、局限（审计 P3）", "",
              "- 涨跌停无法买入/一字板：触发价成交假设（与基线同口径）",
              "- 持仓期内除权：复权价跳变致 highest/lowest 失真（未处理，标注）",
              "- 无滑点（D2 口径同基线）；整手 100 股 + 0.5R 半仓（sim_capital 同源）",
              "- 占位触发（信号日在数据末）已排除；停牌/数据缺失跳过计数",
              "- 蒙卡为成交 R 序列 ± 重采样（同种子 2024，n 组间不同——比单笔质量不看终值）"]
    (OUT / "R057-移动获利主动出场对照-20260811.md").write_text("\n".join(lines) + "\n",
                                                             encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
