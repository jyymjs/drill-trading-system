"""E1 平台宽度质量判定（2026-08-13）

宽度 = TY 区间振幅（(ty_high-ty_low)/ty_low，prebreak_grade 返回 ty 边界）。
A 宽度网格：<3% / 3-5% / 5-8% / >8% 分档 → avgR/胜率（26y + 7y 子窗）；
  分评级×分宽度交叉表（同评级内比宽度，防评级混淆）；邻域检查（相邻档差 <0.05R）
B 宽度过滤增量作用（错杀/冗余分解：过滤掉的信号 vs 保留）
C 资金层复验（10 万 × 0.025 × 999，全/≤8%/≤5% 子集 × T-020 双口径）
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import numpy as np
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
SIG = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出", "归档",
                   "旧回测-20260813", "backtest_r43_t2_T8", "signals.csv")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                   "实验", "E1-平台宽度-20260813.md")

BANDS = [(0.00, 0.03, "<3%"), (0.03, 0.05, "3-5%"), (0.05, 0.08, "5-8%"),
         (0.08, 9.99, ">8%")]


def load_kline(con, symbol: str) -> pd.DataFrame | None:
    from 数据基础.duckdb.reader import compute_qfq, _to_cn_kline
    daily = con.execute(
        "SELECT date, open, high, low, close, vol, amount FROM daily "
        "WHERE symbol=? ORDER BY date", [symbol]).df()
    xdxr = con.execute(
        "SELECT date, fenhong, peigujia, songzhuangu, peigu FROM xdxr "
        "WHERE symbol=? AND category=1 ORDER BY date", [symbol]).df()
    if daily.empty:
        return None
    return _to_cn_kline(compute_qfq(daily, xdxr if len(xdxr) else None)
                        .reset_index(drop=True))


def _band(w: float) -> str:
    for lo, hi, tag in BANDS:
        if lo <= w < hi:
            return tag
    return ">8%"


def main() -> int:
    from 分析决策.分析.indicators import all_indicators
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy

    sig = pd.read_csv(SIG, encoding="utf-8-sig", dtype={"code": str})
    trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)].copy()
    print(f"T8 prebreak 触发集: {len(trig)} 笔", flush=True)

    con = duckdb.connect(DB, read_only=True)
    strat = ZuanQianStrategy()
    rows = []
    t0 = time.time()
    for i, (_, r) in enumerate(trig.iterrows(), 1):
        k = load_kline(con, str(r["code"]))
        if k is None:
            continue
        end = pd.to_datetime(r["date"])
        sub = k[k["日期"] <= end]
        if len(sub) < 60:
            continue
        sub = sub.copy()
        sub.attrs["code"] = str(r["code"])
        sub = all_indicators(sub, needed_cols=strat.required_indicators)
        try:
            res = strat.prebreak_grade(sub)
        except Exception:  # noqa: BLE001
            continue
        th, tl = res.get("ty_high", 0) or 0, res.get("ty_low", 0) or 0
        w = (th - tl) / tl if tl > 0 else np.nan
        rows.append({"code": r["code"], "date": r["date"], "r_20d": r["r_20d"],
                     "grade": r["grade"], "width": w, "band": _band(w) if w == w else "NA"})
        if i % 300 == 0:
            print(f"  [{i}/{len(trig)}] {time.time()-t0:.0f}s", flush=True)
    con.close()
    df = pd.DataFrame(rows)
    df["year"] = df["date"].astype(str).str[:4]
    df = df[df["band"] != "NA"]
    print(f"分型成功: {len(df)} 笔", flush=True)

    lines = ["# E1 平台宽度质量判定（2026-08-13）", "",
             f"> T8 触发集 {len(df)} 笔（2000-2026）｜宽度 = TY 区间振幅 "
             f"((ty_high-ty_low)/ty_low)", ""]

    def _stats(sub):
        rs = sub["r_20d"].astype(float)
        return (len(sub), round(float(rs.mean()), 3) if len(rs) else 0,
                round(float((rs > 0).mean()), 3) if len(rs) else 0)

    # A 宽度网格
    lines += ["## A 宽度网格（信号层）", "", "| 宽度档 | 笔数 | avgR | 胜率 |", "|---|---|---|---|"]
    band_stats = {}
    for _, _, tag in BANDS:
        s = df[df["band"] == tag]
        n, ar, wr = _stats(s)
        band_stats[tag] = (n, ar)
        lines.append(f"| {tag} | {n} | {ar:+.3f} | {wr:.1%} |")
    sub7 = df[df["year"] >= "2020"]
    lines += ["", "7y 子窗（2020+）："]
    for _, _, tag in BANDS:
        s = sub7[sub7["band"] == tag]
        n, ar, wr = _stats(s)
        lines.append(f"- {tag}: {n} 笔 | avgR {ar:+.3f} | 胜率 {wr:.1%}")
    # 邻域检查
    avgs = [band_stats[t][1] for _, _, t in BANDS]
    lines += ["", f"邻域检查（相邻档差 <0.05R 为稳定）：" +
              "；".join(f"{BANDS[i][2]}→{BANDS[i+1][2]} "
                       f"差 {abs(avgs[i]-avgs[i+1]):.3f}R" for i in range(3))]

    # 分评级交叉
    lines += ["", "## 分评级 × 分宽度交叉（同评级内比宽度）", "",
              "| 评级 | 宽度档 | 笔数 | avgR |", "|---|---|---|---|"]
    for g in ("S", "A"):
        for _, _, tag in BANDS:
            s = df[(df["grade"] == g) & (df["band"] == tag)]
            n, ar, _ = _stats(s)
            if n >= 10:
                lines.append(f"| {g} | {tag} | {n} | {ar:+.3f} |")

    # B 过滤增量
    lines += ["", "## B 宽度过滤增量作用（触发集上）", "",
              "| 过滤 | 保留笔数 | 保留 avgR | 剔除 avgR | 剔除笔数 |", "|---|---|---|---|---|"]
    for cut, tag in [(0.08, "≤8%"), (0.05, "≤5%")]:
        keep = df[df["width"] <= cut]
        drop = df[df["width"] > cut]
        nk, ark, _ = _stats(keep)
        nd, ard, _ = _stats(drop)
        lines.append(f"| {tag} | {nk} | {ark:+.3f} | {ard:+.3f} | {nd} |")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
