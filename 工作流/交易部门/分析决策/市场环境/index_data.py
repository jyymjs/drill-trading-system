#!/usr/bin/env python3
"""指数日线数据层 + 全市场涨跌家数（B1 环境闸门 + C4 情绪闸门 · 2026-08-05 老板拍板
执行优化方案第 3 波）

出处：《量化体系优化方案》（总理/工作区/待确认/2026-08-05）B1/C4 项——
  - B1 环境闸门需先解决指数日线数据来源：duckdb 主库（t017_p2.duckdb）仅含个股 daily 表，
    无指数数据（2026-08-05 实测确认），故用 pytdx 通达信协议直拉三大指数并本地缓存。
  - C4 情绪闸门需全市场涨跌家数：pytdx get_index_bars 返回的每根指数 K 线自带
    up_count/down_count（当日该市场涨跌家数，含历史——2026-08-05 实测确认，
    2024-12-27/2026-05-29 等历史 bar 均可取），本模块提取落缓存，供回测。

数据口径：
  - 指数清单（与 市场复盘方法/复盘三支柱 知识卡一致，三支柱第一支柱=指数状态）：
      上证指数（市场1/000001）、深证成指（市场0/399001）、创业板指（市场0/399006）
  - 涨跌家数：上证指数=沪市家数、深证成指=深市家数（2026-08-05 实测对账：
    2026-05-29 全市场下跌占比 71.6%，与 duckdb 全库口径 71.4% 一致）；
    全市场 = 沪 + 深 两指数家数相加
  - 日线复权：指数无除权，原始价即口径
  - 缓存：数据基础/data/index_cache/{market}_{code}.csv（中文列，升序），
    覆盖回测区间后直接复用，不重复联网

设计约束（无前视纪律）：指数日线仅向前看行情，不含任何未来信息；
闸门判定用"信号日当日指数涨跌幅/涨跌家数"（T 日收盘后可知，符合 T+1 决策时点）。
"""
from pathlib import Path

import pandas as pd

# ── 指数清单（pytdx 参数：market 0=深 1=沪；code 通达信代码） ──
INDEXES = [
    {"name": "上证指数", "market": 1, "code": "000001"},
    {"name": "深证成指", "market": 0, "code": "399001"},
    {"name": "创业板指", "market": 0, "code": "399006"},
]
# C4 涨跌家数口径：沪=上证指数、深=深证成指（市场覆盖面最大，见模块 docstring）
BREADTH_INDEXES = ["上证指数", "深证成指"]
# 默认缓存目录（交易部门根/数据基础/data/index_cache/）
_CACHE_DIR = Path(__file__).resolve().parents[2] / "数据基础" / "data" / "index_cache"

# 单次拉取上限（通达信协议上限 800 根/次）
_PULL_BATCH = 800


def _cache_path(market: int, code: str, cache_dir: Path | None = None) -> Path:
    """缓存文件名：{market}_{code}.csv"""
    base = Path(cache_dir) if cache_dir else _CACHE_DIR
    return base / f"{market}_{code}.csv"


