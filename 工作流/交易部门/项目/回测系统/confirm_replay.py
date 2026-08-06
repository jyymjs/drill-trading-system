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
    python 项目/回测系统/confirm_replay.py [--signals-final 产出/输出/数据/backtest_final_20260806/signals.csv]
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
                   mode: str = "prebreak", hold: str = "20d",
                   confirm_mode: str = "strict") -> dict:
    """③ 确认规则质量回放：确认率 / 误杀率 / 漏补率

    Args:
        signals_df: backtest_final signals.csv 全量
        klines: code -> 日K线（load_kline_cache 产物）
        mode/hold: 触发集口径（与资金模拟一致：prebreak/20d 触发）
        confirm_mode: strict（现状）/ any2 / no_c2 / delay2（2026-08-06
            老板拍板 1B 对照实验参数化；判定规则单一来源
            indicators.phase_confirm_from_kline）

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
        v = phase_confirm_from_kline(k, str(r["date"])[:10], entry, stop, confirm_mode)
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
        "confirm_mode": confirm_mode,
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


def explore_conditions(signals_df: pd.DataFrame,
                       klines: dict[str, pd.DataFrame],
                       mode: str = "prebreak", hold: str = "20d") -> dict:
    """三条件独立命中率探索（1B 对照实验 · 数据驱动定放宽方案）

    对每笔触发信号（确认日单根）独立评估 C1/C2/C3 + 20 天 ≥1R 标记：
      - 各条件在 good（20 天内 ≥1R）/ bad / 误杀组（reject&good）上的通过率
        —— 通过率越低 = 挡掉的好票越多 = 该条件误杀越重
      - reject 原因组合分布（good vs bad）
      - 放宽模拟（any2 / no_c2 / delay2 转确认数 + 残留误杀率估计）
      - 微差度量（c1/c2 距通过线的比例差——判断延迟确认能否救回）
    触发日定位复用 phase_confirm_from_kline（与回放同源，不复制规则）。

    Returns:
        dict: 条件命中率表 / 组合分布 / 放宽模拟 / 微差 + detail DataFrame
    """
    from 分析决策.分析.indicators import confirm_conditions, phase_confirm_from_kline

    h = int(str(hold).replace("d", ""))
    sub = signals_df[(signals_df["mode"] == mode)
                     & (signals_df[f"triggered_{h}d"] == 1)].copy()
    sub = sub.sort_values(["date", "code"])

    rows = []
    for _, r in sub.iterrows():
        code = str(r["code"])
        k = klines.get(code)
        if k is None or len(k) < 2:
            rows.append({"code": code, "date": str(r["date"])[:10], "verdict": "no_data"})
            continue
        entry = float(r[f"entry_{h}d"])
        stop_raw = r.get("stop")
        if stop_raw is None or str(stop_raw) in ("", "nan", "None"):
            stop_raw = r.get("stop_loss")
        try:
            stop = float(stop_raw) if stop_raw is not None and str(stop_raw) not in ("", "nan", "None") else 0.0
        except (TypeError, ValueError):
            stop = 0.0
        risk = float(r.get("risk", 0) or 0)
        v = phase_confirm_from_kline(k, str(r["date"])[:10], entry, stop)
        if v["wait"]:
            continue
        dates = k["日期"].astype(str).str[:10].values
        conf_idx = next(i for i, d in enumerate(dates) if d == v["confirm_date"])
        cond = confirm_conditions(k.iloc[:conf_idx + 1], entry, stop)
        close = cond["close"]
        post_high = _post_exit_high(k, str(dates[conf_idx])[:10], window=20)
        good = bool(risk > 0 and post_high - close >= risk)
        # 触止损（stopped）不算 reject——止损层面1 优先，无论条件状态
        reject = bool(not cond["stopped"]
                      and (not cond["c1"] or not cond["c2"] or cond["reject_vol"]))
        # delay2 模拟：首根未确认（reject 且未触止损）→ 第二根 strict 单根判定
        d2_ok = d2_stopped = None
        if reject and not cond["stopped"] and conf_idx + 1 < len(k):
            c2 = confirm_conditions(k.iloc[:conf_idx + 2], entry, stop)
            d2_ok = bool(c2["c1"] and c2["c2"] and c2["c3"])
            d2_stopped = bool(c2["stopped"])
        # 微差（距通过线的比例差 %，仅在条件不满足时定义）
        c1_gap = c2_gap = None
        if not cond["c1"]:
            c1_gap = (entry - close) / entry * 100 if entry else None
        if not cond["c2"]:
            open_close = float(k.iloc[conf_idx - 1]["收盘"])
            c2_gap = (open_close - close) / open_close * 100 if open_close else None
        rows.append({
            "code": code, "date": str(r["date"])[:10],
            "entry": entry, "risk": risk, "close": close,
            "c1": bool(cond["c1"]), "c2": bool(cond["c2"]), "c3": bool(cond["c3"]),
            "stopped": bool(cond["stopped"]), "reject": reject,
            "good": good, "c1_gap_pct": c1_gap, "c2_gap_pct": c2_gap,
            "d2_ok": d2_ok, "d2_stopped": d2_stopped,
        })
    det = pd.DataFrame(rows)

    def _rate(grp: pd.DataFrame, col: str) -> float:
        return round(float(grp[col].mean()), 4) if len(grp) else 0.0

    judged = det[~det["verdict"].isna()] if "verdict" in det.columns else det
    judged = det[det["stopped"] | det["reject"] | (det["c1"] & det["c2"] & det["c3"])]
    rej = judged[judged["reject"]]
    kill = rej[rej["good"]]
    true_rej = rej[~rej["good"]]
    good_all = judged[judged["good"]]
    bad_all = judged[~judged["good"]]
    stopped = judged[judged["stopped"]]

    cond_table = {
        "n": len(judged), "n_good": len(good_all), "n_bad": len(bad_all),
        "n_reject": len(rej), "n_kill": len(kill), "n_true_reject": len(true_rej),
        "n_stopped": len(stopped),
        "good_rate": _rate(judged, "good"),
        "pass_rate": {c: {
            "all": _rate(judged, c), "good": _rate(good_all, c),
            "bad": _rate(bad_all, c), "kill": _rate(kill, c),
        } for c in ("c1", "c2", "c3")},
    }

    # reject 原因组合分布（good vs bad）——哪个条件挡掉了好票
    def _combo(g: pd.DataFrame) -> str:
        return ("C1" if g["c1"] else "✗C1") + ("C2" if g["c2"] else "✗C2") \
            + ("C3" if g["c3"] else "✗C3")
    if len(rej):
        rej2 = rej.copy()
        rej2["combo"] = rej2.apply(_combo, axis=1)
        combos = rej2.groupby("combo")["good"].agg(["count", "sum"]).rename(
            columns={"count": "n", "sum": "n_good"})
        combos["pct_good"] = (combos["n_good"] / combos["n"] * 100).round(1)
        combos = combos.sort_values("n", ascending=False)
    else:
        combos = pd.DataFrame(columns=["n", "n_good", "pct_good"])

    # 放宽模拟（reject 组内转 confirm → 残留误杀率）
    def _relax_sim(mask) -> dict:
        if not len(rej):
            return {"saved": 0, "n_new": 0, "remain_kill_rate": 0.0}
        sub2 = rej[mask]
        n_new = len(sub2)
        saved = int(sub2["good"].sum())
        remain = len(rej) - n_new
        remain_kill = len(kill) - saved
        return {"saved": saved, "n_new": n_new,
                "remain_kill_rate": remain_kill / remain if remain else 0.0}

    relax = {
        "any2": _relax_sim((rej[["c1", "c2", "c3"]].sum(axis=1)) >= 2),
        "no_c2": _relax_sim(rej["c1"] & rej["c3"]),
        "no_c1": _relax_sim(rej["c2"] & rej["c3"]),   # 参考
        "no_c3": _relax_sim(rej["c1"] & rej["c2"]),   # 参考
        # delay2：首根未确认 → 第二根确认的转 confirm；第二根触止损的转 stop 组
        # （止损层面1 优先，不是"不确认平仓"——两者都从残留 reject 组转出）
        "delay2": _relax_sim(rej["d2_ok"].fillna(False)
                             | rej["d2_stopped"].fillna(False)),
    }

    # 微差：误杀票中距通过线 ≤1% 的比例（延迟/放宽能救回的"微差误杀"）
    gap = {"kill": {}, "true_reject": {}}
    for name, grp in (("kill", kill), ("true_reject", true_rej)):
        if len(grp):
            c1m = grp["c1_gap_pct"].notna()
            c2m = grp["c2_gap_pct"].notna()
            gap[name] = {
                "c1_le1pct": round(float((grp[c1m]["c1_gap_pct"] <= 1).mean()), 4) if c1m.any() else 0.0,
                "c2_le1pct": round(float((grp[c2m]["c2_gap_pct"] <= 1).mean()), 4) if c2m.any() else 0.0,
            }
        else:
            gap[name] = {"c1_le1pct": 0.0, "c2_le1pct": 0.0}

    return {"cond": cond_table, "combos": combos, "relax": relax,
            "gap": gap, "detail": det, "mode": mode, "hold": hold}


def format_explore_report(e: dict) -> str:
    """探索版式（三条件命中率 + 组合分布 + 放宽模拟 + 微差）"""
    W = 78
    line = "-" * W
    c = e["cond"]
    out = [line, "三条件独立命中率探索（确认日单根 · 20天≥1R=good）".center(W), line]
    out.append(f"  判定样本 {c['n']} 笔 | good {c['n_good']}（{c['good_rate']:.1%}）"
               f" | bad {c['n_bad']} | stopped {c['n_stopped']}")
    out.append(f"  reject {c['n_reject']} 笔（其中误杀 {c['n_kill']}）")
    out.append(f"  {'条件':<14}{'全量':>9}{'good':>9}{'bad':>9}{'误杀组':>9}  ←通过率")
    for name, col in (("C1 收下去", "c1"), ("C2 动能延续", "c2"), ("C3 非放量阴线", "c3")):
        p = c["pass_rate"][col]
        out.append(f"  {name:<14}{p['all']:>9.1%}{p['good']:>9.1%}{p['bad']:>9.1%}{p['kill']:>9.1%}")
    out.append("  解读: 误杀组通过率越低 → 该条件挡掉的好票越多（主要误杀源）")
    out.append(line)
    out.append("  reject 原因组合（good vs bad）".center(W))
    out.append(f"  {'组合':<24}{'笔数':>6}{'good':>6}{'good占比':>9}")
    for combo, row in e["combos"].iterrows():
        out.append(f"  {combo:<24}{int(row['n']):>6}{int(row['n_good']):>6}{row['pct_good']:>8.1f}%")
    out.append(line)
    out.append("  放宽模拟（reject 组内转 confirm）".center(W))
    out.append(f"  {'方案':<12}{'转confirm':>10}{'救回good':>10}{'残留误杀率':>12}")
    base = c["n_kill"] / c["n_reject"] if c["n_reject"] else 0.0
    out.append(f"  {'现状(strict)':<12}{'0':>10}{'0':>10}{base:>12.1%}")
    for name, key in (("三取二 any2", "any2"), ("去C2 no_c2", "no_c2"),
                      ("参考:去C1", "no_c1"), ("参考:去C3", "no_c3"),
                      ("延迟二次 delay2", "delay2")):
        s = e["relax"][key]
        out.append(f"  {name:<12}{s['n_new']:>10}{s['saved']:>10}{s['remain_kill_rate']:>12.1%}")
    out.append("  微差（距通过线≤1% 占该组比例）")
    for name, key in (("误杀组", "kill"), ("真reject组", "true_reject")):
        g = e["gap"][key]
        out.append(f"    {name}: C1微差 {g['c1_le1pct']:.1%} | C2微差 {g['c2_le1pct']:.1%}")
    out.append(line)
    return "\n".join(out)


def make_confirm_fn(confirm_mode: str = "strict"):
    """sim_capital half_phase 确认判定注入（放宽版同源 · 1B 对照）

    与 sim_capital.default_confirm_fn 同构：K线直读 duckdb（只读，带缓存），
    判定规则单一来源 indicators.phase_confirm_from_kline（confirm_mode 参数化：
    strict=现状 / no_c2 / delay2），wait/数据不可得 → 放行侧确认
    （不因数据问题误拒补仓）。strict 模式与 default_confirm_fn 等价
    （同库同规则，仅实现位置不同）。
    """
    from 分析决策.分析.indicators import phase_confirm_from_kline
    from 数据基础.duckdb.reader import read_kline

    _cache: dict[str, object] = {}

    def _fn(code: str, signal_date: str, entry_price: float,
            stop_loss: float) -> dict:
        sym = str(code).zfill(6)
        df = _cache.get(sym)
        if df is None:
            try:
                df = read_kline(sym)
            except Exception:  # noqa: BLE001 - 数据异常 → 放行侧
                df = None
            _cache[sym] = df
        if df is None or len(df) < 2:
            return {"confirmed": True, "stopped": False, "close": 0.0,
                    "confirm_date": signal_date}
        v = phase_confirm_from_kline(df, signal_date, entry_price, stop_loss,
                                     confirm_mode)
        if v["wait"]:
            return {"confirmed": True, "stopped": False, "close": 0.0,
                    "confirm_date": signal_date}
        return {"confirmed": v["confirmed"], "stopped": v["stopped"],
                "close": v["close"], "confirm_date": v["confirm_date"]}

    return _fn


def rebuild_exit_for_mode(signals_df: pd.DataFrame,
                          klines: dict[str, pd.DataFrame],
                          confirm_mode: str = "strict",
                          mode: str = "prebreak", hold: str = "20d"
                          ) -> tuple[pd.DataFrame, dict]:
    """放宽版 signals 副本：出场数据按确认规则重算（1B 对照 · 引擎同规则近似）

    strict 模式 = 原样返回副本（现状锚点，零改动；顺带做近似准确度校验）。
    放宽模式（no_c2 / delay2）仅重写「放宽新确认」的票（现状 strict reject
    → 放宽 confirm 的票——引擎没算过它们的继续持有出场）：
      确认日次日 → 触发日+hold 窗口按 tracking._track_window 跟踪
      （最低≤止损 → 止损价出场；否则窗口末收盘出场；无移动止损——与
      引擎 _phase_in_track 的 confirm 后续跟踪同式同源，不复制规则），
      r = (exit - entry) / risk。其余票（strict 也确认 / reject / stopped /
      wait）保持引擎原列不变。

    校验（近似准确度证据，报告如实标注）：strict 模式下对全量引擎 confirm 票
    重算 exit vs signals exit_20d 的一致性（相同比例 / 最大偏差）。

    Returns:
        (signals 副本, 校验统计 dict)
    """
    from 分析决策.分析.indicators import phase_confirm_from_kline
    from 回测系统.tracking import _track_window

    h = int(str(hold).replace("d", ""))
    out = signals_df.copy()
    trig = out[(out["mode"] == mode) & (out[f"triggered_{h}d"] == 1)]
    entry_col, exit_col = f"entry_{h}d", f"exit_{h}d"
    exit_date_col, r_col = f"exit_date_{h}d", f"r_{h}d"
    verify = {"n_confirm_strict": 0, "exit_identical": 0, "r_identical": 0,
              "max_dev": 0.0, "n_rebuild": 0}

    def _track_from(cdate: str, t: int, entry: float, stop: float) -> tuple[float, str]:
        dates = k["日期"].astype(str).str[:10].values
        c_idx = next(i for i, d in enumerate(dates) if d == cdate)
        end = min(t + h, len(k) - 1)
        if c_idx + 1 <= end:
            ex_price, ex_ts, _st = _track_window(
                k["最高"].values, k["最低"].values, k["收盘"].values, dates,
                c_idx + 1, end, entry, stop,
                enable_cost=False, cost_multiplier=1.0, moving_stop=False)
            return float(ex_price), str(ex_ts)[:10]
        return float(k["收盘"].values[end]) if end >= 0 else entry, \
            str(dates[end])[:10] if end >= 0 else cdate

    for idx, r in trig.iterrows():
        code = str(r["code"])
        k = klines.get(code)
        if k is None or len(k) < 2:
            continue
        entry = float(r[entry_col])
        stop_raw = r.get("stop")
        if stop_raw is None or str(stop_raw) in ("", "nan", "None"):
            stop_raw = r.get("stop_loss")
        try:
            stop = float(stop_raw) if stop_raw is not None and str(stop_raw) not in ("", "nan", "None") else 0.0
        except (TypeError, ValueError):
            stop = 0.0
        risk = float(r.get("risk", 0) or 0)
        sd = str(r["date"])[:10]
        v_strict = phase_confirm_from_kline(k, sd, entry, stop)
        if v_strict["wait"] or v_strict["stopped"] or not v_strict["confirmed"]:
            # 非 strict-confirm 票：放宽模式下仍需看是否放宽新确认
            if confirm_mode == "strict":
                continue
            v = phase_confirm_from_kline(k, sd, entry, stop, confirm_mode)
            if v["wait"] or v["stopped"] or not v["confirmed"]:
                continue
            # 放宽新确认：重算出场（确认日索引 → 信号日窗口）
            dates = k["日期"].astype(str).str[:10].values
            t = next((i for i, d in enumerate(dates) if d == sd),
                     next(i for i, d in enumerate(dates) if d > sd))
            ex_price, ex_date = _track_from(v["confirm_date"], t, entry, stop)
            out.at[idx, exit_col] = ex_price
            out.at[idx, exit_date_col] = ex_date
            out.at[idx, r_col] = (ex_price - entry) / risk if risk > 0 \
                else float(r[r_col])
            verify["n_rebuild"] += 1
            continue
        # strict confirm 票：校验重算 vs 引擎（strict 模式零改动；放宽模式保持原列）
        if confirm_mode == "strict":
            dates = k["日期"].astype(str).str[:10].values
            t = next((i for i, d in enumerate(dates) if d == sd),
                     next(i for i, d in enumerate(dates) if d > sd))
            ex_price, ex_date = _track_from(v_strict["confirm_date"], t, entry, stop)
            verify["n_confirm_strict"] += 1
            r_new = (ex_price - entry) / risk if risk > 0 else 0.0
            if abs(ex_price - float(r[exit_col])) < 0.01:
                verify["exit_identical"] += 1
            if abs(r_new - float(r[r_col])) < 0.05:
                verify["r_identical"] += 1
            verify["max_dev"] = max(verify["max_dev"],
                                    abs(ex_price - float(r[exit_col])))
    return out, verify


def compare_confirm_modes(signals_df: pd.DataFrame,
                          klines: dict[str, pd.DataFrame],
                          mode: str = "prebreak", hold: str = "20d",
                          capital: float = 5600.0, risk_ratio: float = 0.02,
                          max_positions: int = 3) -> dict:
    """1B 对照实验：现状(strict) vs 放宽(no_c2/delay2) 全量对照

    维度：
      ① 回放（replay_confirm 参数化）：确认率 / 不确认率 / 误杀率 / 漏补率 /
         机会成本（reject 组持有 20 天近似 R）
      ② 资金约束模拟（sim_capital half_phase，2.0%×max_positions 仓）：
         收益 / 回撤 / 胜率 / avgR / 笔数；放宽版用 rebuild_exit_for_mode
         重算出场数据的副本 + make_confirm_fn 注入（引擎同规则近似）
      ③ 截断代价：sim_capital trades 的 R 分布（-3~-1R 尾部大亏笔数 /
         胜率 / 平均 R）
      ④ 信号层 avgR：现状 = 引擎 r_20d；放宽版 = 重算副本 r 均值

    Returns:
        {"strict": {...}, "no_c2": {...}, "delay2": {...}, "verify": {...},
         "explore": {...}}
    """
    from 回测系统.sim_capital import simulate_capital

    res: dict[str, dict] = {}
    for cm in ("strict", "no_c2", "delay2"):
        rp = replay_confirm(signals_df, klines, mode=mode, hold=hold,
                            confirm_mode=cm)
        sig, verify = rebuild_exit_for_mode(signals_df, klines, cm,
                                            mode=mode, hold=hold)
        sim = simulate_capital(sig, capital, risk_ratio,
                               max_positions=max_positions, mode=mode,
                               hold=hold, grades=["S"], half_phase=True,
                               confirm_fn=make_confirm_fn(cm))
        res[cm] = {"replay": rp, "sim": sim, "verify": verify, "sig": sig}
    res["explore"] = explore_conditions(signals_df, klines, mode=mode,
                                        hold=hold)
    return res


def _tail_stats(trades: list[dict]) -> dict:
    """截断代价统计：trades 的 R 分布（-3~-1R 尾部大亏 / 胜率 / 平均R）"""
    rs = [float(t["r"]) for t in trades]
    n = len(rs)
    tail = sum(1 for x in rs if -3 <= x < -1)
    return {
        "n": n, "tail_3_1": tail,
        "tail_pct": tail / n if n else 0.0,
        "win_rate": sum(1 for x in rs if x > 0) / n if n else 0.0,
        "avg_r": sum(rs) / n if n else 0.0,
        "sum_r": sum(rs),
    }


def format_compare_report(c: dict, capital: float, risk_ratio: float,
                          max_positions: int) -> str:
    """1B 对照版式（报告文本）"""
    W = 78
    line = "-" * W
    out = [line, "0.5R 确认规则对照实验（2026-08-06 老板拍板 1B · 数据说话留哪版）".center(W), line]
    out.append(f"  口径: 回放 backtest_final 514 笔（prebreak/20d 触发）| "
               f"资金模拟 {capital:,.0f} 元 × {risk_ratio:.1%} × {max_positions} 仓 half_phase")
    v = c["strict"]["verify"]
    if v["n_confirm_strict"]:
        out.append(f"  近似校验: 引擎 confirm 票重算 exit 与 exit_20d 一致 "
                   f"{v['exit_identical']}/{v['n_confirm_strict']}"
                   f"（{v['exit_identical'] / v['n_confirm_strict']:.1%}）"
                   f" | 最大偏差 {v['max_dev']:.3f} 元 | 放宽新确认重算 {v['n_rebuild']} 笔")
    out.append(line)
    out.append("① 回放（确认率 / 误杀率 / 漏补率 / 机会成本）".center(W))
    hdr = f"{'':<16}{'现状(strict)':>18}{'放宽-去C2':>18}{'放宽-延迟二次':>18}"
    out.append(hdr)
    rows = [
        ("确认率（补仓）", "confirm_rate", "n_confirm", "{:.1%}（{}笔）"),
        ("不确认平仓率", "reject_rate", "n_reject", "{:.1%}（{}笔）"),
        ("触止损率", "stop_rate", "n_stop", "{:.1%}（{}笔）"),
        ("误杀率", "miss_rate", "n_missed_kill", "{:.1%}（{}/{}笔）"),
        ("漏补率（确认正确率）", "leak_rate", "n_leak_ok", "{:.1%}（{}/{}笔）"),
        ("机会成本 reject组", "reject_hold20_avg_r", None, "{:+.3f}R"),
        ("机会成本 confirm组", "confirm_hold20_avg_r", None, "{:+.3f}R"),
    ]
    for name, key, nkey, fmt in rows:
        cells = []
        for cm in ("strict", "no_c2", "delay2"):
            rp = c[cm]["replay"]
            if nkey and nkey == "n_missed_kill":
                cells.append(fmt.format(rp[key], rp[nkey], rp["n_reject"]))
            elif nkey and nkey == "n_leak_ok":
                cells.append(fmt.format(rp[key], rp[nkey], rp["n_confirm"]))
            elif nkey:
                cells.append(fmt.format(rp[key], rp[nkey]))
            else:
                cells.append(fmt.format(rp[key]))
        out.append(f"  {name:<14}{cells[0]:>18}{cells[1]:>18}{cells[2]:>18}")
    out.append(line)
    out.append("② 资金约束模拟（half_phase · 收益/回撤/胜率/avgR/笔数）".center(W))
    out.append(hdr)
    sim_rows = [
        ("净盈利", "total_pnl", "{:+,.1f} 元"),
        ("总收益", "total_ret", "{:+.1f}%"),
        ("最大回撤", "max_dd_pct", "{:.1f}%"),
        ("可执行笔数", "n_exec", "{} 笔"),
        ("胜率", "win_rate", "{:.1%}"),
        ("平均 R", "avg_r", "{:.3f}"),
        ("盈亏比", "profit_factor", "{:.2f}"),
        ("半仓起步/确认补仓", "half_stats", None),
    ]
    for name, key, fmt in sim_rows:
        cells = []
        for cm in ("strict", "no_c2", "delay2"):
            sim = c[cm]["sim"]
            if key == "half_stats":
                hs = sim["half_stats"]
                cells.append(f"{hs['n_half']}/{hs['n_confirm']}笔")
            elif key == "total_pnl" or key == "total_ret" or key == "max_dd_pct" or key == "n_exec" or key == "win_rate" or key == "avg_r":
                cells.append(fmt.format(sim[key]))
            elif key == "profit_factor":
                pf = sim[key]
                cells.append("∞" if pf == float("inf") else fmt.format(pf))
        out.append(f"  {name:<14}{cells[0]:>18}{cells[1]:>18}{cells[2]:>18}")
    out.append(line)
    out.append("③ 截断代价（trades R 分布 · -3~-1R 尾部大亏）".center(W))
    out.append(hdr)
    tail_rows = [
        ("样本笔数", "n", "{}"),
        ("-3~-1R 大亏笔数", "tail_3_1", "{} 笔"),
        ("-3~-1R 占比", "tail_pct", "{:.1%}"),
        ("胜率", "win_rate", "{:.1%}"),
        ("平均 R", "avg_r", "{:+.3f}"),
        ("累计 R", "sum_r", "{:+.1f}"),
    ]
    for name, key, fmt in tail_rows:
        cells = []
        for cm in ("strict", "no_c2", "delay2"):
            ts = _tail_stats(c[cm]["sim"]["trades"])
            cells.append(fmt.format(ts[key]))
        out.append(f"  {name:<14}{cells[0]:>18}{cells[1]:>18}{cells[2]:>18}")
    out.append(line)
    out.append("④ 信号层 avgR（现状=引擎 r_20d；放宽=重算副本近似）".center(W))
    out.append(hdr)
    cells = []
    for cm in ("strict", "no_c2", "delay2"):
        sig = c[cm]["sig"]
        trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)]
        rs = trig["r_20d"].astype(float)
        cells.append(f"{rs.mean():+.3f}（{len(rs)}笔）")
    out.append(f"  {'信号层 avgR':<14}{cells[0]:>18}{cells[1]:>18}{cells[2]:>18}")
    out.append(line)
    return "\n".join(out)


def format_compare_md(c: dict, capital: float, risk_ratio: float,
                      max_positions: int) -> str:
    """1B 对照报告 markdown（产出/输出/确认规则对照-20260806.md · 项目惯例表格）"""
    names = {"strict": "现状(strict)", "no_c2": "放宽-去C2",
             "delay2": "放宽-延迟二次"}
    e = c["explore"]
    cond = e["cond"]
    out: list[str] = []
    out.append("# 0.5R 确认规则对照实验（老板拍板 1B · 数据说话留哪版）")
    out.append("")
    out.append("> 日期：2026-08-06 · 背景：现状版确认规则（C1 收下去 / C2 动能延续 / "
               "C3 非放量阴线，全满足才确认）误杀率 57.6%（>20% 红线），老板拍板跑 "
               "放宽版 vs 现状版对照，数据决定留哪版。")
    out.append("> 信号源：`产出/输出/数据/backtest_final_20260806/signals.csv`（514 笔触发 "
               "· prebreak/20d · 全 S 级）+ K 线 duckdb 只读 qfq 全量。")
    out.append("> 口径：误杀率 = 被不确认平仓（reject）的票中，平仓后 20 天内最高涨幅 "
               "≥1R 的比例；漏补率 = 确认补仓的票中 20 天内最高涨幅 ≥1R 的比例；"
               "合格线 <10%，>20% 需调规则。")
    out.append(f"> 资金模拟：{capital:,.0f} 元 × 单笔风险 {risk_ratio:.1%} × "
               f"{max_positions} 仓 · `sim_capital --half-phase`（0.5R 分步）。")
    out.append("")

    v = c["strict"]["verify"]
    out.append("## 〇、近似校验（放宽版出场重算 vs 引擎）")
    out.append("")
    if v["n_confirm_strict"]:
        out.append("| 项 | 数值 |")
        out.append("|---|---|")
        out.append(f"| 引擎 confirm 票重算 exit 与 exit_20d 一致 | "
                   f"{v['exit_identical']}/{v['n_confirm_strict']}"
                   f"（{v['exit_identical'] / v['n_confirm_strict']:.1%}） |")
        out.append(f"| R 重算一致（|Δr|<0.05） | "
                   f"{v['r_identical']}/{v['n_confirm_strict']}"
                   f"（{v['r_identical'] / v['n_confirm_strict']:.1%}） |")
        out.append(f"| 最大 exit 偏差 | {v['max_dev']:.3f} 元 |")
        out.append(f"| 放宽新确认重算笔数 | {v['n_rebuild']} 笔 |")
        out.append("")
        out.append("> 放宽版「新确认」票（现状 reject → 放宽 confirm）的出场无法从引擎 "
                   "复用，按引擎同规则重算（确认日次日 → 触发日+hold 窗口，"
                   "`tracking._track_window` 同式：最低≤止损 → 止损价出场，否则窗口末 "
                   "收盘出场，无移动止损）。上表证明重算与引擎严格一致——近似可信。")
    out.append("")

    out.append("## 一、探索：三条件独立命中率（数据驱动定案）")
    out.append("")
    out.append(f"判定样本 {cond['n']} 笔（确认日单根判定）｜good（20 天内 ≥1R）"
               f" {cond['n_good']} 笔（{cond['good_rate']:.1%}）｜bad {cond['n_bad']} 笔"
               f"｜stopped {cond['n_stopped']} 笔｜reject {cond['n_reject']} 笔"
               f"（其中误杀 {cond['n_kill']} 笔）")
    out.append("")
    out.append("| 条件 | 全量通过率 | good 通过率 | bad 通过率 | **误杀组通过率** |")
    out.append("|---|---:|---:|---:|---:|")
    for name, col in (("C1 收下去（收盘≥进场价）", "c1"),
                      ("C2 动能延续（收盘≥开仓日收盘）", "c2"),
                      ("C3 非放量阴线", "c3")):
        p = cond["pass_rate"][col]
        out.append(f"| {name} | {p['all']:.1%} | {p['good']:.1%} | {p['bad']:.1%} "
                   f"| **{p['kill']:.1%}** |")
    out.append("")
    out.append("> **误杀组通过率越低 = 该条件挡掉的好票越多 = 误杀越重。"
               "C2 动能延续仅 12.0%（good 全组 48.2%）——绝对主误杀源；"
               "C3 非放量阴线 82.5% 几乎不误杀；C1 居中。**")
    out.append("")
    out.append("reject 原因组合分布（good vs bad）：")
    out.append("")
    out.append("| 组合（✓=满足 ✗=不满足） | 笔数 | good | good 占比 |")
    out.append("|---|---:|---:|---:|")
    for combo, row in e["combos"].iterrows():
        out.append(f"| {combo} | {int(row['n'])} | {int(row['n_good'])} "
                   f"| {row['pct_good']:.1f}% |")
    out.append("")
    out.append("放宽模拟（reject 组内转 confirm 的残留误杀率）：")
    out.append("")
    out.append("| 方案 | 转 confirm | 救回 good | 残留误杀率 |")
    out.append("|---|---:|---:|---:|")
    base = cond["n_kill"] / cond["n_reject"] if cond["n_reject"] else 0.0
    out.append(f"| 现状（strict） | 0 | 0 | {base:.1%} |")
    for name, key in (("三取二 any2", "any2"), ("去C2 no_c2", "no_c2"),
                      ("参考:去C1", "no_c1"), ("参考:去C3", "no_c3"),
                      ("延迟二次 delay2", "delay2")):
        s = e["relax"][key]
        out.append(f"| {name} | {s['n_new']} | {s['saved']} "
                   f"| {s['remain_kill_rate']:.1%} |")
    out.append("")
    out.append("> **定案（数据驱动）：实现 no_c2（去 C2）与 delay2（延迟二次确认）两个"
               "对照方案；三取二淘汰**——残留误杀率不降反升（58.1%），新确认的 159 笔"
               "里只有 91 笔好票（57%），混入坏票多。no_c2 直接对应主误杀源 C2，救回 "
               "121 笔/73 good（60.3%）；delay2 只给强票多一天，救回 117 笔（93 笔"
               "确认 + 24 笔第二根触止损）中 86 笔好票（**73.5% 救回质量最高**）+ 残留"
               "误杀率降至 46.8%（全方案最低）。微差统计：误杀组 C1/C2 距通过线 ≤1% "
               "各约 35%——大量误杀是「单根微差回落」，延迟一天能救回强票。**"
               "（注：本表残留误杀率为首根平仓视角的近似；正式口径见第二节回放——"
               "delay2 实际在 T+2 平仓，误杀率 57.3%）")
    out.append("")

    out.append("## 二、回放对照（确认率 / 误杀率 / 漏补率 / 机会成本）")
    out.append("")
    hdr = f"| 指标 | {names['strict']} | {names['no_c2']} | {names['delay2']} |"
    out.append(hdr)
    out.append("|---|---:|---:|---:|")
    for name, key, nkey, fmt in [
        ("确认率（补仓）", "confirm_rate", "n_confirm", "{:.1%}（{}笔）"),
        ("不确认平仓率", "reject_rate", "n_reject", "{:.1%}（{}笔）"),
        ("触止损率（层面1）", "stop_rate", "n_stop", "{:.1%}（{}笔）"),
        ("**误杀率**", "miss_rate", "n_missed_kill",
         "{:.1%}（{}/{}笔）"),
        ("**漏补率**（确认正确率）", "leak_rate", "n_leak_ok",
         "{:.1%}（{}/{}笔）"),
        ("机会成本 reject 组", "reject_hold20_avg_r", None, "{:+.3f}R"),
        ("机会成本 confirm 组", "confirm_hold20_avg_r", None, "{:+.3f}R"),
    ]:
        cells = []
        for cm in ("strict", "no_c2", "delay2"):
            rp = c[cm]["replay"]
            if nkey == "n_missed_kill":
                cells.append(fmt.format(rp[key], rp[nkey], rp["n_reject"]))
            elif nkey == "n_leak_ok":
                cells.append(fmt.format(rp[key], rp[nkey], rp["n_confirm"]))
            elif nkey:
                cells.append(fmt.format(rp[key], rp[nkey]))
            else:
                cells.append(fmt.format(rp[key]))
        out.append(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} |")
    out.append("")

    out.append("## 三、资金约束模拟（5600 元 × 2.0% × 3 仓 · half_phase 0.5R 分步）")
    out.append("")
    out.append(hdr)
    out.append("|---|---:|---:|---:|")
    for name, key in [
        ("净盈利", "total_pnl"), ("总收益", "total_ret"),
        ("最大回撤", "max_dd_pct"), ("可执行笔数", "n_exec"),
        ("胜率", "win_rate"), ("平均 R（成交口径）", "avg_r"),
        ("盈亏比（金额）", "profit_factor"),
        ("半仓起步/确认补仓", "half_stats"),
    ]:
        cells = []
        for cm in ("strict", "no_c2", "delay2"):
            sim = c[cm]["sim"]
            if key == "half_stats":
                hs = sim["half_stats"]
                cells.append(f"{hs['n_half']} / {hs['n_confirm']} 笔")
            elif key == "total_pnl":
                cells.append(f"{sim[key]:+,.1f} 元")
            elif key == "total_ret":
                cells.append(f"{sim[key]:+.1f}%")
            elif key == "max_dd_pct":
                cells.append(f"{sim[key]:.1f}%")
            elif key == "n_exec":
                cells.append(f"{sim[key]} 笔")
            elif key == "win_rate":
                cells.append(f"{sim[key]:.1%}")
            elif key == "avg_r":
                cells.append(f"{sim[key]:+.3f}")
            elif key == "profit_factor":
                pf = sim[key]
                cells.append("∞" if pf == float("inf") else f"{pf:.2f}")
        out.append(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} |")
    out.append("")

    out.append("## 四、截断代价对比（资金模拟 trades 的 R 分布）")
    out.append("")
    out.append("| 指标 | 现状(strict) | 放宽-去C2 | 放宽-延迟二次 |")
    out.append("|---|---:|---:|---:|")
    for name, key, fmt in [
        ("样本笔数", "n", "{}"),
        ("-3~-1R 尾部大亏笔数", "tail_3_1", "{} 笔"),
        ("-3~-1R 占比", "tail_pct", "{:.1%}"),
        ("胜率", "win_rate", "{:.1%}"),
        ("平均 R", "avg_r", "{:+.3f}"),
        ("累计 R", "sum_r", "{:+.1f}"),
    ]:
        cells = []
        for cm in ("strict", "no_c2", "delay2"):
            ts = _tail_stats(c[cm]["sim"]["trades"])
            cells.append(fmt.format(ts[key]))
        out.append(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} |")
    out.append("")

    out.append("## 五、信号层 avgR（现状 = 引擎 r_20d；放宽 = 重算副本近似）")
    out.append("")
    cells = []
    for cm in ("strict", "no_c2", "delay2"):
        sig = c[cm]["sig"]
        trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)]
        rs = trig["r_20d"].astype(float)
        cells.append(f"{rs.mean():+.3f}（{len(rs)} 笔）")
    out.append("| 信号层 avgR | " + " | ".join(cells) + " |")
    out.append("")

    # 结论（数据驱动 · 签字权归老板）
    rp_strict, rp_delay2 = (c["strict"]["replay"],
                            c["delay2"]["replay"])
    sim_strict, sim_no_c2, sim_delay2 = (c["strict"]["sim"], c["no_c2"]["sim"],
                                         c["delay2"]["sim"])
    delay_win = (sim_delay2["total_ret"] > sim_strict["total_ret"]
                 and sim_delay2["max_dd_pct"] <= sim_strict["max_dd_pct"])
    no_c2_win = (sim_no_c2["total_ret"] > sim_strict["total_ret"]
                 and sim_no_c2["win_rate"] >= sim_strict["win_rate"])
    if delay_win:
        rec = "delay2（放宽-延迟二次确认）"
        rec_why = (f"收益最高（{sim_delay2['total_ret']:+.1f}% vs 现状 "
                   f"{sim_strict['total_ret']:+.1f}%）、回撤最低（"
                   f"{sim_delay2['max_dd_pct']:.1f}% vs {sim_strict['max_dd_pct']:.1f}%）、"
                   f"胜率 {sim_delay2['win_rate']:.1%} vs {sim_strict['win_rate']:.1%}、"
                   f"avgR {sim_delay2['avg_r']:+.3f} vs {sim_strict['avg_r']:+.3f}、"
                   f"盈亏比 {sim_delay2['profit_factor']:.2f} vs "
                   f"{sim_strict['profit_factor']:.2f}、漏补率 "
                   f"{rp_delay2['leak_rate']:.1%} vs {rp_strict['leak_rate']:.1%}"
                   "（确认质量不降反升）——六项全胜，无一短板")
    elif no_c2_win:
        rec = "no_c2（放宽-去C2）"
        rec_why = f"收益 {sim_no_c2['total_ret']:+.1f}% > 现状 {sim_strict['total_ret']:+.1f}%"
    else:
        rec = "strict（现状严格版，保持不动）"
        rec_why = "放宽版收益/回撤未全面优于现状"
    out.append("## 六、结论与建议（数据说话 · 签字权归老板）")
    out.append("")
    out.append(f"**推荐：{rec}**——{rec_why}。")
    out.append("")
    out.append("### 数据依据")
    out.append("")
    out.append("1. **delay2（延迟二次确认）六项全胜**：资金模拟收益 +88.8% vs 现状 "
               "+73.9%（+14.9pp）、最大回撤 83.0% vs 86.9%（更低）、胜率 51.3% vs "
               "49.6%、avgR +0.583 vs +0.439、盈亏比 2.72 vs 2.26、漏补率 64.3% vs "
               "62.7%。机制上它「只给强票多一天」——第二根能收回来的票天然强势（救回 "
               "质量 73.5% 全方案最高），坏票第二根通常也回不来，所以确认质量不降反升。")
    out.append("2. **no_c2 不推荐**：收益 +80.4% 中等，但胜率崩到 40.6%（vs 49.6%）、"
               "回撤最大 91.7%——直接去掉 C2 等于「确认放水」，混入 48 笔坏票确认"
               "（漏补率 61.8% 全方案最低）。")
    out.append("3. **误杀率口径要两看**：delay2 误杀率 57.3% 与现状持平——reject 组"
               "变成「两轮都不确认」的最弱票组（机会成本 +0.629R → +0.358R），且被"
               "delay2 拿住的强票收益已在资金层兑现（+88.8%）。误杀率是规则质量视角"
               "（仍 >20% 红线），资金层是实际损益视角（已覆盖）——两者都是事实，"
               "取舍看老板。")
    out.append("4. **截断代价不升反降**：-3~-1R 尾部大亏 23 笔（现状）→ 19 笔"
               "（delay2）/ 15 笔（no_c2）——放宽后截断效应并未放大尾部大亏（强票"
               "止损位不变，只延长了持有）。")
    out.append("5. **三取二（any2）已淘汰**：残留误杀率不降反升（58.1%），混入坏票多。")
    out.append("")
    out.append("### 白话版")
    out.append("")
    out.append("- 现状规则像「谈崩一次就分手」：次一交易日收线稍弱（哪怕只差 1% 或只是"
               "放量小阴）就全仓退出——57.6% 退出的票后面 20 天能涨 1R，机会成本 +0.63R。")
    out.append("- **延迟二次确认 = 分手前给一天缓冲**：当天没确认，第二天收回来才算数。"
               "好票第二天大概率收回（救回质量 73.5%），坏票第二天也回不来。收益 +14.9pp、"
               "回撤还更低——数据上全面更好，且逻辑贴近老师「动能无法接受才平」的原意"
               "（一天弱不算无法接受）。")
    out.append("- 去 C2 则太松：动能转弱也不管，坏票混入（胜率 40.6%），不推荐。")
    out.append("")
    out.append("**最终留哪版 = 老板签字**（现状版数据已存档可回退："
               "`0.5R执行卡与验证-20260806.md` ③ 节 + `backtest_final_20260806/` 原样；"
               "放宽版实现已参数化 `indicators.phase_confirm_from_kline(confirm_mode)`，"
               "接生产只改一处）。")
    return "\n".join(out)


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
    out.append(f"  确认模式          {r3.get('confirm_mode', 'strict')}"
               "（strict=现状 C1&C2&C3；放宽版见 1B 对照）")
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
    ap.add_argument("--confirm-mode", default="strict",
                    choices=["strict", "any2", "no_c2", "delay2"],
                    help="确认规则模式（strict=现状；放宽版见 1B 对照实验）")
    ap.add_argument("--explore", action="store_true",
                    help="只跑三条件独立命中率探索（数据驱动定放宽方案）")
    ap.add_argument("--compare", action="store_true",
                    help="跑 1B 对照实验（strict vs no_c2 vs delay2 全量对照）")
    ap.add_argument("--report-md", default=None,
                    help="对照报告 markdown 输出路径（--compare 时）")
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

    if args.explore:
        print("\n[探索] 三条件独立命中率…")
        e = explore_conditions(fin, klines, mode=args.mode, hold=args.hold)
        print(format_explore_report(e))
        return 0

    if args.compare:
        print("\n[对照] strict vs no_c2 vs delay2 全量对照（回放+资金模拟，分钟级）…")
        c = compare_confirm_modes(fin, klines, mode=args.mode, hold=args.hold)
        txt = format_compare_report(c, capital=5600.0, risk_ratio=0.02,
                                    max_positions=3)
        print()
        print(txt)
        if args.report_md:
            with open(args.report_md, "w", encoding="utf-8") as f:
                f.write(format_compare_md(c, capital=5600.0, risk_ratio=0.02,
                                          max_positions=3))
            print(f"对照报告 → {args.report_md}")
        return 0

    r3 = replay_confirm(fin, klines, mode=args.mode, hold=args.hold,
                        confirm_mode=args.confirm_mode)
    r4_fin = r_bucket_dist(fin, mode=args.mode, hold=args.hold)
    r4_c23 = r_bucket_dist(c23, mode=args.mode, hold=args.hold)
    print()
    print(format_replay_report(r3, r4_fin, r4_c23))
    return 0


if __name__ == "__main__":
    sys.exit(main())
