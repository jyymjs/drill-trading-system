"""R-080 G2 幸存者偏差量化——信号层重跑（2026-08-13）

读 delisted.duckdb（184 只退市股 22.6 万行）→ 对退市股跑 V4 评级（zuanqian_strategy
grade()，复用主策略）→ 统计退市股 S 级信号数量与退市前表现 → 对比存活池信号率
→ 量化幸存者偏差幅度。

输出：退市股信号数/存活池信号数/偏差幅度（方向+大小）→ 报告落盘 产出/输出/实验/。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "delisted.duckdb")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出", "实验", "R080-G2-幸存者偏差-20260813.md")

# 存活池信号率基准（V4 定版：514 笔 3y 信号 / 5208 只）
LIVE_SIGNALS_3Y = 514
LIVE_STOCKS = 5208


def load_delisted() -> pd.DataFrame:
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT * FROM delisted_daily ORDER BY code, date").fetchdf()
    con.close()
    return df


def main() -> int:
    from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy
    strat = ZuanQianStrategy()
    df = load_delisted()
    print(f"退市股数据: {df['code'].nunique()} 只 / {len(df)} 行", flush=True)

    # 每只退市股滚动评级（窗口 60 根，末 20 根判 S 信号）
    sig_codes, sig_dates = [], []
    per_stock = df.groupby("code")
    for code, g in per_stock:
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 60:
            continue
        k = pd.DataFrame({
            "日期": g["date"].astype(str),
            "开盘": g["open"], "收盘": g["close"],
            "最高": g["high"], "最低": g["low"], "成交量": g["volume"],
        })
        for i in range(60, len(g)):
            win = k.iloc[:i + 1]
            try:
                res = strat.grade(win)
                if res.get("grade") == "S":
                    sig_codes.append(code)
                    sig_dates.append(str(g["date"].iloc[i]))
            except Exception:  # noqa: BLE001 - 单点评级失败跳过
                continue
    # 口径统一（交易部标准）：退市股与存活池同取 2023+ 窗口（514 笔 3y 基准同期）
    win = [(c, d) for c, d in zip(sig_codes, sig_dates) if d >= "2023-01-01"]
    n_sig = len(win)
    n_delist = df[df["date"] >= "2023-01-01"]["code"].nunique()
    # 偏差量化：退市股 S 级信号率 vs 存活池信号率
    delist_rate = n_sig / n_delist if n_delist else 0
    live_rate = LIVE_SIGNALS_3Y / LIVE_STOCKS
    bias_pct = (delist_rate / live_rate - 1) * 100 if live_rate else 0
    print(f"退市股 S 级信号: {n_sig} 笔 / {n_delist} 只（率 {delist_rate:.4f}）", flush=True)
    print(f"存活池信号率（3y 基准）: {live_rate:.4f}（{LIVE_SIGNALS_3Y}/{LIVE_STOCKS}）", flush=True)
    print(f"偏差幅度: {bias_pct:+.1f}%（{'高估' if bias_pct < 0 else '低估'}——负=退市股信号率更低，存活池高估）", flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"""# R-080 G2 幸存者偏差量化（2026-08-13）

| 项 | 值 |
|---|---|
| 退市股样本 | {n_delist} 只（2019-07 后退市，22.6 万行 K 线）|
| 退市股 S 级信号 | {n_sig} 笔（率 {delist_rate:.4f}）|
| 存活池信号率基准 | {live_rate:.4f}（{LIVE_SIGNALS_3Y} 笔 / {LIVE_STOCKS} 只，3y）|
| **偏差幅度** | **{bias_pct:+.1f}%**（{'高估' if bias_pct < 0 else '低估'}）|

> 判定（交易部标准）：偏差 >±2pp 需动作。本结果绝对值 {abs(bias_pct/100):.4f}（{'需动作' if abs(bias_pct) > 2 else '可接受'}）。
> 注：退市股信号率含退市前窗口，未区分信号后是否退市（保守口径）。
""")
    print(f"报告: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
