"""R-080 G12 复权待核核销（2026-08-13 · 2020+ 优先）

核销方法（方案：raw/qfq 反推因子比对）：
  A 类（送转缺除权除息）：读当日 raw 跳变 vs 送转/派息理论因子 → 吻合
      = 真实除权漏记（xdxr 缺 category=1，需补录）；不吻合 = 假阳性（记录多余）
  B 类（跳变超限无记录）：跳变 vs 常见除权比例网格（10送N ± 派息）→ 吻合
      = 疑似漏记；不吻合 = 涨跌停/新股等正常（假阳性）

用法：python -m 数据基础.duckdb.g12_verify [--since 2020-01-01]
输出：duckdb_runtime/xdxr_verify_<日期>.csv（核销结论）+ 待补清单
"""
import argparse
import sys
import time

import numpy as np
import pandas as pd
from 数据基础.duckdb.config import DB_PATH, RUNTIME_DIR

sys.stdout.reconfigure(encoding="utf-8")

CHECK_CSV = RUNTIME_DIR / f"xdxr_check_{time.strftime('%Y-%m-%d')}.csv"
MATCH_TOL = 0.20          # 实际跳变 vs 理论因子 相对容差


def _gap(x):
    return float(x) if x == x else np.nan


def _nearby_xdxr1(con, sym: str, date: str, days: int = 3) -> list:
    """查该股 date±days 天内 category=1 记录（排除日期错位的规则 A 命中）"""
    import datetime as _dt
    d0 = (_dt.date.fromisoformat(date) - _dt.timedelta(days=days)).isoformat()
    d1 = (_dt.date.fromisoformat(date) + _dt.timedelta(days=days)).isoformat()
    return con.execute(
        "SELECT date FROM xdxr WHERE symbol=? AND category=1 "
        "AND date BETWEEN ? AND ? ORDER BY date", [sym, d0, d1]).fetchall()


def verify_a(con, sym: str, date: str) -> dict:
    """A 类核销：邻日 1 类排除 + 实际跳变判定

    9/15 行不带送转数（songzhuangu=0，明细在 category=1 行）→ 无法算理论因子，
    改两段判定：① 邻日已有 category=1 → 记录错位假阳性；② 当日跳变 ≤-1%
    （除权下调特征）→ 真实除权漏记；否则记录多余。
    """
    daily = con.execute(
        "SELECT date, open, close FROM daily WHERE symbol=? AND date<=? "
        "ORDER BY date DESC LIMIT 2", [sym, date]).df()
    if len(daily) < 2:
        return {"verdict": "数据不足", "detail": "无前收/当日"}
    prev_close = float(daily.iloc[1]["close"])
    cur_open = float(daily.iloc[0]["open"])
    act_gap = cur_open / prev_close - 1.0
    near = _nearby_xdxr1(con, sym, date)
    if near:
        return {"verdict": "假阳性(记录错位)", "detail":
                f"邻日已有 category=1（{near[0][0]}），非漏记（跳变{act_gap:.2%}）"}
    if act_gap <= -0.01:
        return {"verdict": "真实除权漏记", "detail":
                f"跳变{act_gap:.2%} ≤ -1%（除权下调特征，无邻日 1 类）"}
    return {"verdict": "假阳性(记录多余)", "detail":
            f"跳变{act_gap:.2%} 无除权特征（9/15 记录多余）"}


def verify_b(con, sym: str, date: str, jump_pct: float) -> dict:
    """B 类核销：跳变 vs 常见除权网格（10送N + 派息 + 配股）"""
    daily = con.execute(
        "SELECT date, open, close FROM daily WHERE symbol=? AND date<=? "
        "ORDER BY date DESC LIMIT 2", [sym, date]).df()
    if len(daily) < 2:
        return {"verdict": "数据不足", "detail": "无前收/当日"}
    prev_close = float(daily.iloc[1]["close"])
    cur_open = float(daily.iloc[0]["open"])
    act_gap = cur_open / prev_close - 1.0
    # 常见除权组合网格：10送N（1..20）+ 派息 0/1/2/5 元 + 配股 0
    best = None
    for n in range(1, 21):
        for div in (0.0, 1.0, 2.0, 5.0):
            theo_gap = (prev_close - div / 10.0) / (prev_close * (1 + n / 10.0)) - 1.0
            if abs(act_gap - theo_gap) / max(abs(theo_gap), 1e-9) <= MATCH_TOL:
                best = (n, div, theo_gap)
    near = _nearby_xdxr1(con, sym, date)
    if best is not None:
        n, div, tg = best
        if near:
            return {"verdict": "假阳性(记录错位)", "detail":
                    f"邻日已有 category=1（{near[0][0]}），跳变{act_gap:.2%}≈10送{n}派{div}"}
        return {"verdict": "疑似漏记", "detail":
                f"跳变{act_gap:.2%} ≈ 10送{n}派{div}理论{tg:.2%}（无邻日 1 类）"}
    return {"verdict": "正常波动/假阳性", "detail": f"跳变{act_gap:.2%}无匹配除权组合"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2020-01-01", help="核销起点（默认 2020+ 优先）")
    args = ap.parse_args(argv)

    import duckdb
    chk = pd.read_csv(CHECK_CSV, encoding="utf-8-sig", dtype={"symbol": str})
    sub = chk[chk["date"] >= args.since].copy()
    print(f"待核: {len(chk)} 条（全库）→ 核销 {len(sub)} 条（{args.since}+）", flush=True)

    con = duckdb.connect(DB_PATH, read_only=True)
    rows = []
    for i, (_, r) in enumerate(sub.iterrows(), 1):
        if r["rule"] == "A":
            v = verify_a(con, r["symbol"], r["date"])
        else:
            v = verify_b(con, r["symbol"], r["date"], r.get("jump_pct", 0))
        rows.append({"symbol": r["symbol"], "rule": r["rule"], "date": r["date"],
                     **v})
        if i % 200 == 0:
            print(f"  [{i}/{len(sub)}]", flush=True)
    con.close()

    res = pd.DataFrame(rows)
    out = RUNTIME_DIR / f"xdxr_verify_{time.strftime('%Y-%m-%d')}.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    vc = res["verdict"].value_counts().to_dict()
    print(f"核销结论分布: {vc}", flush=True)
    need = res[res["verdict"].isin(["真实除权漏记", "疑似漏记"])]
    print(f"需补录: {len(need)} 条（占核销 {len(res)} 条的 {len(need)/max(len(res),1):.1%}）",
          flush=True)
    if len(need):
        print("示例:", need.head(8)[["symbol", "rule", "date", "verdict", "detail"]]
              .to_string(index=False), flush=True)
    print(f"输出: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