def load_index_daily(name: str = "上证指数", cache_dir: Path | None = None,
                     min_date: str | None = None, force_refresh: bool = False,
                     require_breadth: bool = False) -> pd.DataFrame:
    """加载指数日线（本地缓存优先，缺失/覆盖不足 → pytdx 拉取后写缓存）

    Args:
        name: 指数名（INDEXES 中之一，默认上证指数）
        cache_dir: 缓存目录（默认 数据基础/data/index_cache/；测试可注入临时目录）
        min_date: 需要的起始日期 "YYYYMMDD"（None=全量）；缓存早于该日期 → 重新拉取
        force_refresh: 强制联网重拉（忽略缓存）
        require_breadth: 需要涨跌家数列（C4 情绪闸门）；旧缓存缺 上涨家数/下跌家数 列
            → 视为过期重新拉取（C4 上线前的老缓存无家数列）

    Returns:
        中文列 DataFrame：日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅
        （require_breadth=True 时另有 上涨家数/下跌家数；升序）
        无网/无缓存 → 空表（调用方自行降级）
    """
    idx = next((i for i in INDEXES if i["name"] == name), None)
    if idx is None:
        raise ValueError(f"未知指数: {name!r}（可选: {[i['name'] for i in INDEXES]}）")
    path = _cache_path(idx["market"], idx["code"], cache_dir)

    if not force_refresh and path.exists():
        df = pd.read_csv(path, parse_dates=["日期"])
        if not df.empty and _cache_ok(df, min_date) and _breadth_cols_ok(df, require_breadth):
            return df

    # 缓存缺失/过期 → pytdx 拉取全量（分段）
    raw = _pull_index_all(idx["market"], idx["code"], min_date=min_date)
    if raw is None or raw.empty:
        # 拉取失败但已有旧缓存 → 回退旧缓存（宁可旧数据不可无数据；
        # require_breadth 时旧缓存缺家数列也视为不可用，宁缺毋滥）
        if path.exists():
            df = pd.read_csv(path, parse_dates=["日期"])
            if not df.empty and _breadth_cols_ok(df, require_breadth):
                return df
        return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅"] +
                            (["上涨家数", "下跌家数"] if require_breadth else []))

    path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(path, index=False, encoding="utf-8-sig")
    return raw


def _cache_ok(df: pd.DataFrame, min_date: str | None) -> bool:
    """缓存是否覆盖所需区间：数据非空且起始不晚于 min_date"""
    if min_date is None:
        return True
    need = pd.Timestamp(min_date)
    return bool(df["日期"].min() <= need)


def _breadth_cols_ok(df: pd.DataFrame, require: bool) -> bool:
    """缓存是否含涨跌家数列（require=False 时不检查）"""
    if not require:
        return True
    return {"上涨家数", "下跌家数"}.issubset(df.columns)


