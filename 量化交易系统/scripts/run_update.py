#!/usr/bin/env python3
"""数据更新脚本 - 供 Windows 定时任务调用

用法:
    python scripts/run_update.py              # 增量更新（默认）
    python scripts/run_update.py --full       # 全量更新
    python scripts/run_update.py --overwrite  # 强制覆盖

此脚本可被 Windows 任务计划程序定时执行，实现每日自动更新。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from data.updater import incremental_update, update_all_stocks, get_cache_stats
from config.stock_pool import get_all_stocks


def main():
    args = sys.argv[1:]
    full_mode = "--full" in args
    overwrite = "--overwrite" in args

    mode = "over" if overwrite else "skip"

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始数据更新...")
    print(f"模式: {'全量' if full_mode else '增量'} | {'强制覆盖' if overwrite else '跳过已有'}")

    t0 = datetime.now()

    if full_mode:
        # 全量更新（按只下载）
        stocks = get_all_stocks()
        print(f"全量更新 {len(stocks)} 只股票...")

        def on_progress(current, total, code, name, status):
            if current % 100 == 0 or current == total:
                print(f"  [{current}/{total}] {status}: {code} {name}")

        result = update_all_stocks(stocks, mode=mode, progress_callback=on_progress)
    else:
        # 增量更新（批量API）
        def on_progress(current, total, code, name, status):
            bar = "#" * (current * 40 // total) if total else ""
            print(f"\r  [{current}/{total}] {'█'* (current * 30 // max(total,1))}{'.'* (30 - current * 30 // max(total,1))} {status}: {code}", end="", flush=True)

        result = incremental_update(mode=mode, include_etf=True, progress_callback=on_progress)
        print()

    elapsed = (datetime.now() - t0).total_seconds()

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 更新完成!")
    print(f"  ✅ 更新: {result.get('updated', 0)}")
    print(f"  ⏭️  跳过: {result.get('skipped', 0)}")
    print(f"  ❌ 失败: {result.get('failed', 0)}")
    print(f"  ⏱ 耗时: {elapsed:.1f}秒")

    stats = get_cache_stats()
    print(f"  📊 缓存统计: {stats['total']} 只 (股票 {stats['stock_cached']} + ETF {stats['etf_cached']})")
    if stats["latest_date"]:
        print(f"  📅 最新数据: {stats['latest_date']}")


if __name__ == "__main__":
    main()
