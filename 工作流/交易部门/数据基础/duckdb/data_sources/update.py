"""公告/预约披露/快讯 增量更新入口（T-017 P4）

用法（在交易部门根目录执行）：
    python -m 数据基础.duckdb.data_sources.update --prbook --announcements --news
        [--db 路径] [--symbols 000651,600519] [--limit N] [--section 2026-06-30]

模式：
  --prbook        预约披露：默认全市场刷新当前报告期（分页翻完 ≈5540 只）
  --announcements 公告：逐只拉最近公告（默认 20 只蓝筹样本，--symbols 覆盖）
  --news          快讯：拉最新一页（50 条）
  不带任何模式 = --prbook（财报日避让数据是 P4 核心交付）

落库：
  - 默认主库 t017_p2.duckdb（P3 库）；验证/试跑用 --db 指向临时库，不污染主库
  - 三表 PK upsert 幂等：重复执行不产生重复行（announcements/prbook/news_flash）

P4 依据：老板 2026-08-05 确认执行 T-017 P4（估工报告 ② a-stock-data 接入）。
"""
import argparse
import sys
import time
from pathlib import Path

from 数据基础.duckdb.data_sources import cls as CLS
from 数据基础.duckdb.data_sources import cninfo as CI
from 数据基础.duckdb.data_sources import store as DS
from 数据基础.duckdb.data_sources.config import SAMPLE_SYMBOLS

sys.stdout.reconfigure(encoding="utf-8")


def run_prbook(con, section_time: str, symbols: list[str] | None) -> dict:
    """预约披露拉取落库；symbols 为空 → 全市场翻页"""
    st = section_time or CI.default_section_time()
    if symbols:
        rows = []
        for s in symbols:
            rows.extend(CI.fetch_prbook(symbol=s, section_time=st))
    else:
        rows = CI.fetch_prbook(section_time=st)
    n = DS.upsert_prbook(con, rows)
    return {"报告期": st, "拉取行数": len(rows), "落库行数": n}


def run_announcements(con, symbols: list[str], limit: int | None) -> dict:
    """公告逐只拉取落库（每只最近 CNINFO_ANN_PAGE 条）"""
    syms = symbols[:limit] if limit else symbols
    rows = []
    per = {}
    for s in syms:
        got = CI.fetch_announcements(s)
        rows.extend(got)
        per[s] = len(got)
        print(f"  [公告] {s}: {len(got)} 条")
    n = DS.upsert_announcements(con, rows)
    return {"股票数": len(syms), "拉取行数": len(rows), "落库行数": n, "每只分布": per}


def run_news(con) -> dict:
    """财联社快讯拉取落库（最新一页）"""
    rows = CLS.fetch_telegraph()
    n = DS.upsert_news(con, rows)
    return {"拉取行数": len(rows), "落库行数": n}


def main():
    ap = argparse.ArgumentParser(description="公告/预约披露/快讯增量更新（T-017 P4）")
    ap.add_argument("--db", default=None, help="duckdb 路径（默认 P3 主库）")
    ap.add_argument("--prbook", action="store_true", help="拉取预约披露（默认开启）")
    ap.add_argument("--announcements", action="store_true", help="拉取公告")
    ap.add_argument("--news", action="store_true", help="拉取财联社快讯")
    ap.add_argument("--symbols", default="", help="公告/预约披露的股票列表（逗号分隔，覆盖默认样本）")
    ap.add_argument("--limit", type=int, default=None, help="公告抽样只数上限（默认全部）")
    ap.add_argument("--section", default=None, help="报告期（财报截止日，默认按当前日期推算）")
    args = ap.parse_args()

    modes = [args.prbook, args.announcements, args.news]
    if not any(modes):
        args.prbook = True  # 默认模式：预约披露（财报日避让核心数据）

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or SAMPLE_SYMBOLS

    t0 = time.time()
    if args.db:
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)  # 临时库父目录兜底
    con = DS.open_db(args.db)
    try:
        if args.prbook:
            r = run_prbook(con, args.section, symbols if args.symbols else None)
            print(f"[预约披露] {r}")
        if args.announcements:
            r = run_announcements(con, symbols, args.limit)
            print(f"[公告] {r}")
        if args.news:
            r = run_news(con)
            print(f"[快讯] {r}")
        # 摘要
        for t in ("announcements", "prbook", "news_flash"):
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"[表 {t}] 累计 {n} 行")
    finally:
        con.close()
    print(f"完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
