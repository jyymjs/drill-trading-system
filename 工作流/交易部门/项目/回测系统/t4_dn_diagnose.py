#!/usr/bin/env python3
"""T-4.2：DN 动能诊断（只读分析，不改代码）

统计全市场 VOL_RATIO/实体比真实分布，定位 DN 96% B/C 的卡死原因：
  1. 最后一根 K 线量比分布（P50/75/90/95/99）——对照 DN 阈值 S=2.5 A=1.5 B=1.1
  2. 最近 5 根最大量比分布（判断是否窗口问题——启动 K 可能不是最后一根）
  3. 实体比分布——对照阈值 S/A/B ≥5%/4%/3%
  4. 判定失败原因分布（量比不过 / 实体不过 / 两者都过但降级）
"""
import argparse
import glob
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd
from 分析决策.分析.indicators import all_indicators
from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "数据基础", "数据", "cache")


def load(path: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
        df.rename(columns={"日期": "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CACHE, "*.csv")))
    random.seed(args.seed)
    chosen = random.sample(files, min(args.sample, len(files)))

    strategy = ZuanQianStrategy()
    last_vol = []      # 最后一根 K 量比
    max5_vol = []      # 最近 5 根最大量比
    last_body = []     # 最后一根实体比
    max5_body = []     # 最近 5 根最大实体比
    fail_reason = {"量比不过": 0, "实体不过": 0, "都过但降级": 0, "通过": 0, "异常": 0}
    dn_grades = {}
    n_ok = 0

    for f in chosen:
        df = load(f)
        if df is None or len(df) < 60:
            continue
        try:
            df = all_indicators(df, needed_cols=strategy.required_indicators)
            len(df)
            if "VOL_RATIO" not in df.columns:
                continue
            n_ok += 1
            tail = df.tail(5)
            last_vol.append(float(df["VOL_RATIO"].iloc[-1]))
            max5_vol.append(float(tail["VOL_RATIO"].max()))
            body = abs(df["收盘"].iloc[-1] - df["开盘"].iloc[-1]) / df["收盘"].iloc[-1]
            last_body.append(float(body))
            b5 = abs(tail["收盘"] - tail["开盘"]) / tail["收盘"]
            max5_body.append(float(b5.max()))

            # 判定链模拟（按 _grade_dn 规则：1根S/2根A/3根B）
            v = float(df["VOL_RATIO"].iloc[-1])
            b = body
            if v < 1.1 or b < 0.03:
                fail_reason["量比不过" if v < 1.1 else "实体不过"] += 1
            else:
                if v >= 2.5 and b >= 0.05 or v >= 1.5 and b >= 0.04:
                    fail_reason["通过"] += 1
                else:
                    fail_reason["通过"] += 1  # B 档也通过（但可能降级）
            res = strategy.grade(df)
            g = res.get("scores", {}).get("DN动能", ("?", ""))[0]
            dn_grades[g] = dn_grades.get(g, 0) + 1
        except Exception:
            fail_reason["异常"] += 1

    def pct(a, p):
        return float(np.percentile(a, p))

    print("=" * 55)
    print(f"DN 动能诊断（{n_ok} 只有效）")
    print("=" * 55)
    print("\n1. 最后一根 K 线量比分布（对照阈值 S=2.5 A=1.5 B=1.1）")
    print(f"   P50={pct(last_vol,50):.2f}  P75={pct(last_vol,75):.2f}  P90={pct(last_vol,90):.2f}  "
          f"P95={pct(last_vol,95):.2f}  P99={pct(last_vol,99):.2f}  max={max(last_vol):.2f}")
    print(f"   ≥2.5 比例: {np.mean(np.array(last_vol) >= 2.5):.1%}   ≥1.5 比例: {np.mean(np.array(last_vol) >= 1.5):.1%}   ≥1.1 比例: {np.mean(np.array(last_vol) >= 1.1):.1%}")

    print("\n2. 最近 5 根最大量比分布（启动 K 可能是前几根）")
    print(f"   P50={pct(max5_vol,50):.2f}  P75={pct(max5_vol,75):.2f}  P90={pct(max5_vol,90):.2f}  "
          f"P95={pct(max5_vol,95):.2f}  P99={pct(max5_vol,99):.2f}")
    print(f"   ≥2.5 比例: {np.mean(np.array(max5_vol) >= 2.5):.1%}   ≥1.5 比例: {np.mean(np.array(max5_vol) >= 1.5):.1%}")

    print("\n3. 最后一根实体比分布（对照阈值 S≥5% A≥4% B≥3%）")
    print(f"   P50={pct(last_body,50):.2%}  P75={pct(last_body,75):.2%}  P90={pct(last_body,90):.2%}  "
          f"P95={pct(last_body,95):.2%}")
    print(f"   ≥5% 比例: {np.mean(np.array(last_body) >= 0.05):.1%}   ≥3% 比例: {np.mean(np.array(last_body) >= 0.03):.1%}")

    print("\n4. 最近 5 根最大实体比")
    print(f"   P50={pct(max5_body,50):.2%}  P75={pct(max5_body,75):.2%}  P90={pct(max5_body,90):.2%}")
    print(f"   ≥5% 比例: {np.mean(np.array(max5_body) >= 0.05):.1%}")

    print("\n5. grade() 实际 DN 评级分布: ", dn_grades)
    print("   判定链模拟失败原因: ", fail_reason)

    # 教学对照
    print("\n" + "=" * 55)
    print("教学对照（钻潜内训第五节 + 2024 周会）")
    print("=" * 55)
    print("老师动能定义 = 相对比较（与之前调整的强弱对比）+ 视觉形态（收完/连续/影线少）")
    print("代码实现 = 绝对量比阈值（VOL_RATIO≥2.5/1.5/1.1）+ 实体比——方向可能偏离")
    print("建议下一步：若分布证实阈值偏高，测『相对量比』（当前量 vs 调整段均量）")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "产出", "输出", "t4")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "t4_dn_diagnose.txt"), "w", encoding="utf-8") as fp:
        fp.write(f"DN 诊断（{n_ok} 只）：\n")
        fp.write(f"量比 P50/75/90/95/99: {pct(last_vol,50):.2f}/{pct(last_vol,75):.2f}/{pct(last_vol,90):.2f}/{pct(last_vol,95):.2f}/{pct(last_vol,99):.2f}\n")
        fp.write(f"5根最大量比 P50/75/90/95: {pct(max5_vol,50):.2f}/{pct(max5_vol,75):.2f}/{pct(max5_vol,90):.2f}/{pct(max5_vol,95):.2f}\n")
        fp.write(f"实体 P50/75/90/95: {pct(last_body,50):.2%}/{pct(last_body,75):.2%}/{pct(last_body,90):.2%}/{pct(last_body,95):.2%}\n")
        fp.write(f"DN 评级分布: {dn_grades}\n")
        fp.write(f"失败原因: {fail_reason}\n")
    print("\n报告已写入 产出/输出/数据/t4/t4_dn_diagnose.txt")


if __name__ == "__main__":
    main()
