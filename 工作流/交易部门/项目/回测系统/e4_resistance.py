"""E4 平台突破 vs 阻力突破语义校验（2026-08-13）

分型：prebreak 触发集（T8 26y）→ 每笔信号重读 K 线 →
  过阻力突破（trigger ≥ 信号日前 120/250 日高点）vs 平台内突破（trigger < 前高）
→ 两型质量对照（avgR/胜率，26y + 7y 子窗）+ 000597 评级归因。

密集成交区辅助口径（wvma ±3%）作为交叉验证。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import numpy as np
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")
SIG = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出", "归档",
                   "旧回测-20260813", "backtest_r43_t2_T8", "signals.csv")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                   "实验", "E4-阻力校验-20260813.md")


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


def classify(row, k: pd.DataFrame) -> dict:
    """分型：trigger vs 信号日前 120/250 日高点；密集成交区辅助"""
    d = pd.to_datetime(row["date"])
    sub = k[k["日期"] < d]
    out = {"n_bars": len(sub), "hi120": np.nan, "hi250": np.nan, "wvma": np.nan,
           "type120": "?", "type250": "?"}
    if len(sub) >= 30:
        hi120 = sub["最高"].iloc[-120:].max()
        out["hi120"] = float(hi120)
        out["type120"] = "过阻力" if row["trigger"] >= hi120 else "平台内"
    if len(sub) >= 30:
        hi250 = sub["最高"].iloc[-250:].max()
        out["hi250"] = float(hi250)
        out["type250"] = "过阻力" if row["trigger"] >= hi250 else "平台内"
    if len(sub) >= 60:
        v = sub["成交量"].iloc[-60:].values
        w = v / v.sum()
        wv = float((sub["最高"].iloc[-60:] + sub["最低"].iloc[-60:]).values / 2 @ w)
        out["wvma"] = wv
        out["dense"] = "过密集区" if row["trigger"] >= wv else "密集区内"
    return out


def seg_stats(df: pd.DataFrame) -> dict:
    rs = df["r_20d"].astype(float)
    return {"n": len(df), "avg_r": round(float(rs.mean()), 3) if len(rs) else 0,
            "win": round(float((rs > 0).mean()), 3) if len(rs) else 0}


def main() -> int:
    sig = pd.read_csv(SIG, encoding="utf-8-sig", dtype={"code": str})
    trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)].copy()
    print(f"T8 prebreak 触发集: {len(trig)} 笔", flush=True)

    con = duckdb.connect(DB, read_only=True)
    rows = []
    for i, (_, r) in enumerate(trig.iterrows(), 1):
        k = load_kline(con, str(r["code"]))
        if k is None:
            continue
        c = classify(r, k)
        rows.append({**r[["code", "date", "trigger", "r_20d"]].to_dict(), **c})
        if i % 200 == 0:
            print(f"  [{i}/{len(trig)}]", flush=True)
    con.close()
    df = pd.DataFrame(rows)
    df["year"] = df["date"].astype(str).str[:4]

    lines = ["# E4 平台突破 vs 阻力突破语义校验（2026-08-13）", "",
             f"> T8 触发集 {len(df)} 笔（2000-2026）｜分型口径：trigger ≥ 前高 = 过阻力",
             ""]
    for tag, col in [("120 日前高", "type120"), ("250 日前高", "type250")]:
        lines += [f"## {tag} 分型对照", "", "| 类型 | 笔数 | avgR | 胜率 |", "|---|---|---|---|"]
        for t in ("过阻力", "平台内"):
            s = seg_stats(df[df[col] == t])
            lines.append(f"| {t} | {s['n']} | {s['avg_r']:+.3f} | {s['win']:.1%} |")
        # 7y 子窗（2020+）
        sub = df[df["year"] >= "2020"]
        lines += ["", "7y 子窗（2020+）："]
        for t in ("过阻力", "平台内"):
            s = seg_stats(sub[sub[col] == t])
            lines.append(f"- {t}: {s['n']} 笔 | avgR {s['avg_r']:+.3f} | 胜率 {s['win']:.1%}")
        lines.append("")
    # 密集区辅助
    lines += ["## 密集成交区辅助（wvma ±60 日）", "", "| 类型 | 笔数 | avgR | 胜率 |", "|---|---|---|---|"]
    for t in ("过密集区", "密集区内"):
        s = seg_stats(df[df["dense"] == t])
        lines.append(f"| {t} | {s['n']} | {s['avg_r']:+.3f} | {s['win']:.1%} |")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
