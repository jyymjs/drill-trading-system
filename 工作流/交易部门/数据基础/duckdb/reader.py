"""duckdb 读层（T-017 P5：主链路切换后的权威数据源）

职责：
- read_kline：对外统一出口——中文列名 K 线（qfq 四价自算 + 派生列），
  供 fetcher.get_daily_kline 的 duckdb 优先分支与回测 CacheDataProvider 使用；
- compute_qfq：等比复权因子法自算前复权（P1/P2 同款算法，自 recon.py 提取共用），
  前复权四价（开/高/低/收）同乘因子——技术指标需要 OHLC 一致口径。

覆盖约定（与旧 CSV 缓存命中语义一致）：
  库内无该 symbol / 请求窗口越界（早于库内起点或晚于库内最新）→ 返回 None，
  由调用方回退下一层（CSV 缓存 → 网络链路）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from 数据基础.duckdb.store import open_db


def read_daily_raw(con, symbol: str) -> pd.DataFrame:
    """库内原始日线（升序）。无该 symbol → 空表"""
    df = con.execute(
        "SELECT date, open, high, low, close, vol, amount FROM daily "
        "WHERE symbol=? ORDER BY date", [symbol]).df()
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
    return df


def read_xdxr(con, symbol: str) -> pd.DataFrame:
    """库内除权因子（category=1 分红送配）。无 → 空表"""
    df = con.execute(
        "SELECT date, fenhong, peigujia, songzhuangu, peigu FROM xdxr "
        "WHERE symbol=? AND category=1 ORDER BY date", [symbol]).df()
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
    return df


def compute_qfq(daily: pd.DataFrame, xdxr: pd.DataFrame | None) -> pd.DataFrame:
    """等比复权因子法自算前复权（P1/P2 同款算法）：四价（开/高/低/收）同乘因子

    Args:
        daily: 原始日线（date/open/high/low/close/vol/amount，升序）
        xdxr: 除权因子（date/fenhong/peigujia/songzhuangu/peigu），可为 None

    Returns:
        增加 factor 与 qfq_open/qfq_high/qfq_low/qfq_close 列的副本
        （factor 语义：该行相对最新价的前复权乘数，最新行 factor=1.0）
    """
    d = daily.copy()
    if d.empty:
        return d
    d["factor"] = 1.0
    if xdxr is not None and len(xdxr):
        x = xdxr.copy()
        x["d"] = x["fenhong"].fillna(0) / 10.0          # 每股派息（元）
        x["s"] = x["songzhuangu"].fillna(0) / 10.0      # 每股送转股数
        x["r"] = x["peigu"].fillna(0) / 10.0            # 每股配股数
        x["p"] = x["peigujia"].fillna(0)                # 配股价
        x = x.merge(d[["date", "close"]].rename(columns={"close": "close_t"}),
                    on="date", how="left")
        x["prev_close"] = np.nan
        idx = d["date"].searchsorted(x["date"])         # 除权日前一交易日
        valid = (idx > 0) & (idx <= len(d))
        x.loc[valid, "prev_close"] = d["close"].iloc[idx[valid] - 1].values
        x = x.dropna(subset=["prev_close"])
        for _, row in x.iterrows():
            pre = row["prev_close"]
            f = (pre - row["d"] + row["p"] * row["r"]) / (pre * (1 + row["s"] + row["r"]))
            if not np.isfinite(f) or f <= 0:
                continue
            d.loc[d["date"] < row["date"], "factor"] *= f
    for c in ("open", "high", "low", "close"):
        d[f"qfq_{c}"] = d[c] * d["factor"]
    return d


def _to_cn_kline(k: pd.DataFrame) -> pd.DataFrame:
    """英文列（含 qfq_* 或原始价）→ 中文列 + 派生列（与 fetcher 输出同构）"""
    price_cols = {c: (f"qfq_{c}" if f"qfq_{c}" in k.columns else c) for c in
                  ("open", "high", "low", "close")}
    out = pd.DataFrame({
        "日期": k["date"],
        "开盘": k[price_cols["open"]],
        "收盘": k[price_cols["close"]],
        "最高": k[price_cols["high"]],
        "最低": k[price_cols["low"]],
        "成交量": k["vol"],
        "成交额": k["amount"],
    })
    out["涨跌幅"] = out["收盘"].pct_change() * 100
    out["涨跌额"] = out["收盘"].diff()
    out["振幅"] = np.where(out["最低"] > 0,
                           (out["最高"] - out["最低"]) / out["最低"] * 100, 0)
    out["换手率"] = 0.0  # duckdb 无换手率，与 pytdx 分支同口径置 0
    return out


def read_kline(symbol: str, start: str | None = None, end: str | None = None,
               db_path=None, adjust: str = "qfq") -> pd.DataFrame | None:
    """读单只 K 线（duckdb 权威源，默认 qfq 四价自算）

    Args:
        symbol: 股票代码
        start/end: 日期 "YYYYMMDD"；None=库内全量（回测时光机用全量历史）
        db_path: 库路径（默认配置 DB_PATH；测试可注入临时库）
        adjust: "qfq"=前复权自算（默认）；其他=原始价

    Returns:
        中文列 DataFrame（日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅/涨跌额/振幅/换手率），
        无该 symbol 或窗口覆盖不足 → None（调用方回退下一层）
    """
    con = open_db(db_path, read_only=True)
    try:
        daily = read_daily_raw(con, symbol)
        if daily is None or daily.empty:
            return None
        start_dt = pd.to_datetime(start) if start else daily["date"].min()
        end_dt = pd.to_datetime(end) if end else daily["date"].max()
        # 覆盖不足（新股/请求晚于库内最新）→ 回退，与旧 CSV 缓存命中语义一致
        if daily["date"].min() > start_dt or daily["date"].max() < end_dt:
            return None
        if adjust == "qfq":
            xdxr = read_xdxr(con, symbol)
            k = compute_qfq(daily, xdxr)
        else:
            k = daily
        mask = (k["date"] >= start_dt) & (k["date"] <= end_dt)
        return _to_cn_kline(k[mask].reset_index(drop=True))
    finally:
        con.close()
