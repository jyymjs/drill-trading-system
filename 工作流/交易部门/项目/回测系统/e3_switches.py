"""E3 差距代码化·开关对照（2026-08-13 · 验证段 2023-2026）

对"已实现规则"做开关对照（验证边际贡献，T-024 教训）：
  A 关通道感（quick_prefilter 通道检查）
  B 关像素感硬降级（_grade_lk px 阈值放宽）
引擎重评级（运行时子类覆盖，不碰策略文件——g1_perturb 同款合规路径），
与基线（backtest_final）信号层对照 avgR/胜率/信号数。

新增项（②回踩硬规则 ④PT过高点排除）留待本轮结论后单独评估（涉及评级规则
设计，先出开关结论）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))       # 项目/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 交易部门根

import pandas as pd

from 回测系统.adapters.strategy_provider import ZuanQianProvider  # noqa: E402
from 回测系统.engine import BacktestEngine  # noqa: E402
from 回测系统.params import BacktestParams  # noqa: E402
from 回测系统.report import signals_to_frame  # noqa: E402
from 策略.核心策略.samples.zuanqian_strategy import ZuanQianStrategy  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BASE = os.path.join(ROOT, "产出", "输出", "数据", "backtest_final_20260806", "signals.csv")
OUTD = os.path.join(ROOT, "产出", "输出", "实验", "E3-开关对照-20260813.md")


class NoChannelStrategy(ZuanQianStrategy):
    """关通道感：quick_prefilter 跳过通道检查（原 :793-800）——策略层子类
    （provider 委托给内部 _strategy，策略层重写才生效且可 pickle）"""
    def quick_prefilter(self, df):
        n = len(df)
        if n < 60:
            return False
        close = df["收盘"].values
        high = df["最高"].values
        low = df["最低"].values
        rh, rl = high[-60:].max(), low[-60:].min()
        if (rh - rl) / close[-1] > 0.50:
            return False
        l60 = low[-60:].min()
        if l60 > 0 and (close[-1] - l60) / l60 > 0.40:
            return False
        return True  # 跳过通道检查


class NoPixelStrategy(ZuanQianStrategy):
    """关像素感硬降级：_grade_lk 的 px 阈值放宽（不因像素感降级）"""
    def _grade_lk(self, df):
        return "S", "px 降级关闭（E3 对照）"


class NoChannelProvider(ZuanQianProvider):
    def __init__(self) -> None:
        self._strategy = NoChannelStrategy()


class NoPixelProvider(ZuanQianProvider):
    def __init__(self) -> None:
        self._strategy = NoPixelStrategy()


def _params(tag: str) -> BacktestParams:
    return BacktestParams(
        start="20230701", end="20260731", strategy="zuanqian_strategy",
        mode="prebreak", interval=5, holds=[20], grades=["S"],
        max_workers=12, enable_cost=True, moving_stop=False,
        env_gate=True, env_drop_pct=-2.0, env_mode="veto", env_index="上证指数",
        volume_filter=True, min_amount=5000.0, vol_window=5,
        prbook_gate=True, sentiment_gate=True, sent_threshold=70.0,
        missing_sentiment="pass", dn_confirm=1.5, c23=True, phase_in=True,
        output_dir=os.path.join(ROOT, "产出", "输出", "数据", f"E3_{tag}"),
    )


def run_variant(tag: str, provider) -> dict:
    eng = BacktestEngine(_params(tag), strategy=provider)
    res = eng.run()
    df = signals_to_frame(res.records, [20])
    trig = df[(df["triggered_20d"] == 1)].copy()
    rs = trig["r_20d"].astype(float)
    return {"n_sig": len(df), "n_trig": len(trig),
            "trig_rate": len(trig) / len(df) if len(df) else 0,
            "win_rate": float((rs > 0).mean()) if len(rs) else 0,
            "avg_r": float(rs.mean()) if len(rs) else 0}


def main() -> int:
    # 基线 = 今日数据同引擎重跑（原始 provider）——与变体唯一变量 = 规则开关
    print("== 基线重跑（今日数据，原始 provider）…", flush=True)
    baseline = run_variant("E3_base", ZuanQianProvider())
    print(f"基线: {baseline}", flush=True)

    results = {"基线(现行·今日数据)": baseline}
    for tag, prov in [("E3_A_noChannel", NoChannelProvider()),
                      ("E3_B_noPixel", NoPixelProvider())]:
        print(f"== {tag} 引擎重评级…", flush=True)
        results[tag] = run_variant(tag, prov)
        print(f"  {results[tag]}", flush=True)

    lines = ["# E3 开关对照（2026-08-13 · 验证段 2023-2026）", "",
             "| 变体 | 信号数 | 触发 | 触发率 | 胜率 | avgR |", "|---|---|---|---|---|---|"]
    for k, v in results.items():
        lines.append(f"| {k} | {v['n_sig']} | {v['n_trig']} | {v['trig_rate']:.1%} "
                     f"| {v['win_rate']:.1%} | {v['avg_r']:+.3f} |")
    lines += ["", "## 判定（边际贡献）",
              "- avgR 显著提升（≥+0.05R）= 规则负贡献（考虑移除）",
              "- avgR 下降/持平 = 规则正贡献/中性（维持）",
              "- 信号数大增 = 规则拦截大量信号（检查拦截质量）"]
    base_ar = baseline["avg_r"]
    for k, v in results.items():
        if k == "基线(现行)":
            continue
        d = v["avg_r"] - base_ar
        lines.append(f"- {k}: avgR {d:+.3f}（相对基线 {base_ar:+.3f}）"
                     f"{'⚠️ 负贡献' if d >= 0.05 else '正贡献/中性'}")
    os.makedirs(os.path.dirname(OUTD), exist_ok=True)
    with open(OUTD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告: {OUTD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
