"""数据获取 - akshare + baostock 双数据源"""
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak
from config.settings import KLINE_ADJUST, KLINE_CACHE_DAYS, KLINE_YEARS, STOCK_LIST_CACHE_DAYS
from data.cache import read_cache, write_cache

def _baostock_prefix(symbol: str) -> str:
    """获取 baostock 代码前缀

    Args:
        symbol: 6位代码 000001 / 600000 / 510050

    Returns:
        "sh." 或 "sz."
    """
    if symbol.startswith("6") or symbol.startswith("51"):
        return "sh."
    return "sz."


def get_stock_list(use_cache: bool = True) -> list[dict]:
    """获取全部A股列表（含退市风险警示股）

    Returns:
        [{"code": "000001", "name": "平安银行"}, ...]
    """
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
    """通过 baostock 获取股票列表"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            return []

        rs = bs.query_stock_basic()
        stocks = []
        while rs.next():
            s = rs.get_row_data()
            code = s[0]   # 如 "sh.600000"
            name = s[1]
            typ = s[4]    # 1=股票 2=指数
            status = s[5] # 1=上市 0=退市
            if typ == "1" and status == "1" and (code.startswith("sh.6") or code.startswith("sz.0") or code.startswith("sz.3")):
                clean_code = code.replace("sh.", "").replace("sz.", "")
                stocks.append({"code": clean_code, "name": name})

        bs.logout()
        return stocks
    except Exception as e:
        print(f"baostock 获取股票列表失败: {e}")
        return []


def _get_stock_list_akshare() -> list[dict]:
    """通过 akshare 获取股票列表"""
    try:
        df = ak.stock_zh_a_spot_em()
        result = df[["代码", "名称"]].rename(
            columns={"代码": "code", "名称": "name"}
        ).to_dict("records")
        result = [r for r in result if not r["code"].startswith("8")]
        return result
    except Exception as e:
        print(f"akshare 获取股票列表失败: {e}")
        return []


def get_daily_kline(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str = KLINE_ADJUST,
    use_cache: bool = True,
) -> pd.DataFrame:
    """获取日K线数据（baostock → akshare 双数据源）

    Args:
        symbol: 股票代码（如 "000001"）
        start_date: 开始日期 "YYYYMMDD"，默认3年前
        end_date: 结束日期 "YYYYMMDD"，默认今天
        adjust: 复权方式 ""/"qfq"/"hfq" (仅akshare支持)
        use_cache: 是否使用缓存

    Returns:
        DataFrame: 日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
    """
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = start_date or (datetime.now() - timedelta(days=365 * KLINE_YEARS)).strftime("%Y%m%d")

    # 尝试读取缓存
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

    # 尝试 baostock
    df = _kline_baostock(symbol, start, end)
    if df is not None and not df.empty:
        df = _standardize_columns(df)
        if use_cache:
            write_cache(symbol, df)
        return df

    # 尝试 akshare
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start, end_date=end, adjust=adjust,
        )
        if df is not None and not df.empty:
            df = _standardize_columns(df)
            if use_cache:
                write_cache(symbol, df)
            return df
    except Exception as e:
        print(f"akshare 获取 {symbol} K线失败: {e}")

    return pd.DataFrame()


def _kline_baostock(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """通过 baostock 获取日K线 (独立登录，避免会话冲突)"""
    def fmt(s): return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            return None
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

        if rows:
            df = pd.DataFrame(rows, columns=["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率"])
            for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期").reset_index(drop=True)
            df["涨跌幅"] = df["收盘"].pct_change() * 100
            df["振幅"] = (df["最高"] - df["最低"]) / df["最低"] * 100
            df["涨跌额"] = df["收盘"].diff()
            return df
    except Exception as e:
        print(f"baostock 获取 {symbol} K线失败: {e}")
    return None


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名为中文"""
    rename_map = {"股票代码": "代码"}
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    if "日期" in df.columns:
        df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    return df


