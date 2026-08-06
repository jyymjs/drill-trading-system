"""G1 阈值校准扫描（补完计划第二批 · 2026-08-06 工程定案用）

对全市场（duckdb 只读，主仓库库文件）统计"经常跳空/涨跌停"排除率：
- 跳空判定线（|开盘/前收盘-1|）参数化：{3%, 4%, 5%}
- 涨跌停判定线按板块口径：统一 9.5%（现状） vs 分板块（主板 9.5% / 20cm 19.5%）
- 次数阈值：{3, 5}
- 窗口：最近 60 根（与 indicators.gap_limit_detect 一致）
- 复权：qfq 自算（复用 reader.compute_qfq），消除除权日假跳空

输出：分布表（组合 × 板块排除率）+ 明细 CSV（每只股票的 limit/gap 次数）
"""
import os
import sys
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parents[3]  # 交易部门/
sys.path.insert(0, str(PROJ_ROOT))
os.chdir(PROJ_ROOT)

import numpy as np
import pandas as pd

# 主仓库 duckdb（worktree 无数据文件，只读引用主仓库路径）
DB = Path(r"C:/Users/32032/Desktop/deepseek/工作流/交易部门/数据基础/data/t017_p2.duckdb")
OUT_DIR = Path(__file__).resolve().parent

WINDOW = 60        # 与 indicators.GAP_LIMIT_WINDOW 一致
GAP_PCTS = [0.03, 0.04, 0.05]
LIMIT_CFGS = {
    "统一9.5%": {"沪主板": 0.095, "深主板": 0.095, "创业板": 0.095, "科创板": 0.095},
    "分板块(9.5/19.5)": {"沪主板": 0.095, "深主板": 0.095, "创业板": 0.195, "科创板": 0.195},
}
FREQS = [3, 5]


def board_of(code: str) -> str | None:
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("600", "601", "603", "605")):
        return "沪主板"
    if code.startswith(("000", "001", "002", "003")):
        return "深主板"
    return None  # 北交所/其他：不在 A 股扫描池（baostock 口径 sh.6/sz.0/sz.3）


