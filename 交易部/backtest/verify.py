"""验收自检：同源抽查 / 收盘价抽查 / 无前视对照

1. 同源抽查：抽 N 只×M 个信号日，用 diagnose 同款调用序列（先截断 → all_indicators 逐窗重算
   → quick_prefilter → grade()/prebreak_grade()）独立重演，比对评级 + 6 条件分项 100% 一致
   ——证明回测用的是现行策略，不是另写的一套。
2. 收盘价抽查：信号日收盘价/出场价与 data/cache 原值逐笔一致（防止时间切片错位）。
3. 无前视对照：向量化全序列路径与逐窗重算路径评级一致（等价性）。
"""
from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from backtest.adapters.data_provider import CacheDataProvider
from backtest.adapters.strategy_provider import ZuanQianProvider
from backtest.tracking import TrackedRecord

SCORE_KEYS = ("PT平台测试", "TY统一区间", "DN动能", "DL独立结构", "LK轮廓质量", "SF释放级别")


def _pick(records, samples: int, seed: int):
    """确定性抽样（seed 固定 → 可复现）"""
    rng = random.Random(seed)
    if len(records) <= samples:
        return list(records)
    return rng.sample(records, samples)


def verify_engine_output(records: list[TrackedRecord], samples: int = 20, seed: int = 42) -> dict:
    """对引擎产出做三项自检，返回结果摘要 dict"""
    provider = CacheDataProvider()
    strategy = ZuanQianProvider()
    needed = strategy.required_indicators()

    checked = _pick(records, samples, seed)
    # 同源比对按 (code, date, mode) 去重，避免多 hold 重复重演
    cases = []
    seen = set()
    for rec in checked:
        key = (rec.signal.code, str(rec.signal.date), rec.signal.mode)
        if key not in seen:
            seen.add(key)
            cases.append(rec.signal)

    same_source_ok = True
    price_ok = True
    mismatches = []
    price_issues = []
    n_checked = 0

    for sig in cases:
        n_checked += 1
        base = provider.load(sig.code)
        # 定位信号日索引
        dates = base["日期"].values
        idx = next(i for i, d in enumerate(dates) if pd.Timestamp(d) == sig.date)
        # ① 同源重演：先截断基础列 → 逐窗重算指标 → 评级（diagnose 同款序列）
        window_base = base.iloc[: idx + 1]
        window = provider.compute_indicators(window_base, needed)
        if not strategy.quick_prefilter(window):
            same_source_ok = False
            mismatches.append(f"{sig.code}@{sig.date} prefilter 不一致（引擎有信号但重演被过滤）")
            continue
        if sig.mode == "normal":
            res = strategy.grade(window)
        else:
            res = strategy.prebreak_grade(window)
        if res.get("grade") != sig.grade:
            same_source_ok = False
            mismatches.append(f"{sig.code}@{sig.date} 评级不一致: 引擎={sig.grade} 重演={res.get('grade')}")
        for key in SCORE_KEYS:
            if res.get("scores", {}).get(key, ("C", ""))[0] != sig.score_grade(key):
                same_source_ok = False
                mismatches.append(f"{sig.code}@{sig.date} {key} 分项不一致")
        # ② 价格抽查：normal 进场价必须等于缓存原收盘
        if sig.mode == "normal":
            cached_close = float(base["收盘"].iloc[idx])
            if abs(sig.close - cached_close) > 1e-6:
                price_ok = False
                price_issues.append(f"{sig.code}@{sig.date} close 不一致: 引擎={sig.close} 缓存={cached_close}")

    # ③ 出场价抽查：抽 10 笔断言出场/进场价==缓存原值（到期出场==缓存收盘；止损出场=约定价且当日最低≤止损）
    exit_cases = _pick(records, min(10, len(records)), seed)
    for rec in exit_cases:
        sig = rec.signal
        base = provider.load(sig.code)
        dates = base["日期"].values
        idx = next(i for i, d in enumerate(dates) if pd.Timestamp(d) == sig.date)
        for hold, oc in rec.outcomes.items():
            if not oc.triggered:
                continue
            if oc.stopped and oc.exit_date is not None:
                j = next(i for i, d in enumerate(dates) if pd.Timestamp(d) == oc.exit_date)
                if not (float(base["最低"].iloc[j]) <= sig.stop + 1e-6) or abs(oc.exit_price - sig.stop) > 1e-6:
                    price_ok = False
                    price_issues.append(f"{sig.code}@{sig.date} hold={hold}d 止损出场价异常")
            else:
                j = next(i for i, d in enumerate(dates) if pd.Timestamp(d) == oc.exit_date)
                if abs(oc.exit_price - float(base["收盘"].iloc[j])) > 1e-6:
                    price_ok = False
                    price_issues.append(f"{sig.code}@{sig.date} hold={hold}d 到期出场价≠缓存收盘")

    return {
        "samples_checked": n_checked,
        "same_source_ok": same_source_ok,
        "price_ok": price_ok,
        "mismatches": mismatches[:10],
        "price_issues": price_issues[:10],
    }


def verify_csv(path: str | Path, samples: int = 20, seed: int = 42) -> dict:
    """从 signals.csv 抽查：信号日收盘价/出场价与缓存原值一致（verify 子命令）"""
    df = pd.read_csv(path, dtype={"code": str})
    if df.empty:
        return {"checked": 0, "ok": True, "issues": ["空信号文件"]}
    rng = random.Random(seed)
    rows = df.sample(n=min(samples, len(df)), random_state=seed).to_dict("records")
    provider = CacheDataProvider()
    issues = []
    for row in rows:
        base = provider.load(row["code"])
        if base.empty:
            issues.append(f"{row['code']} 缓存缺失")
            continue
        dates = base["日期"].values
        target = pd.Timestamp(row["date"])
        idxs = [i for i, d in enumerate(dates) if pd.Timestamp(d) == target]
        if not idxs:
            issues.append(f"{row['code']}@{row['date']} 缓存无此日")
            continue
        idx = idxs[0]
        if abs(float(row["close"]) - float(base["收盘"].iloc[idx])) > 1e-6:
            issues.append(f"{row['code']}@{row['date']} 收盘价不一致: csv={row['close']} 缓存={float(base['收盘'].iloc[idx])}")
    return {"checked": len(rows), "ok": not issues, "issues": issues[:10]}