def get_min_kline(
    symbol: str,
    period: str = "5",
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """获取分钟K线数据（仅 akshare）"""
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = start_date or (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol, period=period,
            start_date=start, end_date=end, adjust="qfq",
        )
        return df
    except Exception as e:
        print(f"获取 {symbol} 分钟K线失败: {e}")
        return pd.DataFrame()


def get_realtime_quotes() -> pd.DataFrame:
    """获取全市场实时行情（仅 akshare）"""
    try:
        df = ak.stock_zh_a_spot_em()
        return df
    except Exception as e:
        print(f"获取实时行情失败: {e}")
        return pd.DataFrame()


# ========== 批量 API（用于快速增量更新） ==========

def _parse_bulk_row(row, prefix_len: int = 0) -> dict:
    """解析 baostock bulk K线行数据

    Baostock bulk API 列顺序:
    [date, code, open, high, low, close, preClose, volume, amount, ...]
    """
    # row[0]=日期, row[1]=代码(带前缀)
    date_str = row[0]
    code = row[1][prefix_len:]  # 去掉 "sh."/"sz." 前缀
    return {
        "code": code,
        "日期": date_str,
        "开盘": float(row[2]) if row[2] else 0,
        "最高": float(row[3]) if row[3] else 0,
        "最低": float(row[4]) if row[4] else 0,
        "收盘": float(row[5]) if row[5] else 0,
        "成交量": float(row[7]) if len(row) > 7 and row[7] else 0,
        "成交额": float(row[8]) if len(row) > 8 and row[8] else 0,
        "换手率": float(row[12]) if len(row) > 12 and row[12] else 0,
    }


def _find_latest_trade_date() -> str:
    """查找最近的有数据交易日（统一登录/注销）"""
    import baostock as bs
    today = datetime.now()
    lg = bs.login()
    if lg.error_code != "0":
        return (today - timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        for days_back in range(1, 15):
            test_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            try:
                rs = bs.query_daily_history_k_AStock(test_date)
                if rs.next():
                    return test_date
            except Exception:
                continue
        return (today - timedelta(days=2)).strftime("%Y-%m-%d")
    finally:
        bs.logout()


def _bulk_query(date: str | None, query_func, date_param: bool = True) -> pd.DataFrame:
    """通用批量查询（独立登录/注销）"""
    import baostock as bs
    if date is None:
        date = _find_latest_trade_date()
    try:
        lg = bs.login()
        if lg.error_code != "0":
            return pd.DataFrame()
        if date_param:
            rs = query_func(date)
        else:
            rs = query_func()
        rows = []
        while rs.next():
            row = rs.get_row_data()
            rows.append(_parse_bulk_row(row, prefix_len=3))
        bs.logout()
        if rows:
            df = pd.DataFrame(rows)
            df["日期"] = pd.to_datetime(df["日期"])
            return df
    except Exception as e:
        print(f"批量查询失败: {e}")
        try:
            bs.logout()
        except Exception:
            pass
    return pd.DataFrame()


def get_bulk_a_stock_day(date: str | None = None) -> pd.DataFrame:
    """批量获取全部A股某日的K线数据"""
    import baostock as bs
    return _bulk_query(date, bs.query_daily_history_k_AStock)


def get_bulk_etf_day(date: str | None = None) -> pd.DataFrame:
    """批量获取全部ETF某日的K线数据"""
    import baostock as bs
    return _bulk_query(date, bs.query_daily_history_k_ETF)


def get_bulk_day(date: str | None = None, include_etf: bool = True) -> pd.DataFrame:
    """批量获取全部A股+ETF某日的K线数据"""
    stocks = get_bulk_a_stock_day(date)
    if include_etf:
        etfs = get_bulk_etf_day(date)
        if not etfs.empty:
            stocks = pd.concat([stocks, etfs], ignore_index=True)
    return stocks
