#!/usr/bin/env python3
"""T-4 第一步：评级函数行为审计（只读不改）

对照老师 2024 教学规则，量化当前 grade() 的行为偏差：
  1. 评级分布（S/A/B/C 占比）——老师：标准模式突破 80-85%，均 A 级不做
  2. DL 非 S 仍出评级的频率（教学：DL 必须 S 级，"没有后面的选项"；代码：DL_A=90 DL_B=60 允许 B/C）
  3. 各条件函数评级分布（定位主要分歧源）

用法:
    python 项目/回测系统/t4_rating_audit.py --sample 300 [--full]
"""
import argparse
import glob
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
from 分析决策.分析.indicators import all_indicators
from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "数据基础", "数据", "cache")


def load_cache_csv(path: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
        for col in ("日期", "date"):
            if col in df.columns:
                df.rename(columns={col: "date"}, inplace=True)
                break
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300, help="抽样数量")
    ap.add_argument("--full", action="store_true", help="全市场跑（慢）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dl-cands", default="120,90,60", help="DL 候选根数 S,A,B（T-4.1 参数测试）")
    args = ap.parse_args()

    dl_cands = tuple(int(x) for x in args.dl_cands.split(","))
    if len(dl_cands) != 3:
        print("--dl-cands 需为 S,A,B 三个数")
        sys.exit(1)

    files = sorted(glob.glob(os.path.join(CACHE, "*.csv")))
    print(f"[T4] 缓存共 {len(files)} 只 A 股")

    if args.full:
        chosen = files
    else:
        random.seed(args.seed)
        chosen = random.sample(files, min(args.sample, len(files)))
    print(f"[T4] 审计 {len(chosen)} 只（seed={args.seed}）")

    strategy = ZuanQianStrategy()
    strategy.DL_CANDS = dl_cands
    print(f"[T4] DL 候选根数: S={dl_cands[0]} A={dl_cands[1]} B={dl_cands[2]}")
    stats = {"S": 0, "A": 0, "B": 0, "C": 0, "Tier0": 0}
    cond_dist = {}   # 条件名 -> {评级: 次数}
    dl_not_s_with_grade = []   # DL 非 S 但综合评级仍 S/A/B
    dl_dist = {}
    total_graded = 0
    t0 = time.time()

    for i, f in enumerate(chosen, 1):
        code = os.path.basename(f)[:-4]
        df = load_cache_csv(f)
        if df is None or len(df) < 60:
            continue
        try:
            # 模拟 scan 流程：先算指标列，再评级
            df = all_indicators(df, needed_cols=strategy.required_indicators)
            res = strategy.grade(df)
        except Exception:
            continue
        grade = res.get("grade", "C")
        scores = res.get("scores", {})
        total_graded += 1

        if grade == "C" and "Tier0" in scores:
            stats["Tier0"] += 1
        stats[grade] = stats.get(grade, 0) + 1

        for name, (g, _r) in scores.items():
            cond_dist.setdefault(name, {}).setdefault(g, 0)
            cond_dist[name][g] += 1

        dl_g = scores.get("DL独立结构", ("?", ""))[0]
        dl_dist[dl_g] = dl_dist.get(dl_g, 0) + 1
        if dl_g in ("B", "C") and grade in ("S", "A", "B"):
            dl_not_s_with_grade.append((code, dl_g, grade))

        if i % 100 == 0:
            print(f"  ...{i}/{len(chosen)} ({time.time()-t0:.0f}s)")

    print(f"\n[T4] 审计完成：{total_graded} 只有效，用时 {time.time()-t0:.0f}s\n")

    print("=" * 50)
    print("1. 综合评级分布")
    print("=" * 50)
    for g in ("S", "A", "B", "C", "Tier0"):
        n = stats.get(g, 0)
        pct = n / total_graded * 100 if total_graded else 0
        print(f"  {g:6s}: {n:6d}  ({pct:5.1f}%)")

    print("\n" + "=" * 50)
    print("2. DL 独立结构评级分布（教学：必须 S 级）")
    print("=" * 50)
    for g in ("S", "A", "B", "C", "?"):
        if g in dl_dist:
            print(f"  DL={g}: {dl_dist[g]}")

    print(f"\n  对照教学规则【DL 必须 S 级】：DL 非 S 仍给出 S/A/B 综合评级的共 {len(dl_not_s_with_grade)} 只")
    for code, dl_g, grade in dl_not_s_with_grade[:15]:
        print(f"    {code}: DL={dl_g} 综合={grade}")
    if len(dl_not_s_with_grade) > 15:
        print(f"    ... 共 {len(dl_not_s_with_grade)} 只")

    print("\n" + "=" * 50)
    print("3. 各条件函数评级分布（定位分歧源）")
    print("=" * 50)
    for name, dist in cond_dist.items():
        line = "  ".join(f"{g}:{n}" for g, n in sorted(dist.items()))
        print(f"  {name}: {line}")

    # 输出报告
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "产出", "输出", "t4")
    os.makedirs(out_dir, exist_ok=True)
    report = os.path.join(out_dir, "t4_audit_report.txt")
    with open(report, "w", encoding="utf-8") as fp:
        fp.write(f"T-4 评级函数行为审计（{total_graded} 只，seed={args.seed}）\n\n")
        fp.write(f"1. 综合评级分布: {stats}\n")
        fp.write(f"2. DL 分布: {dl_dist}；DL 非 S 仍出 S/A/B: {len(dl_not_s_with_grade)} 只\n")
        fp.write(f"3. 条件分布: {cond_dist}\n")
        for code, dl_g, grade in dl_not_s_with_grade:
            fp.write(f"   DL非S案例 {code}: DL={dl_g} 综合={grade}\n")
    print(f"\n[T4] 报告已写入 {report}")


if __name__ == "__main__":
    main()
