"""R-035 对照实验（临时脚本）：DN 门槛修复前后候选质量对比
对 7.5 年信号源触发行重算 DN 评级 → 分组统计 avgR/胜率，证明门槛剔除的是差票"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 交易部门
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))          # 项目
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np

from 回测系统.confirm_replay import load_kline_cache
from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy
from 分析决策.分析.indicators import all_indicators

def main():
    df = pd.read_csv('产出/输出/数据/backtest_fullcycle_20190101_20260807/signals.csv',
                     encoding='utf-8-sig', dtype=str)
    tr = df[df['triggered_20d'] == '1'].copy()
    tr['r'] = tr['r_20d'].astype(float)
    print(f"触发信号: {len(tr)}", flush=True)

    codes = sorted(tr['code'].unique())
    print(f"加载 K 线（{len(codes)} 只）…", flush=True)
    klines = load_kline_cache(codes)
    strat = ZuanQianStrategy()

    dn_grades = {}
    for code in codes:
        base = klines.get(code)
        if base is None or len(base) < 60:
            continue
        needed = strat.required_indicators
        dates = pd.to_datetime(base['日期']).astype(str).str[:10].values
        sub = tr[tr['code'] == code]
        for _, row in sub.iterrows():
            d = str(row['date'])[:10]
            idx = np.searchsorted(dates, d, side='right') - 1
            if idx < 250:
                continue
            window = base.iloc[:idx + 1]
            try:
                ind = all_indicators(window, needed_cols=needed)
                dn_g, _ = strat._grade_dn(ind)
            except Exception:
                dn_g = 'C'
            dn_grades[(code, d)] = dn_g

    tr['dn'] = tr.apply(lambda r: dn_grades.get((str(r['code']), str(r['date'])[:10]), '?'), axis=1)
    kept = tr[tr['dn'] != 'C']
    removed = tr[tr['dn'] == 'C']
    print(f"\nDN 评级分布: {tr['dn'].value_counts().to_dict()}")
    print(f"\n修复前候选: {len(tr)} | 门槛后保留: {len(kept)} | 剔除(DN=C): {len(removed)} ({len(removed)/len(tr)*100:.1f}%)")
    for name, g in (("保留集(DN≥B)", kept), ("剔除集(DN=C)", removed)):
        if len(g):
            print(f"  {name}: {len(g)}笔 avgR {g['r'].mean():+.3f} 胜率 {100*(g['r']>0).mean():.1f}%")
    print(f"\n结论: 剔除集 avgR {removed['r'].mean():+.3f} vs 保留集 {kept['r'].mean():+.3f}"
          f" —— 门槛{'有效剔除差票' if removed['r'].mean() < kept['r'].mean() else '需审视'}")

if __name__ == '__main__':
    main()