def _pull_index_all(market: int, code: str, min_date: str | None = None) -> pd.DataFrame | None:
    """pytdx 分段拉取指数全量日线（start 为距最近 K 线的偏移，0=最近）

    返回中文列 DataFrame（升序）；联网失败 → None
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return None

    # 通达信服务器（fetcher 已验证可用列表，含备份）
    servers = [("180.153.18.170", 7709), ("60.191.117.167", 7709),
               ("119.147.212.81", 7709), ("112.74.214.43", 7709)]
    need = pd.Timestamp(min_date) if min_date else None
    for host, port in servers:
        api = TdxHq_API()
        try:
            if not api.connect(host, port, time_out=5):
                continue
            rows = []
            offset = 0
            while True:
                bars = api.get_index_bars(9, market, code, offset, _PULL_BATCH)
                if not bars:
                    break
                rows.extend(bars)
                # 已到最早（不足一批）或已覆盖 min_date
                if need is not None and len(bars) < _PULL_BATCH and pd.Timestamp(f"{bars[-1]['year']:04d}-{bars[-1]['month']:02d}-{bars[-1]['day']:02d}") <= need:
                    break
                offset += _PULL_BATCH
                if len(bars) < _PULL_BATCH:
                    break
            api.disconnect()
            if not rows:
                continue
            df = _bars_to_cn(rows)
            if need is not None:
                df = df[df["日期"] >= need]
            return df.reset_index(drop=True)
        except Exception:  # noqa: BLE001 - 单服务器失败换下一个
            try:
                api.disconnect()
            except Exception:  # noqa: BLE001
                pass
            continue
    return None


def _bars_to_cn(rows: list) -> pd.DataFrame:
    """pytdx 原始 bars → 中文列 DataFrame（升序、去重）

    提取 上涨家数/下跌家数（C4 情绪闸门）：get_index_bars 每根指数 K 线自带当日
    该市场涨跌家数（up_count/down_count），历史 bar 同样携带（2026-08-05 实测确认）。
    """
    df = pd.DataFrame(rows)
    df["日期"] = pd.to_datetime(df["datetime"].str[:10])
    out = pd.DataFrame({
        "日期": df["日期"],
        "开盘": df["open"].astype(float),
        "收盘": df["close"].astype(float),
        "最高": df["high"].astype(float),
        "最低": df["low"].astype(float),
        "成交量": df["vol"].astype(float),
        "成交额": df["amount"].astype(float),
    })
    if "up_count" in df.columns and "down_count" in df.columns:
        out["上涨家数"] = df["up_count"].astype(float)
        out["下跌家数"] = df["down_count"].astype(float)
    out = out.sort_values("日期").drop_duplicates(subset="日期")
    out["涨跌幅"] = out["收盘"].pct_change() * 100
    return out.reset_index(drop=True)


# ── C4 全市场涨跌家数（情绪闸门数据层） ──


def load_market_breadth(cache_dir: Path | None = None, min_date: str | None = None,
                        force_refresh: bool = False) -> pd.DataFrame:
    """加载全市场涨跌家数日线（沪+深，本地缓存优先，缺列/覆盖不足 → pytdx 拉取）

    口径：上涨家数 = 上证指数（沪）up + 深证成指（深）up；下跌家数同理。
    2026-08-05 实测对账：2026-05-29 全市场下跌占比 71.6%（n=5185），
    与 duckdb 全库 71.4%（n=5188）一致——家数与库内个股口径吻合。

    Args:
        cache_dir: 缓存目录（默认 数据基础/data/index_cache/；测试可注入临时目录）
        min_date: 需要的起始日期 "YYYYMMDD"（None=全量）
        force_refresh: 强制联网重拉（忽略缓存）

    Returns:
        DataFrame：日期/上涨家数/下跌家数/下跌占比(%)（升序）
        无网/无缓存 → 空表（调用方自行降级，闸门侧有 missing 策略）
    """
    parts = []
    for name in BREADTH_INDEXES:
        df = load_index_daily(name, cache_dir=cache_dir, min_date=min_date,
                              force_refresh=force_refresh, require_breadth=True)
        if df.empty:
            return pd.DataFrame(columns=["日期", "上涨家数", "下跌家数", "下跌占比"])
        parts.append(df[["日期", "上涨家数", "下跌家数"]])

    out = parts[0]
    for p in parts[1:]:
        out = out.merge(p, on="日期", how="inner", suffixes=("_沪", "_深"))
    out["上涨家数"] = out["上涨家数_沪"] + out["上涨家数_深"]
    out["下跌家数"] = out["下跌家数_沪"] + out["下跌家数_深"]
    out = out[["日期", "上涨家数", "下跌家数"]].sort_values("日期").reset_index(drop=True)
    total = out["上涨家数"] + out["下跌家数"]
    out["下跌占比"] = (out["下跌家数"] / total.replace(0, float("nan")) * 100).fillna(0.0)
    return out


if __name__ == "__main__":
    """CLI 自查：拉取/刷新指数缓存 + 全市场涨跌家数（C4）"""
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for idx in INDEXES:
        df = load_index_daily(idx["name"], min_date="20210101")
        print(f"{idx['name']}: {len(df)} 根, {df['日期'].min().date()} ~ {df['日期'].max().date()}"
              f", 缓存 → {_cache_path(idx['market'], idx['code'])}")
    bd = load_market_breadth(min_date="20210101")
    if not bd.empty:
        print(f"全市场涨跌家数(C4): {len(bd)} 根, "
              f"{bd['日期'].min().date()} ~ {bd['日期'].max().date()}")
        for d in ("2026-05-29", "2024-12-30"):
            hit = bd[bd["日期"] == d]
            if len(hit):
                r = hit.iloc[0]
                print(f"  {d}: 上涨 {r['上涨家数']:.0f} / 下跌 {r['下跌家数']:.0f}"
                      f" / 下跌占比 {r['下跌占比']:.1f}%")