def main() -> None:
    import duckdb
    from 数据基础.duckdb.reader import compute_qfq

    con = duckdb.connect(str(DB), read_only=True)
    print("读取 daily / xdxr ...", flush=True)
    daily = con.execute(
        "SELECT symbol, date, open, high, low, close FROM daily").df()
    xdxr = con.execute(
        "SELECT symbol, date, fenhong, peigujia, songzhuangu, peigu "
        "FROM xdxr WHERE category=1").df()
    con.close()

    # 只取最近 300 根做 qfq（窗口 60 根的前置复权余量足够）
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date").groupby("symbol", sort=False).tail(300)
    daily["date"] = pd.to_datetime(daily["date"])

    print(f"股票数: {daily['symbol'].nunique()}  行数: {len(daily)}", flush=True)

    rows = []
    for symbol, d in daily.groupby("symbol", sort=False):
        board = board_of(symbol)
        if board is None or len(d) < 30:
            continue
        xd = xdxr[xdxr["symbol"] == symbol] if len(xdxr) else xdxr
        k = compute_qfq(d.reset_index(drop=True), xd)
        k = k.tail(WINDOW).reset_index(drop=True)
        close = k["qfq_close"].values.astype(float)
        op = k["qfq_open"].values.astype(float)
        chg = np.zeros(len(close))
        gap = np.zeros(len(close))
        for i in range(1, len(close)):
            pc = close[i - 1]
            if pc > 0:
                chg[i] = abs(close[i] / pc - 1.0)
                gap[i] = abs(op[i] / pc - 1.0)
        rows.append({"symbol": symbol, "board": board,
                     "limit_days_max": int((chg >= 0.095).sum()),
                     "limit_days_20cm": int((chg >= 0.195).sum()),
                     "gap_3": int((gap >= 0.03).sum()),
                     "gap_4": int((gap >= 0.04).sum()),
                     "gap_5": int((gap >= 0.05).sum()),
                     "n": len(close)})

    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "detail.csv", index=False, encoding="utf-8-sig")
    print(f"明细 {len(df)} 只 → detail.csv", flush=True)

    # 生产链路口径：池级先剔 ST（scanner.scan 2026-08-06 老板拍板）再进策略
    # → 排除率统计剔除 ST 后才是 G1 对实际候选池的排除率
    names = pd.read_csv(OUT_DIR / "stock_list_20260806.csv",
                        encoding="utf-8-sig")
    name_map = dict(zip(names["code"].astype(str), names["name"]))
    df["name"] = df["symbol"].map(name_map)
    df["is_st"] = df["name"].fillna("").apply(
        lambda n: "ST" in n.replace(" ", "").upper()[:4])
    n_st = int(df["is_st"].sum())
    df = df[~df["is_st"]].reset_index(drop=True)
    print(f"剔除 ST {n_st} 只 → 扫描池口径 {len(df)} 只", flush=True)

    # ── 分布表：组合 × 板块排除率（含/不含 ST 两口径） ──
    lines = []
    lines.append("| 跳空线 | 涨跌停口径 | 次数 | 沪主板 | 深主板 | 创业板 | 科创板 | 主板合计 | 20cm合计 | 全市场 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    # 全市场 = 各股按所属板块涨跌停线 + 统一跳空线统计
    df["lim20"] = np.where(df["board"].isin(["创业板", "科创板"]),
                           df["limit_days_20cm"], df["limit_days_max"])
    for gp in GAP_PCTS:
        gap_col = {0.03: "gap_3", 0.04: "gap_4", 0.05: "gap_5"}[gp]
        for cfg_name, cfg in LIMIT_CFGS.items():
            lim_all = df["lim20"] if cfg["创业板"] == 0.195 else df["limit_days_max"]
            for freq in FREQS:
                cells = []
                for board in ["沪主板", "深主板", "创业板", "科创板"]:
                    sub = df[df["board"] == board]
                    lim_col = "limit_days_20cm" if cfg[board] == 0.195 else "limit_days_max"
                    ex = ((sub[lim_col] + sub[gap_col]) >= freq)
                    cells.append(f"{ex.mean()*100:.1f}%")
                main_all = df[df["board"].isin(["沪主板", "深主板"])]
                ex_m = ((main_all["limit_days_max"] + main_all[gap_col]) >= freq)
                c20 = df[df["board"].isin(["创业板", "科创板"])]
                ex_c = ((c20["limit_days_20cm"] + c20[gap_col]) >= freq)
                ex_all = ((lim_all + df[gap_col]) >= freq)
                lines.append(
                    f"| {gp*100:.0f}% | {cfg_name} | {freq} | {cells[0]} | {cells[1]} | "
                    f"{cells[2]} | {cells[3]} | {ex_m.mean()*100:.1f}% | "
                    f"{ex_c.mean()*100:.1f}% | {ex_all.mean()*100:.1f}% |")

    report = "\n".join(lines)
    (OUT_DIR / "threshold_table.md").write_text(report, encoding="utf-8")
    print("\n═══ 排除率分布表（窗口 60 根，剔除 ST 后 {n} 只）═══".format(n=len(df)))
    print(report)

    # ── 现状基线校验（统一 9.5% + 3% + 3 次）──
    sub = df[df["board"].isin(["沪主板", "深主板"])]
    ex_m = ((sub["limit_days_max"] + sub["gap_3"]) >= 3)
    ex_all = ((df["limit_days_max"] + df["gap_3"]) >= 3)
    print(f"\n现状基线（统一9.5% + 3%跳空 + 3次）排除率：主板 {ex_m.mean()*100:.1f}% / 全市场 {ex_all.mean()*100:.1f}%")
    print(f"板块样本数：{df.groupby('board')['symbol'].count().to_dict()}")
    # 各板块涨跌停事件分布（佐证 20cm 需分线）
    for board in ["沪主板", "深主板", "创业板", "科创板"]:
        s = df[df["board"] == board]
        print(f"{board}: 60根内≥1次≥9.5%事件 {((s['limit_days_max']>=1).mean()*100):.1f}% | "
              f"≥1次≥19.5%事件 {((s['limit_days_20cm']>=1).mean()*100):.1f}% | "
              f"≥3次≥19.5%事件 {((s['limit_days_20cm']>=3).mean()*100):.1f}%")


if __name__ == "__main__":
    sys.exit(main())
