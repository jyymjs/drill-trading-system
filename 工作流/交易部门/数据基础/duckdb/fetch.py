"""mootdx 取数层（T-017 P3，复用 P2 已验证模式）

- 服务器：仅用 2 台可用服务器，必须传 (ip, port) 元组
- 验活：连接后 bars(offset=3) 非空才算活（P1 结论：列表接口能列出不代表行情可用）
- 增量窗口：bars 返回最新 N 条（降序），统一升序 + 去重
- 重试：单只最多 MAX_RETRY 次，尝试间切换服务器，退避 BACKOFF
"""
import time

import pandas as pd

from 数据基础.duckdb.config import BACKOFF, MAX_RETRY, PAGE_SLEEP

try:
    from mootdx.quotes import Quotes
    HAS_MOOTDX = True
except ImportError:
    HAS_MOOTDX = False
    Quotes = None


def connect(server):
    """连接 + 真实取数验活（拉 3 条日线非空才算活）

    Args:
        server: (ip, port) 元组

    Returns:
        已验活的 mootdx 客户端

    Raises:
        RuntimeError: 连接失败或空表（静默服务器）
    """
    if not HAS_MOOTDX:
        raise RuntimeError("mootdx 未安装")
    cli = Quotes.factory(market='std', server=server, timeout=15)
    df = cli.bars(symbol='000001', frequency=9, offset=3)
    if df is None or len(df) == 0:
        cli.close()
        raise RuntimeError(f"{server[0]} 空表(静默)验活失败")
    return cli


def _norm_bars(df: pd.DataFrame) -> pd.DataFrame:
    """统一日线格式：升序、去重、保留 daily 表所需列

    注意：mootdx bars 返回的 DataFrame 中 datetime 既是索引名又是列名
    （P2 全量脚本通过 concat(ignore_index=True) 规避）；这里必须先
    reset_index(drop=True) 丢弃索引，再按列操作，否则 pandas 报 ambiguous。
    """
    out = df.reset_index(drop=True)
    out = out.drop_duplicates(subset=['datetime']).sort_values('datetime').reset_index(drop=True)
    out['date'] = pd.to_datetime(out['datetime']).dt.date
    return out[['date', 'open', 'high', 'low', 'close', 'vol', 'amount']]


def fetch_recent_bars(cli, symbol: str, offset: int = 15) -> pd.DataFrame | None:
    """增量拉取：最新 offset 条日线（升序）。失败/空表返回 None"""
    df = cli.bars(symbol=symbol, frequency=9, offset=offset)
    if df is None or len(df) == 0:
        return None
    return _norm_bars(df)


def fetch_full_bars(cli, symbol: str) -> pd.DataFrame | None:
    """全量拉取（P2 翻页模式），用于新股/补齐。返回升序日线"""
    rows = []
    start = 0
    while True:
        df = cli.bars(symbol=symbol, frequency=9, start=start, offset=800)
        if df is None or len(df) == 0:
            break
        rows.append(df)
        got = len(df)
        if got < 800:
            break
        start += got
        time.sleep(PAGE_SLEEP)
    if not rows:
        return None
    return _norm_bars(pd.concat(rows, ignore_index=True))


def fetch_xdxr(cli, symbol: str) -> pd.DataFrame | None:
    """全量拉取除权因子（xdxr 一次返回全部，无翻页）。
    返回列：date, category, name, fenhong, peigujia, songzhuangu, peigu, suogu, fenshu, xingquanjia
    """
    x = cli.xdxr(symbol=symbol)
    if x is None or len(x) == 0:
        return None
    x = x.copy()
    x['date'] = pd.to_datetime(
        x['year'].astype(int).astype(str) + '-' +
        x['month'].astype(int).astype(str) + '-' +
        x['day'].astype(int).astype(str)).dt.date
    return x[['date', 'category', 'name', 'fenhong', 'peigujia', 'songzhuangu',
              'peigu', 'suogu', 'fenshu', 'xingquanjia']]


def fetch_one(symbol: str, server_a, server_b,
              recent: bool = True, offset: int = 15):
    """拉单只（日线 + xdxr），带重试与服务器切换（P2 模式）

    Args:
        symbol: 股票代码
        server_a / server_b: 轮转的两台服务器
        recent: True=增量窗口(offset 条)，False=全量翻页
        offset: 增量窗口条数

    Returns:
        (daily_df, xdxr_df, err) —— 失败时 daily=None，err 为异常信息
    """
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        server = server_a if attempt % 2 == 1 else server_b
        try:
            cli = connect(server)
            try:
                daily = fetch_recent_bars(cli, symbol, offset) if recent else fetch_full_bars(cli, symbol)
                if daily is None or len(daily) == 0:
                    raise RuntimeError("日线空表")
                xdxr = fetch_xdxr(cli, symbol)
            finally:
                cli.close()
            return daily, xdxr, None
        except Exception as e:  # noqa: BLE001 - mootdx 网络异常兜底，重试机制依赖
            last_err = e
            if attempt < MAX_RETRY:
                time.sleep(BACKOFF[attempt - 1])
    return None, None, last_err


def fetch_stock_list(cli) -> list[str]:
    """拉取全市场 A 股代码列表（mootdx std：深 000-301 + 沪 60/68/69 系）"""
    sz = cli.stocks(market=0)
    sz['code'] = sz['code'].astype(str).str.zfill(6)
    sz_a = sz[sz['code'].str.match(r'^(000|001|002|003|300|301)\d{3}$')]
    sh = cli.stocks(market=1)
    sh['code'] = sh['code'].astype(str).str.zfill(6)
    sh_a = sh[sh['code'].str.match(r'^(60[0135]|688|689)\d{3}$')]
    return sorted(set(sz_a['code'].tolist()) | set(sh_a['code'].tolist()))
