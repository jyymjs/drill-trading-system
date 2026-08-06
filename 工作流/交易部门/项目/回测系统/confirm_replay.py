#!/usr/bin/env python3
"""0.5R 确认规则质量验证 + 大赢家归因（2026-08-06 老板确认四连包 ③④ · 纯分析）

③ 确认规则质量验证（历史回放）：对 backtest_final signals（prebreak/20d 触发
   514 笔）按 0.5R 起步回放 half_position_confirm 判定：
   - 确认率：收线确认（→ 补 0.5R 至 1R）的笔数占比
   - 误杀率：被「不确认平仓」的票中，平仓后 20 天内最高涨幅 ≥ 1R 的比例
     （口径：平仓价 = 确认日收盘；后 20 根 K 线最高价 vs 平仓价涨幅 ≥ 单笔风险）
     —— 误杀 = 规则把后续能走出 ≥1R 的强势票挡在门外
   - 漏补率：确认补仓的票中，补仓后 20 天内最高涨幅 ≥ 1R 的比例
     （口径同误杀，衡量「确认补仓」的正确率——补对了多少）
   判定规则单一来源：indicators.phase_confirm_from_kline（触发日定位与回测层
   tracking._track_prebreak 同规则；确认判定 half_position_confirm 同源，不复制）。
   合格线：误杀 <10% 合格；>20% 需调规则（2026-08-06 老板确认四连包③口径）。

④ phase_in 前后档位对比：backtest_c23_20260806（phase_in 关）vs
   backtest_final_20260806（phase_in 开）触发信号 R 档位分布——
   10R+ 占比 / 最大单笔 R 占累计 R 比例 / 平均 R / 中位数 / 胜率 / 亏损比。
   回答：0.5R 分步是否加剧「依赖大赢家」（预期：非主因，样本量才是——如实记录）。

用法:
    python 项目/回测系统/confirm_replay.py [--signals-final 产出/输出/backtest_final_20260806/signals.csv]
        [--signals-c23 产出/输出/backtest_c23_20260806/signals.csv]
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                        # 项目/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))       # 交易部根

import pandas as pd

DEFAULT_FINAL = os.path.join("产出", "输出", "backtest_final_20260806", "signals.csv")
DEFAULT_C23 = os.path.join("产出", "输出", "backtest_c23_20260806", "signals.csv")

_CN_COLS = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]


def load_kline_cache(codes: list[str]) -> dict[str, pd.DataFrame]:
    """批量 K 线读取（duckdb 单连接只读，缓存复用）——③回放数据源

    数据只读（duckdb read_only）；与 fetcher 主链路同库同口径（qfq 前复权）。
    """
    from 数据基础.duckdb.config import DB_PATH
    from 数据基础.duckdb.reader import (
        _to_cn_kline,
        compute_qfq,
        open_db,
        read_daily_raw,
        read_xdxr,
    )

    out: dict[str, pd.DataFrame] = {}
    con = open_db(DB_PATH, read_only=True)
    try:
        for c in codes:
            # signals 历史格式 code 去前导零（如 2074 → 002074）；duckdb symbol 恒 6 位
            sym = str(c).zfill(6)
            daily = read_daily_raw(con, sym)
            if daily is None or daily.empty:
                continue
            try:
                k = compute_qfq(daily, read_xdxr(con, sym))
                out[c] = _to_cn_kline(k)
            except Exception:  # noqa: BLE001 - 单只失败不阻断批量
                continue
    finally:
        con.close()
    # 库外个股（停牌/新股等）不回退网络——回放分析允许少量 no_data，
    # 缺失数在报告中如实标注（纯分析，不因数据缺失引入网络耗时/噪音）
    return out


def _post_exit_high(df: pd.DataFrame, from_date: str, window: int = 20) -> float:
    """from_date 之后 window 根 K 线的最高价（不含 from_date 当日；数据不足返回 0）"""
    idx = _first_idx_after(df, from_date)
    if idx is None:
        return 0.0
    seg = df["最高"].values[idx:idx + window]
    return float(seg.max()) if len(seg) else 0.0


def _first_idx_after(df: pd.DataFrame, from_date: str) -> int | None:
    """from_date 之后第一根 K 线的索引（严格大于；不存在返回 None）"""
    dates = df["日期"].astype(str).str[:10].values
    for i, d in enumerate(dates):
        if d > from_date:
            return i
    return None


def _post_close_nth(df: pd.DataFrame, from_date: str, window: int = 20) -> float:
    """from_date 之后第 window 根 K 线收盘价（不足 → 最后一根收盘；无后续 → 0）"""
    idx = _first_idx_after(df, from_date)
    if idx is None:
        return 0.0
    closes = df["收盘"].values
    j = min(idx + window - 1, len(closes) - 1)
    return float(closes[j])


def replay_confirm(signals_df: pd.DataFrame, klines: dict[str, pd.DataFrame],
                   mode: str = "prebreak", hold: str = "20d") -> dict:
    """③ 确认规则质量回放：确认率 / 误杀率 / 漏补率

    Args:
        signals_df: backtest_final signals.csv 全量
        klines: code -> 日K线（load_kline_cache 产物）
        mode/hold: 触发集口径（与资金模拟一致：prebreak/20d 触发）

    Returns:
        dict: 全指标 + 明细 DataFrame（code/date/entry/risk/verdict/确认日/20日高点）
    """
    from 分析决策.分析.indicators import phase_confirm_from_kline

    h = int(str(hold).replace("d", ""))
    sub = signals_df[(signals_df["mode"] == mode)
                     & (signals_df[f"triggered_{h}d"] == 1)].copy()
    sub = sub.sort_values(["date", "code"])

    rows = []
    for _, r in sub.iterrows():
        code = str(r["code"])
        k = klines.get(code)
        if k is None or len(k) < 2:
            rows.append({"code": code, "date": str(r["date"])[:10], "grade": r["grade"],
                         "entry": float(r[f"entry_{h}d"]), "risk": float(r.get("risk", 0) or 0),
                         "verdict": "no_data", "confirm_date": "", "reason": "K线不可得"})
            continue
        entry = float(r[f"entry_{h}d"])
        # 止损价：signals 的 stop 列（引擎输出）优先，回退 stop_loss/0
        stop_raw = r.get("stop")
        if stop_raw is None or str(stop_raw) in ("", "nan", "None"):
            stop_raw = r.get("stop_loss")
        try:
            stop = float(stop_raw) if stop_raw is not None and str(stop_raw) not in ("", "nan", "None") else 0.0
        except (TypeError, ValueError):
            stop = 0.0
        v = phase_confirm_from_kline(k, str(r["date"])[:10], entry, stop)
        if v["wait"]:
            rows.append({"code": code, "date": str(r["date"])[:10], "grade": r["grade"],
                         "entry": entry, "risk": float(r.get("risk", 0) or 0),
                         "verdict": "wait", "confirm_date": "", "reason": v["reason"]})
            continue
        verdict = ("stop" if v["stopped"] else "confirm" if v["confirmed"] else "reject")
        cdate = v["confirm_date"] or str(r["date"])[:10]
        # 平仓后 / 补仓后 20 天内最高涨幅（元）： vs 平仓价（确认日收盘）或补仓价
        post_high = _post_exit_high(k, cdate, window=20)
        # 持有 20 天近似 R（机会成本口径）：确认日之后第 20 根收盘 vs 进场价
        post_close20 = _post_close_nth(k, cdate, window=20)
        rows.append({
            "code": code, "date": str(r["date"])[:10], "grade": r["grade"],
            "entry": entry, "risk": float(r.get("risk", 0) or 0),
            "verdict": verdict, "confirm_date": cdate,
            "close": float(v["close"]), "post_high_20d": post_high,
            "post_close_20d": post_close20,
            "reason": v["reason"],
        })

    det = pd.DataFrame(rows)
    n = len(det)
    judged = det[det["verdict"].isin(["confirm", "reject", "stop"])]
    n_j = len(judged)
    n_confirm = int((judged["verdict"] == "confirm").sum())
    n_reject = int((judged["verdict"] == "reject").sum())
    n_stop = int((judged["verdict"] == "stop").sum())

    # 误杀：reject（不确认平仓）的票，平仓后 20 天内最高涨幅 ≥ 1R（risk 元）
    rej = judged[judged["verdict"] == "reject"].copy()
    if len(rej):
        rej["gain_after"] = rej["post_high_20d"] - rej["close"]
        rej["missed"] = (rej["risk"] > 0) & (rej["gain_after"] >= rej["risk"])
        n_kill = int(rej["missed"].sum())
    else:
        n_kill = 0
    kill_rate = n_kill / n_reject if n_reject else 0.0

    # 漏补：confirm（确认补仓）的票，补仓后 20 天内最高涨幅 ≥ 1R（补对了多少）
    conf = judged[judged["verdict"] == "confirm"].copy()
    if len(conf):
        conf["gain_after"] = conf["post_high_20d"] - conf["close"]
        conf["big_win"] = (conf["risk"] > 0) & (conf["gain_after"] >= conf["risk"])
        n_leak_ok = int(conf["big_win"].sum())
    else:
        n_leak_ok = 0
    leak_rate = n_leak_ok / n_confirm if n_confirm else 0.0

    # 机会成本辅助口径：reject/confirm 组若持有 20 天（确认日后第 20 根收盘）的平均 R——
    # 衡量「确认规则截断/保留」的真实代价（R = (收盘20 - 进场价) / 每股风险）
    def _avg_hold_r(sub: pd.DataFrame) -> float:
        if not len(sub) or "post_close_20d" not in sub.columns:
            return 0.0
        ok = sub[(sub["risk"] > 0) & (sub["post_close_20d"] > 0)]
        if not len(ok):
            return 0.0
        return round(float(((ok["post_close_20d"] - ok["entry"]) / ok["risk"]).mean()), 3)

    return {
        "n_signals": n, "n_judged": n_j,
        "n_confirm": n_confirm, "n_reject": n_reject, "n_stop": n_stop,
        "confirm_rate": n_confirm / n_j if n_j else 0.0,
        "reject_rate": n_reject / n_j if n_j else 0.0,
        "stop_rate": n_stop / n_j if n_j else 0.0,
        "n_missed_kill": n_kill, "miss_rate": kill_rate,     # 误杀率
        "n_leak_ok": n_leak_ok, "leak_rate": leak_rate,      # 漏补率（确认正确率）
        "reject_hold20_avg_r": _avg_hold_r(rej),   # reject 组持有20天近似平均R（误杀机会成本）
        "confirm_hold20_avg_r": _avg_hold_r(conf), # confirm 组持有20天近似平均R
        "detail": det,
        # 口径说明（写入报告用）
        "definitions": {
            "miss_rate": "误杀率 = 被不确认平仓(reject)的票中，平仓后20天内最高涨幅≥1R的比例"
                         "（平仓价=确认日收盘；20天内最高价 vs 平仓价涨幅 ≥ 单笔风险额）",
            "leak_rate": "漏补率 = 确认补仓(confirm)的票中，补仓后20天内最高涨幅≥1R的比例"
                         "（补仓价=确认日收盘；口径同误杀）",
            "qualify": "误杀 <10% 合格；>20% 需调规则（2026-08-06 老板确认口径）",
        },
    }


def r_bucket_dist(signals_df: pd.DataFrame, mode: str = "prebreak",
                  hold: str = "20d") -> dict:
    """④ R 档位分布（phase_in 前后对比用）

    Args:
        signals_df: signals.csv 全量（backtest_c23 / backtest_final）
        mode/hold: 触发集口径（prebreak/20d）

    Returns:
        dict: 样本数/平均R/中位数/胜率/10R+占比/最大单笔R/最大单笔占累计R比例/R分布表
    """
    h = int(str(hold).replace("d", ""))
    sub = signals_df[(signals_df["mode"] == mode)
                     & (signals_df[f"triggered_{h}d"] == 1)].copy()
    rs = sub[f"r_{h}d"].astype(float)
    total_r = float(rs.sum())
    max_r = float(rs.max())
    n10 = int((rs >= 10).sum())
    buckets = {}
    edges = [(-1e9, -3), (-3, -1), (-1, 0), (0, 1), (1, 3), (3, 10), (10, 1e9)]
    labels = ["<-3R", "-3~-1R", "-1~0R", "0~1R", "1~3R", "3~10R", "10R+"]
    for (lo, hi), lab in zip(edges, labels):
        buckets[lab] = int(((rs >= lo) & (rs < hi)).sum())
    return {
        "n": len(rs), "sum_r": round(total_r, 1),
        "avg_r": round(float(rs.mean()), 3) if len(rs) else 0.0,
        "median_r": round(float(rs.median()), 3) if len(rs) else 0.0,
        "win_rate": round(float((rs > 0).mean()), 4),
        "n_10r": n10, "pct_10r": round(n10 / len(rs) * 100, 2) if len(rs) else 0.0,
        "max_r": round(max_r, 2),
        "max_share_pct": round(max_r / total_r * 100, 1) if total_r else 0.0,
        "buckets": buckets,
    }


def format_replay_report(r3: dict, r4_final: dict, r4_c23: dict) -> str:
    """③④ 结果版式（报告用文本）"""
    W = 78
    line = "-" * W
    out = [line, "0.5R 确认规则质量验证 + 大赢家归因（2026-08-06 老板确认四连包 ③④）".center(W), line]
    # ③
    out += [line, "③ 确认规则质量回放（backtest_final 514 笔触发 · 0.5R 起步）".center(W), line]
    out.append(f"  回放笔数          {r3['n_signals']} 笔（K线可判定 {r3['n_judged']}）")
    out.append(f"  确认率（补仓）    {r3['confirm_rate']:.1%}（{r3['n_confirm']} 笔）")
    out.append(f"  不确认平仓率      {r3['reject_rate']:.1%}（{r3['n_reject']} 笔）")
    out.append(f"  触止损（层面1）   {r3['stop_rate']:.1%}（{r3['n_stop']} 笔）")
    out.append(f"  误杀率            {r3['miss_rate']:.1%}（{r3['n_missed_kill']} / {r3['n_reject']}）"
               + ("   <10% 合格 ✅" if r3['miss_rate'] < 0.10
                  else "   >20% 需调规则 ⚠️" if r3['miss_rate'] > 0.20
                  else "   10~20% 临界"))
    out.append(f"  漏补率（确认正确率）{r3['leak_rate']:.1%}（{r3['n_leak_ok']} / {r3['n_confirm']}）")
    out.append(f"  机会成本(持有20天近似R): reject组 {r3['reject_hold20_avg_r']:+.3f}"
               f" / confirm组 {r3['confirm_hold20_avg_r']:+.3f}"
               "（确认日之后第20根收盘 vs 进场价的平均R——衡量截断/保留的真实代价）")
    out.append(f"  误杀口径: {r3['definitions']['miss_rate']}")
    out.append(f"  漏补口径: {r3['definitions']['leak_rate']}")
    out.append(f"  合格线: {r3['definitions']['qualify']}")
    # ④
    out += [line, "④ phase_in 前后档位对比（prebreak/20d 触发信号 R）".center(W), line]
    hdr = f"{'':<14}{'phase_in 关(c23)':>18}{'phase_in 开(final)':>18}"
    rows = [
        ("触发样本", f"{r4_c23['n']}", f"{r4_final['n']}"),
        ("平均 R", f"{r4_c23['avg_r']:+.3f}", f"{r4_final['avg_r']:+.3f}"),
        ("中位数 R", f"{r4_c23['median_r']:+.3f}", f"{r4_final['median_r']:+.3f}"),
        ("胜率", f"{r4_c23['win_rate']:.1%}", f"{r4_final['win_rate']:.1%}"),
        ("累计 R", f"{r4_c23['sum_r']:+.1f}", f"{r4_final['sum_r']:+.1f}"),
        ("10R+ 占比", f"{r4_c23['pct_10r']:.1f}%（{r4_c23['n_10r']}笔）",
         f"{r4_final['pct_10r']:.1f}%（{r4_final['n_10r']}笔）"),
        ("最大单笔 R", f"{r4_c23['max_r']:+.1f}", f"{r4_final['max_r']:+.1f}"),
        ("最大单笔/累计R", f"{r4_c23['max_share_pct']:.1f}%", f"{r4_final['max_share_pct']:.1f}%"),
    ]
    out.append(hdr)
    for name, a, b in rows:
        out.append(f"  {name:<12}{a:>18}{b:>18}")
    labs = ["<-3R", "-3~-1R", "-1~0R", "0~1R", "1~3R", "3~10R", "10R+"]
    for lab in labs:
        out.append(f"     {lab:<8}{r4_c23['buckets'][lab]:>14}{r4_final['buckets'][lab]:>18}")
    out.append(line)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals-final", default=DEFAULT_FINAL)
    ap.add_argument("--signals-c23", default=DEFAULT_C23)
    ap.add_argument("--mode", default="prebreak")
    ap.add_argument("--hold", default="20d")
    args = ap.parse_args()

    fin = pd.read_csv(args.signals_final, encoding="utf-8-sig")
    c23 = pd.read_csv(args.signals_c23, encoding="utf-8-sig")
    h = int(str(args.hold).replace("d", ""))
    tr_fin = fin[(fin["mode"] == args.mode) & (fin[f"triggered_{h}d"] == 1)]
    tr_c23 = c23[(c23["mode"] == args.mode) & (c23[f"triggered_{h}d"] == 1)]
    print(f"[③④] 触发样本: final {len(tr_fin)} 笔 / c23 {len(tr_c23)} 笔")

    print("[③] 加载 K 线（只读 duckdb，缓存）…")
    klines = load_kline_cache([str(c) for c in tr_fin["code"].unique()])
    print(f"[③] K 线命中 {len(klines)} 只")
    r3 = replay_confirm(fin, klines, mode=args.mode, hold=args.hold)
    r4_fin = r_bucket_dist(fin, mode=args.mode, hold=args.hold)
    r4_c23 = r_bucket_dist(c23, mode=args.mode, hold=args.hold)
    print()
    print(format_replay_report(r3, r4_fin, r4_c23))
    return 0


if __name__ == "__main__":
    sys.exit(main())
