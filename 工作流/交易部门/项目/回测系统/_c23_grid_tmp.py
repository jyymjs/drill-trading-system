"""C23 参数再校准（P2-1）：9 组变体信号层对比 → 最优资金层验证
变体：动量阈值 [8%,10%,12%] × 止损距离 [0.3-3.0, 0.5-3.0, 0.5-2.5]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
from 回测系统.confirm_replay import load_kline_cache

def main():
    df = pd.read_csv('产出/输出/数据/backtest_fullcycle_20190101_20260807/signals.csv',
                     encoding='utf-8-sig', dtype=str)
    tr = df[df['triggered_20d'] == '1'].copy()
    tr['r'] = tr['r_20d'].astype(float)
    tr['risk_dist'] = tr['risk'].astype(float)
    # 动量重算（trigger vs 前20根收盘；需 K 线）
    codes = sorted(tr['code'].unique())
    print(f"加载 K 线（{len(codes)} 只）…", flush=True)
    klines = load_kline_cache(codes)
    mom = {}
    for code in codes:
        base = klines.get(code)
        if base is None or len(base) < 25:
            continue
        dates = pd.to_datetime(base['日期']).astype(str).str[:10].values
        closes = base['收盘'].astype(float).values
        for _, row in tr[tr['code'] == code].iterrows():
            d = str(row['date'])[:10]
            idx = np.searchsorted(dates, d, side='right') - 1
            if idx < 21 or closes[idx - 20] <= 0:
                continue
            mom[(code, d)] = float(row['trigger']) / closes[idx - 20] - 1.0
    tr['mom'] = tr.apply(lambda r: mom.get((str(r['code']), str(r['date'])[:10]), np.nan), axis=1)

    print(f"\n{'动量上限':>8} {'止损区间':>12} {'保留':>5} {'avgR':>8} {'胜率':>6} {'累计R':>8}")
    results = []
    for mom_max in (0.08, 0.10, 0.12):
        for rmin, rmax in ((0.3, 3.0), (0.5, 3.0), (0.5, 2.5)):
            m = (tr['mom'].notna()) & (tr['mom'] <= mom_max) \
                & (tr['risk_dist'] >= rmin) & (tr['risk_dist'] <= rmax)
            sub = tr[m]
            if len(sub) == 0:
                continue
            avg = sub['r'].mean(); wr = 100 * (sub['r'] > 0).mean()
            results.append((mom_max, rmin, rmax, len(sub), avg, wr))
            print(f"{mom_max*100:6.0f}% {f'{rmin}-{rmax}':>12} {len(sub):5d} {avg:+8.3f} {wr:5.1f}% {sub['r'].sum():+8.1f}")
    # 现行（0.10, 0.5-3.0）与最优对比
    best = max(results, key=lambda x: x[4])
    cur = next(r for r in results if abs(r[0]-0.10) < 1e-9 and abs(r[1]-0.5) < 1e-9 and abs(r[2]-3.0) < 1e-9)
    print(f"\n现行 (10%, 0.5-3.0): avgR {cur[4]:+.3f} 样本 {cur[3]}")
    print(f"最优 ({best[0]*100:.0f}%, {best[1]}-{best[2]}): avgR {best[4]:+.3f} 样本 {best[3]}")

if __name__ == '__main__':
    main()
