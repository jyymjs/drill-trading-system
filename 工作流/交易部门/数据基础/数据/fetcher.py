"""数据获取 - duckdb(权威源, T-017 P5) + pytdx(通达信直连) + baostock + akshare

数据源优先级（2026-08-05 P5 主链路切换）：
  1. duckdb 库（数据基础/data/t017_p2.duckdb，全量历史 1990 起）→ 原始价 + 因子自算 qfq
  2. CSV 缓存（已 deprecated，降级为 fallback；旧文件保留可读，网络回退时仍写）
  3. pytdx（通达信协议直连，最快 ~0.1-0.3秒/只）→ 不复权数据
  4. baostock（~2-3秒/只，fallback）→ 前复权(adjustflag=2)
  5. akshare（最慢，最后备选）→ 前复权(qfq)

注意：网络三源复权方式不一致（pytdx不复权/baostock前复权/akshare前复权），
duckdb 分支统一前复权口径（因子自算 qfq），为主链路默认数据。
技术形态识别（DL/PT/LK/TY）对复权不敏感，但均线/价格阈值可能有轻微偏差。
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from 数据基础.数据.cache import read_cache, write_cache
from 数据基础.配置.settings import (
    KLINE_ADJUST,
    KLINE_CACHE_DAYS,
    KLINE_YEARS,
    STOCK_LIST_CACHE_DAYS,
)

# ── pytdx ──
try:
    from pytdx.hq import TdxHq_API
    HAS_PYTdx = True
except ImportError:
    HAS_PYTdx = False

# ── 通达信服务器列表（已验证可用，按速度排序） ──
TDX_SERVERS = [
    ("180.153.18.170", 7709),   # ✅ 最快 ~0.06秒
    ("60.191.117.167", 7709),   # ✅ 稳定 ~0.07秒
    ("119.147.212.81", 7709),   # ⚠️ 部分受限
    ("112.74.214.43", 7709),    # ⚠️ 部分受限
]


def _get_market_code(symbol: str) -> int:
    """获取 pytdx 市场代码：0=深圳 1=上海"""
    if symbol.startswith(("6", "5")):
        return 1
    return 0


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名"""
    rename_map = {"股票代码": "代码"}
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    if "日期" in df.columns:
        df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    return df


# ════════════════════════════════════════════════════════════
#  pytdx 数据源（首选，最快）
# ════════════════════════════════════════════════════════════

def _fetch_by_pytdx(symbol: str, years: int = KLINE_YEARS) -> pd.DataFrame | None:
    """通过 pytdx（通达信协议）获取日K线数据

    Returns:
        DataFrame 或 None
    """
    if not HAS_PYTdx:
        return None

    market = _get_market_code(symbol)
    # 估算需要多少条数据（一年约 250 交易日）
    # 注意：pytdx 单次最多返回约 800 条，超过 3 年数据可能截断
    count = min(max(years * 250, 800), 800)

    for host, port in TDX_SERVERS:
        try:
            api = TdxHq_API()
            api.connect(host, port)
            # category=9 表示日线
            data = api.get_security_bars(9, market, symbol, 0, count)
            api.disconnect()

            if data is None or len(data) == 0:
                continue

            # 解析为 DataFrame
            rows = []
            for bar in data:
                dt = datetime(bar.get("year", 2000),
                              bar.get("month", 1),
                              bar.get("day", 1))
                rows.append({
                    "日期": dt,
                    "开盘": bar.get("open", 0),
                    "收盘": bar.get("close", 0),
                    "最高": bar.get("high", 0),
                    "最低": bar.get("low", 0),
                    "成交量": bar.get("vol", 0),
                    "成交额": bar.get("amount", 0) * 1.0,  # pytdx 单位：元
                })

            if not rows:
                continue

            df = pd.DataFrame(rows)
            df = df.sort_values("日期").reset_index(drop=True)

            # 计算派生字段
            df["涨跌幅"] = df["收盘"].pct_change() * 100
            df["涨跌额"] = df["收盘"].diff()
            df["振幅"] = np.where(
                df["最低"] > 0,
                (df["最高"] - df["最低"]) / df["最低"] * 100,
                0,
            )
            df["换手率"] = 0.0  # pytdx 日线不提供换手率

            # 去除全零的无效行
            df = df[df["收盘"] > 0].reset_index(drop=True)

            return df

        except Exception:
            continue

    return None


