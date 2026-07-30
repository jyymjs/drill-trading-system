"""股票/ETF 池管理"""
from itertools import islice
from data.fetcher import get_stock_list


def get_all_stocks(use_cache: bool = True) -> list[dict]:
    """获取全部A股列表"""
    return get_stock_list(use_cache=use_cache)


def get_stock_codes(use_cache: bool = True) -> list[str]:
    return [s["code"] for s in get_all_stocks(use_cache)]


def get_stock_names(stocks: list[dict]) -> dict[str, str]:
    return {s["code"]: s["name"] for s in stocks}


# 常见ETF名称映射（补充bulk数据中缺失的名称）
_ETF_NAMES = {
    "510050": "上证50ETF", "510300": "沪深300ETF", "510500": "中证500ETF",
    "510880": "红利ETF", "510180": "上证180ETF", "510230": "金融ETF",
    "512100": "中证1000ETF", "512880": "证券ETF", "512660": "军工ETF",
    "512480": "半导体ETF", "512010": "医药ETF", "512170": "医疗ETF",
    "515050": "5GETF", "515880": "通信ETF", "515030": "新能源车ETF",
    "516160": "新能源ETF", "517050": "互联网ETF",
    "159915": "创业板ETF", "159949": "创业板50ETF",
    "159845": "中证1000ETF",
    "159928": "消费ETF", "159865": "养殖ETF",
    "159870": "化工ETF", "159766": "旅游ETF",
    "518880": "黄金ETF", "513100": "纳指ETF",
    "513050": "中概互联ETF", "513500": "标普500ETF",
}


def get_etf_list() -> list[dict]:
    """获取全部ETF列表

    优先从静态映射表返回常见ETF，确保不依赖 baostock bulk API。

    Returns:
        [{"code": "510050", "name": "上证50ETF"}, ...]
    """
    try:
        # 尝试从 pytdx 获取 ETF 列表
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        api.connect('180.153.18.170', 7709, time_out=3)
        count = api.get_security_count(1)  # 上海ETF
        if count and count > 0:
            etfs = api.get_security_list(1, 0, count)
            api.disconnect()
            if etfs:
                result = []
                for e in etfs:
                    code = str(e.get('code', ''))
                    if code.startswith('51') or code.startswith('15') or code.startswith('16'):
                        name = e.get('name', code)
                        result.append({"code": code, "name": name})
                if result:
                    return result
    except Exception:
        pass

    # 回退：返回静态ETF列表
    return [{"code": c, "name": n} for c, n in _ETF_NAMES.items()]


def get_all_securities(include_etf: bool = True) -> list[dict]:
    """获取全部可交易品种（股票+ETF）"""
    stocks = get_all_stocks()
    result = [{"code": s["code"], "name": s["name"], "type": "stock"} for s in stocks]
    if include_etf:
        for e in get_etf_list():
            result.append({"code": e["code"], "name": e["name"], "type": "etf"})
    return result


def batch_stocks(stocks: list, batch_size: int = 100):
    it = iter(stocks)
    while True:
        batch = list(islice(it, batch_size))
        if not batch:
            break
        yield batch
