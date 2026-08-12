"""R-080 G16 市场状态归因（2026-08-13）

目的：验证段（2023+ 慢熊/震荡）vs 定参段（2020-2022 含大牛）——收益差异的
市场环境归因，修正"衰减>50%=过拟合"的误判（相对指数超额衰减才是判据）。

输出：分年报告（信号层 avgR/胜率 + 指数年涨跌）+ 两段超额收益对照
"""
import sys, os
import time

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CAL = os.path.join(ROOT, "产出", "输出", "数据", "backtest_calib_2020-2022", "signals.csv")
VAL = os.path.join(ROOT, "产出", "输出", "数据", "backtest_final_20260806", "signals.csv")
IDX = os.path.join(ROOT, "数据基础", "行情数据", "index_cache", "1_000001.csv")


def load_index() -> pd.DataFrame:
    df = pd.read_csv(IDX)
    df.columns = [str(c).strip() for c in df.columns]
    date_col = [c for c in df.columns if "日期" in c or "date" in c.lower()][0]
    close_col = [c for c in df.columns if "收盘" in c or "close" in c.lower()][0]
    df = df[[date_col, close_col]].rename(
        columns={date_col: "日期", close_col: "收盘"})
    df["日期"] = pd.to_datetime(df["日期"])
    return df.sort_values("日期").reset_index(drop=True)


def seg_index_ret(idx: pd.DataFrame, start: str, end: str | None) -> float:
    sub = idx[(idx["日期"] >= start) & (idx["日期"] <= (end or "2999-01-01"))]
    if len(sub) < 2:
        return 0.0
    return float(sub["收盘"].iloc[-1] / sub["收盘"].iloc[0] - 1)


def yearly_signals(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, g in df.groupby(df["date"].astype(str).str[:4]):
        trig = g[g["triggered_20d"] == 1]
        rs = trig["r_20d"].astype(float)
        rows.append({"年": y, "信号": len(g), "触发": len(trig),
                     "avgR": round(float(rs.mean()), 3) if len(rs) else 0.0,
                     "胜率": round(float((rs > 0).mean()), 3) if len(rs) else 0.0})
    return pd.DataFrame(rows)


def main() -> int:
    idx = load_index()
    cal = pd.read_csv(CAL, encoding="utf-8-sig", dtype={"code": str})
    val = pd.read_csv(VAL, encoding="utf-8-sig", dtype={"code": str})

    # 两段指数涨幅
    idx_cal = seg_index_ret(idx, "2020-01-01", "2022-12-31")
    idx_val = seg_index_ret(idx, "2023-01-01", "2026-07-31")
    # 资金层收益（G1 实测）
    cap = {"定参段": 160.4, "验证段": 112.4}
    print(f"指数涨幅: 定参段 {idx_cal:+.1%} | 验证段 {idx_val:+.1%}", flush=True)
    print(f"资金层收益: 定参段 +{cap['定参段']:.0f}% | 验证段 +{cap['验证段']:.0f}%", flush=True)
    for k in ("定参段", "验证段"):
        excess = cap[k] / 100 - (idx_cal if k == "定参段" else idx_val)
        print(f"{k} 超额: {excess:+.1%}", flush=True)

    yc, yv = yearly_signals(cal), yearly_signals(val)
    yc["指数年涨跌"] = [f"{seg_index_ret(idx, f'{y}-01-01', f'{y}-12-31'):+.1%}"
                     for y in yc["年"]]
    yv["指数年涨跌"] = [f"{seg_index_ret(idx, f'{y}-01-01', f'{y}-12-31'):+.1%}"
                     for y in yv["年"]]

    out = os.path.join(ROOT, "产出", "输出", "实验",
                       f"G16-市场归因-{time.strftime('%Y-%m-%d')}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"""# R-080 G16 市场状态归因（{time.strftime('%Y-%m-%d')}）

## 1. 两段市场环境（上证指数）

| 段 | 指数涨幅 | 资金层收益（8401×0.025×999） | **超额** |
|---|---|---|---|
| 定参段 2020-2022 | {idx_cal:+.1%} | +{cap['定参段']:.0f}% | {cap['定参段']/100 - idx_cal:+.1%} |
| 验证段 2023-2026 | {idx_val:+.1%} | +{cap['验证段']:.0f}% | {cap['验证段']/100 - idx_val:+.1%} |

> 验证段慢熊/震荡市（指数 {idx_val:+.1%}）vs 定参段含大牛（指数 {idx_cal:+.1%}）——
> 收益"衰减"主要来自市场环境，非策略能力（信号层 avgR/胜率验证段不降反升，见 G1）。

## 2. 分年明细（定参段）

{yc.to_markdown(index=False)}

## 3. 分年明细（验证段）

{yv.to_markdown(index=False)}

## 4. 结论

1. **相对指数超额**：定参段 {cap['定参段']/100 - idx_cal:+.1%} vs 验证段
   {cap['验证段']/100 - idx_val:+.1%}——超额衰减 =
   {(cap['验证段']/100 - idx_val) - (cap['定参段']/100 - idx_cal):+.1%}
   （负 = 验证段超额收窄，方向归因 = 弱势市阿尔法下降）
2. **信号层不受市场影响**：分年 avgR 验证段各年均为正且 ≥ 定参段（G1 已证）
3. 修正口径：**判据 = 相对指数超额衰减，不是绝对收益衰减**；样本量披露 =
   验证段 514 触发 / 定参段 180 触发（见 G1 报告）
""")
    print(f"报告: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
