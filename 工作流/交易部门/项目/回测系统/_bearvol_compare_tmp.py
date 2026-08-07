"""量比熊市上限对照实验（临时脚本）：熊市段量比>3.0 候选 vs 其他 质量对比
对 7.5 年信号源触发行：市场状态（regime）+ 触发日量比 → 分组 avgR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
from 回测系统.confirm_replay import load_kline_cache
from 回测系统.market_regime import load_index_df, regime_series

def main():
    df = pd.read_csv('产出/输出/数据/backtest_fullcycle_20190101_20260807/signals.csv',
                     encoding='utf-8-sig', dtype=str)
    tr = df[df['triggered_20d'] == '1'].copy()
    tr['r'] = tr['r_20d'].astype(float)
    # 市场状态映射（信号日 → 牛/熊/震荡）
    idx = load_index_df()
    series = regime_series(idx)
    regime_by_date = dict(zip(series.index.astype(str).str[:10], series.values))
    tr['regime'] = tr['date'].map(lambda d: regime_by_date.get(str(d)[:10], '?'))
    print(f"市场状态分布: {tr['regime'].value_counts().to_dict()}", flush=True)

    # 触发日量比 = 触发日成交量/前20日均量（从 K 线算）
    codes = sorted(tr['code'].unique())
    print(f"加载 K 线（{len(codes)} 只）…", flush=True)
    klines = load_kline_cache(codes)
    ratios = {}
    for code in codes:
        base = klines.get(code)
        if base is None or len(base) < 25:
            continue
        dates = pd.to_datetime(base['日期']).astype(str).str[:10].values
        vols = base['成交量'].astype(float).values
        sub = tr[tr['code'] == code]
        for _, row in sub.iterrows():
            d = str(row['date'])[:10]
            idx = np.searchsorted(dates, d, side='right') - 1
            if idx < 21:
                continue
            ref = vols[max(0, idx - 20):idx].mean()
            ratio = vols[idx] / ref if ref > 0 else 0
            ratios[(code, d)] = round(ratio, 2)
    tr['vol_ratio'] = tr.apply(lambda r: ratios.get((str(r['code']), str(r['date'])[:10]), 0), axis=1)

    bear = tr[tr['regime'] == '熊']
    bear_high = bear[bear['vol_ratio'] > 3.0]
    bear_rest = bear[bear['vol_ratio'] <= 3.0]
    non_bear = tr[tr['regime'] != '熊']
    print(f"\n熊市触发: {len(bear)} | 熊市量比>3.0: {len(bear_high)} | 熊市量比≤3.0: {len(bear_rest)} | 非熊市: {len(non_bear)}")
    for name, g in (("熊市 量比>3.0（应剔除）", bear_high), ("熊市 量比≤3.0", bear_rest), ("非熊市", non_bear)):
        if len(g):
            print(f"  {name}: {len(g)}笔 avgR {g['r'].mean():+.3f} 胜率 {100*(g['r']>0).mean():.1f}%")
    if len(bear_high):
        print(f"\n结论: 熊市量比>3.0 avgR {bear_high['r'].mean():+.3f} vs 熊市其他 {bear_rest['r'].mean():+.3f}"
              f" —— 上限{'有效' if bear_high['r'].mean() < bear_rest['r'].mean() else '需审视'}")

if __name__ == '__main__':
    main()
