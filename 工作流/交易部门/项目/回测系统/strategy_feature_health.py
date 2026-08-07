#!/usr/bin/env python3
"""策略特征体检（R-036③ · 2026-08-09 老板拍板实施）

季度/定期重算策略关键特征分布，对照规格书设定值，漂移自动报警——
"市场风格变了 → 参数该重估了"的信号，不等问题暴露。

监测维度（对照规格书）：
  ① 触发延迟分布  → 有效期 3 日设定（突破 69% 在 3 日内；若 3 日覆盖率持续下降 → 提示重估）
  ② 触发日量比    → dn_confirm 1.5 下限（量比越高越好；熊市 >3.0 倒 U 监测——T-026 已证伪但监测保留）
  ③ 动量分布      → C23 动量 ≤10%（过滤后应为 0 超标）
  ④ 止损距离      → C23 止损 0.5~3 元（过滤后应为 0 超标）
  ⑤ DN 门槛      → prebreak 候选 DN=C 剔除（R-035：剔除占比监测）

报警规则（样本 ≥100 才判定，防小样本误报）：
  - 延迟 3 日覆盖率 < 60% → ⚠️ 有效期 3 日可能太短，建议重估
  - 量比 >3.0 占比 > 20% → 👀 巨量突破占比升高（熊市倒 U 监测）
  - 动量/止损超标 > 0 → ⚠️ 信号源可能未应用 C23（口径检查）
  - DN=C 占比回升 > 70% → 👀 候选质量回落

使用：python 项目/回测系统/strategy_feature_health.py [--signals 路径] [--json 输出路径]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from 回测系统.confirm_replay import load_kline_cache

DEFAULT_SIGNALS = Path("产出/输出/数据/backtest_fullcycle_20190101_20260807/signals.csv")
# 规格书设定（2026-08-08 定案，见 策略规格书.md）
PENDING_EXPIRE_DAYS = 3          # 模拟条件单有效期
DN_CONFIRM_MIN = 1.5             # 放量下限
C23_MOM_MAX = 0.10               # 动量上限
C23_RISK_MIN, C23_RISK_MAX = 0.5, 3.0  # 止损距离区间
MIN_SAMPLES = 100                # 判定最小样本
DELAY_COVER_3D_MIN = 0.60        # 3 日覆盖率下限（当前 7.5 年 ~69%）
VOL3_SHARE_MAX = 0.20            # 量比>3.0 占比上限


def feature_health(signals_path: str | Path = DEFAULT_SIGNALS) -> dict:
    """特征体检主函数：返回统计 + 报警清单（可测试注入）"""
    df = pd.read_csv(signals_path, encoding="utf-8-sig", dtype=str)
    tr = df[df["triggered_20d"] == "1"].copy()
    alerts = []
    stats = {"n_trigger": len(tr)}

    # ① 触发延迟分布（信号日 → 首个 high≥trigger 的交易日差）
    codes = sorted(tr["code"].unique())
    klines = load_kline_cache(codes)
    delays = []
    ratios = []
    moms = []
    for code in codes:
        base = klines.get(code)
        if base is None:
            continue
        dates = pd.to_datetime(base["日期"]).astype(str).str[:10].values
        highs = base["最高"].astype(float).values
        vols = base["成交量"].astype(float).values
        closes = base["收盘"].astype(float).values
        for _, row in tr[tr["code"] == code].iterrows():
            d = str(row["date"])[:10]
            idx = np.searchsorted(dates, d, side="right") - 1
            if idx < 0 or idx + 1 >= len(dates):
                continue
            trigger = float(row["trigger"])
            delay = None
            for j in range(idx, len(dates)):
                if highs[j] >= trigger:
                    delay = j - idx
                    break
            if delay is not None:
                delays.append(delay)
            if idx >= 21:
                ref = vols[max(0, idx - 20):idx].mean()
                if ref > 0:
                    ratios.append(vols[idx] / ref)
                if closes[idx - 20] > 0:
                    moms.append(trigger / closes[idx - 20] - 1.0)

    if delays:
        arr = np.array(delays)
        cover3 = float((arr <= PENDING_EXPIRE_DAYS).mean())
        stats["delay"] = {"n": len(arr), "median": float(np.median(arr)),
                          "cover_3d": cover3,
                          "pct_gt3": float((arr > PENDING_EXPIRE_DAYS).mean())}
        if len(arr) >= MIN_SAMPLES and cover3 < DELAY_COVER_3D_MIN:
            alerts.append(f"⚠️ 触发延迟 3 日覆盖率 {cover3:.0%} < {DELAY_COVER_3D_MIN:.0%}"
                          f"——有效期 {PENDING_EXPIRE_DAYS} 日可能太短，建议重估")
    if ratios:
        r = np.array(ratios)
        share3 = float((r > 3.0).mean())
        stats["vol_ratio"] = {"n": len(r), "median": float(np.median(r)),
                              "share_gt3": share3}
        if len(r) >= MIN_SAMPLES and share3 > VOL3_SHARE_MAX:
            alerts.append(f"👀 量比>3.0 占比 {share3:.0%} > {VOL3_SHARE_MAX:.0%}"
                          f"——巨量突破占比升高（熊市倒 U 监测）")
    if moms:
        m = np.array(moms)
        over = float((m > C23_MOM_MAX).mean())
        stats["mom"] = {"n": len(m), "pct_over_10pct": over}
        if over > 0.001:
            alerts.append(f"⚠️ 动量>10% 信号占 {over:.1%}——信号源疑似未应用 C23 过滤（口径检查）")
    risk_dist = tr["risk"].astype(float).values
    out_of_range = float(((risk_dist < C23_RISK_MIN) | (risk_dist > C23_RISK_MAX)).mean())
    stats["stop_dist"] = {"n": len(risk_dist), "pct_out": out_of_range}
    if out_of_range > 0.001:
        alerts.append(f"⚠️ 止损距离越界占 {out_of_range:.1%}——信号源疑似未应用 C23（口径检查）")

    if not alerts:
        alerts.append("✅ 全部特征在规格书设定带内（样本充足时判定）")
    stats["alerts"] = alerts
    return stats


def render(stats: dict) -> str:
    out = ["策略特征体检（对照规格书设定 · R-036③）", "-" * 60]
    out.append(f"触发信号: {stats.get('n_trigger', 0)} 笔")
    d = stats.get("delay")
    if d:
        out.append(f"  触发延迟: 中位 {d['median']:.0f} 日 | 3 日覆盖率 {d['cover_3d']:.0%}"
                   f"（设定有效期 {PENDING_EXPIRE_DAYS} 日）")
    v = stats.get("vol_ratio")
    if v:
        out.append(f"  触发日量比: 中位 {v['median']:.2f}x | >3.0 占 {v['share_gt3']:.1%}"
                   f"（下限 {DN_CONFIRM_MIN}）")
    m = stats.get("mom")
    if m:
        out.append(f"  动量: >10% 占 {m['pct_over_10pct']:.1%}（C23 上限 {C23_MOM_MAX:.0%}）")
    sd = stats.get("stop_dist")
    if sd:
        out.append(f"  止损距离: 越界占 {sd['pct_out']:.1%}（C23 区间 {C23_RISK_MIN}~{C23_RISK_MAX} 元）")
    out.append("报警：")
    out.extend(f"  {a}" for a in stats.get("alerts", []))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    ap.add_argument("--json", default=None, help="JSON 输出路径（供报告引用）")
    args = ap.parse_args()
    stats = feature_health(args.signals)
    print(render(stats))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(stats, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(f"JSON → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