# ════════════════════════════════════════════════════════════
#  baostock 数据源（fallback）
# ════════════════════════════════════════════════════════════

def _baostock_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "51")):
        return "sh."
    return "sz."


def _fetch_by_baostock(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """通过 baostock 获取日K线"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            return None

        def fmt(s): return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        prefix = _baostock_prefix(symbol)
        rs = bs.query_history_k_data_plus(
            f"{prefix}{symbol}",
            "date,open,high,low,close,volume,amount,turn",
            start_date=fmt(start), end_date=fmt(end),
            frequency="d", adjustflag="2",
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["日期", "开盘", "最高", "最低", "收盘",
                                          "成交量", "成交额", "换手率"])
        for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.sort_values("日期").reset_index(drop=True)
        df["涨跌幅"] = df["收盘"].pct_change() * 100
        df["振幅"] = np.where(df["最低"] > 0,
                              (df["最高"] - df["最低"]) / df["最低"] * 100, 0)
        df["涨跌额"] = df["收盘"].diff()
        return df
    except ImportError:
        return None
    except Exception:
        try:
            bs.logout()
        except (NameError, AttributeError):
            pass
        return None


# ════════════════════════════════════════════════════════════
#  akshare 数据源（最后备选）
# ════════════════════════════════════════════════════════════

def _fetch_by_akshare(symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame | None:
    """通过 akshare 获取日K线"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start, end_date=end, adjust=adjust,
        )
        if df is not None and not df.empty:
            df = _standardize_columns(df)
            return df
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════
#  对外接口
# ════════════════════════════════════════════════════════════

def _fetch_from_duckdb(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """duckdb 优先分支（T-017 P5 主链路切换）：原始价 + 因子自算 qfq

    库缺失 / 该股未入库 / 请求窗口超出库内覆盖 → None，由调用方回退
    CSV 缓存 → 网络链路。任何异常不阻断主链路（回退兜底）。
    """
    try:
        from 数据基础.duckdb.reader import read_kline
        df = read_kline(symbol, start=start, end=end)
        return df if df is not None and not df.empty else None
    except Exception:  # noqa: BLE001 - 库缺失/损坏一律回退，主链路不受影响
        return None


def get_daily_kline(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str = KLINE_ADJUST,
    use_cache: bool = True,
) -> pd.DataFrame:
    """获取日K线数据

    数据源优先级（P5 主链路切换后）:
      duckdb（权威源，因子自算 qfq）→ CSV 缓存（deprecated fallback）→
      pytdx(最快) → baostock → akshare(最慢)
    函数签名保持不变（调用方零改动）。

    Args:
        symbol: 股票代码（如 "000001"）
        start_date: 开始日期 "YYYYMMDD"，默认 3 年前
        end_date: 结束日期 "YYYYMMDD"，默认今天
        adjust: 复权方式（仅网络 akshare 分支支持；duckdb 分支恒自算 qfq）
        use_cache: 是否使用 CSV 缓存

    Returns:
        DataFrame: 日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
    """
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = start_date or (datetime.now() - timedelta(days=365 * KLINE_YEARS)).strftime("%Y%m%d")

    # 1) duckdb 优先（T-017 P5）：全量历史权威源，命中直接返回
    df = _fetch_from_duckdb(symbol, start, end)
    if df is not None and not df.empty:
        return df

    # 2) 尝试 CSV 缓存（已 deprecated：降级为 fallback，以 duckdb 为准）
    if use_cache:
        cached = read_cache(symbol, max_days=KLINE_CACHE_DAYS)
        if cached is not None and not cached.empty:
            earliest = cached["日期"].min()
            latest = cached["日期"].max()
            start_dt = datetime.strptime(start, "%Y%m%d")
            end_dt = datetime.strptime(end, "%Y%m%d")
            if earliest <= start_dt and latest >= end_dt:
                mask = (cached["日期"] >= start_dt) & (cached["日期"] <= end_dt)
                return cached[mask].reset_index(drop=True)

    # 3) 尝试 pytdx（最快）——先缓存全量再过滤，提高后续命中率
    df = _fetch_by_pytdx(symbol)
    if df is not None and not df.empty:
        if use_cache:
            write_cache(symbol, df)   # deprecated CSV 层，仅网络回退时写
        _apply_date_filter(df, start, end)
        return df

    # 4) 尝试 baostock
    df = _fetch_by_baostock(symbol, start, end)
    if df is not None and not df.empty:
        if use_cache:
            write_cache(symbol, df)
        return df

    # 5) 尝试 akshare（最慢）
    df = _fetch_by_akshare(symbol, start, end, adjust)
    if df is not None and not df.empty:
        if use_cache:
            write_cache(symbol, df)
        return df

    return pd.DataFrame()


def _apply_date_filter(df: pd.DataFrame, start: str, end: str):
    """按日期范围过滤（原地修改）"""
    start_dt = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)
    mask = (df["日期"] >= start_dt) & (df["日期"] <= end_dt)
    df.drop(df[~mask].index, inplace=True)


# ════════════════════════════════════════════════════════════
#  pytdx 批量获取（用于增量更新）
# ════════════════════════════════════════════════════════════

def fetch_batch_pytdx(codes: list[str], years: int = KLINE_YEARS) -> dict[str, pd.DataFrame]:
    """用 pytdx 批量获取多只股票K线（支持并发）

    Args:
        codes: 股票代码列表
        years: 拉取年数

    Returns:
        {code: DataFrame, ...}
    """
    result = {}
    for code in codes:
        try:
            df = _fetch_by_pytdx(code, years)
            if df is not None and not df.empty:
                result[code] = df
        except Exception:
            continue
    return result


# ════════════════════════════════════════════════════════════
#  以下接口保持不变（兼容现有调用方）
# ════════════════════════════════════════════════════════════

def get_stock_list(use_cache: bool = True) -> list[dict]:
    """获取全部A股列表（同原实现，通过 baostock/akshare）"""
    cache_key = "__stock_list__"
    if use_cache:
        cached = read_cache(cache_key, STOCK_LIST_CACHE_DAYS)
        if cached is not None:
            return cached.to_dict("records")

    result = _get_stock_list_baostock()
    if not result:
        result = _get_stock_list_akshare()

    if result and use_cache:
        cache_df = pd.DataFrame(result)
        write_cache(cache_key, cache_df)

    return result


def _get_stock_list_baostock() -> list[dict]:
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            return []
        rs = bs.query_stock_basic()
        stocks = []
        while rs.next():
            s = rs.get_row_data()
            code = s[0]
            name = s[1]
            typ = s[4]
            status = s[5]
            if typ == "1" and status == "1" and \
               (code.startswith(("sh.6", "sz.0", "sz.3"))):
                clean_code = code.replace("sh.", "").replace("sz.", "")
                stocks.append({"code": clean_code, "name": name})
        bs.logout()
        return stocks
    except Exception:
        return []


def _get_stock_list_akshare() -> list[dict]:
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        result = df[["代码", "名称"]].rename(
            columns={"代码": "code", "名称": "name"}
        ).to_dict("records")
        return [r for r in result if not r["code"].startswith("8")]
    except Exception:
        return []


# ---- 以下为兼容旧接口保留的函数 ----


def get_min_kline(symbol: str, period: str = "5", start_date: str | None = None,
                  end_date: str | None = None) -> pd.DataFrame:
    """获取分钟K线（仅 akshare）"""
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = start_date or (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol, period=period,
            start_date=start, end_date=end, adjust="qfq",
        )
        return df
    except Exception:
        return pd.DataFrame()


def get_realtime_quotes() -> pd.DataFrame:
    """获取全市场实时行情"""
    try:
        import akshare as ak
        return ak.stock_zh_a_spot_em()
    except Exception:
        return pd.DataFrame()


def get_bulk_a_stock_day(date: str | None = None) -> pd.DataFrame:
    """批量获取全部A股某日K线（baostock bulk API，保留兼容）"""
    _import_error_hint()
    return pd.DataFrame()


def get_bulk_etf_day(date: str | None = None) -> pd.DataFrame:
    return pd.DataFrame()


def get_bulk_day(date: str | None = None, include_etf: bool = True) -> pd.DataFrame:
    return pd.DataFrame()


def _import_error_hint():
    """baostock bulk API 已废弃，提示用户使用 pytdx"""
