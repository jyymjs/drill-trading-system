"""R-080 G1 扰动包络 ±10%（2026-08-13 · 验证段邻域检查）

V4 参数邻域 ±10% → 性能变化 → 悬崖检测（邻域 max-min 差 <20% = 参数非尖峰/稳健）：

  DL_RANGE_S/A/B ×0.9 / ×1.1   —— 引擎重评级（验证段 2023-2026 同 final 参数，直接
                                  引擎 API 绕信号缓存；dl_cands 不变只动 RANGE）
  T-020 量比阈值 1.1 / 1.3      —— 触发集过滤（r69d _sig_vol_ratio 同款，秒级）
  风险额 0.0225 / 0.0275        —— 资金层（r44 run_one，秒级）

判据：邻域内 avgR / 收益 max-min 差 ≤20% 视为稳健；>40% = 悬崖（参数敏感）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))       # 项目/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 交易部门根

import pandas as pd

from 回测系统.adapters.strategy_provider import ZuanQianProvider  # noqa: E402
from 回测系统.engine import BacktestEngine  # noqa: E402
from 回测系统.params import BacktestParams  # noqa: E402
from 回测系统.r44_position_grid import run_one  # noqa: E402
from 回测系统.report import signals_to_frame  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
VAL = os.path.join(ROOT, "产出", "输出", "数据", "backtest_final_20260806", "signals.csv")
OUTD = os.path.join(ROOT, "产出", "输出", "实验", "G1-扰动包络-20260813.md")


def _dl_params() -> BacktestParams:
    """验证段引擎参数（与 backtest_final 同款）"""
    return BacktestParams(
        start="20230701", end="20260731", strategy="zuanqian_strategy",
        mode="prebreak", interval=5, holds=[20], grades=["S"],
        max_workers=12, enable_cost=True, cost_multiplier=1.0,
        moving_stop=False, env_gate=True, env_drop_pct=-2.0, env_mode="veto",
        env_index="上证指数", volume_filter=True, min_amount=5000.0, vol_window=5,
        prbook_gate=True, sentiment_gate=True, sent_threshold=70.0,
        missing_sentiment="pass", dn_confirm=1.5, c23=True, phase_in=True,
        output_dir=os.path.join(ROOT, "产出", "输出", "数据", "backtest_valid_DLperturb"),
    )


def run_dl_variant(factor: float) -> dict:
    """DL_RANGE ×factor 引擎重评级（验证段）→ 信号层统计"""
    p = ZuanQianProvider()
    s = p._strategy
    s.DL_RANGE_S *= factor
    s.DL_RANGE_A *= factor
    s.DL_RANGE_B *= factor
    eng = BacktestEngine(_dl_params(), strategy=p)
    res = eng.run()
    df = signals_to_frame(res.records, [20])
    trig = df[(df["triggered_20d"] == 1)].copy()
    rs = trig["r_20d"].astype(float)
    return {"n_sig": len(df), "n_trig": len(trig),
            "trig_rate": len(trig) / len(df) if len(df) else 0,
            "win_rate": float((rs > 0).mean()) if len(rs) else 0,
            "avg_r": float(rs.mean()) if len(rs) else 0}


def t020_variants() -> dict:
    """T-020 量比阈值邻域（触发集过滤）→ 信号层统计"""
    from 回测系统.r69d_combo import _sig_vol_ratio, KlineCache  # noqa: E402
    sig = pd.read_csv(VAL, encoding="utf-8-sig", dtype={"code": str})
    trig = sig[sig["triggered_20d"] == 1]
    kc = KlineCache()
    out = {}
    for th in (1.1, 1.3):
        keep = [r for _, r in trig.iterrows()
                if (_sig_vol_ratio(str(r["code"]), str(r["date"])[:10], kc) or 0) > th]
        k = pd.DataFrame(keep)
        rs = k["r_20d"].astype(float)
        out[f"T020_{th}"] = {"n_trig": len(k),
                             "win_rate": float((rs > 0).mean()) if len(rs) else 0,
                             "avg_r": float(rs.mean()) if len(rs) else 0}
    return out


def risk_variants() -> dict:
    """风险额 ±10% 资金层（验证段）"""
    out = {}
    for rr in (0.0225, 0.0275):
        m, _ = run_one(VAL, 8401.0, rr, 999, min_date="2023-01-01", return_raw=True)
        out[f"risk_{rr}"] = {"total_ret_pct": m.get("total_ret_pct", 0),
                             "n_exec": m.get("n_exec", 0),
                             "avg_r": m.get("avg_r", 0)}
    return out


def main() -> int:
    lines = ["# G1 扰动包络 ±10%（2026-08-13 · 验证段邻域检查）", ""]
    print("== DL_RANGE 变体（引擎重评级，~12 分钟）==", flush=True)
    dl = {}
    for f in (0.9, 1.1):
        print(f"  DL×{f} …", flush=True)
        dl[f"DLx{f}"] = run_dl_variant(f)
        print(f"    {dl[f'DLx{f}']}", flush=True)
    base = {"n_sig": 2010, "n_trig": 514, "trig_rate": 514 / 2010,
            "win_rate": 0.551, "avg_r": 0.903}
    rows = [["参数", "信号数", "触发数", "触发率", "胜率", "avgR"]]
    for k in ("DLx0.9", "DLx1.1"):
        d = dl[k]
        rows.append([k, d["n_sig"], d["n_trig"], f"{d['trig_rate']:.1%}",
                     f"{d['win_rate']:.1%}", f"{d['avg_r']:+.3f}"])
    rows.append(["基线(1.0)", base["n_sig"], base["n_trig"], f"{base['trig_rate']:.1%}",
                 f"{base['win_rate']:.1%}", f"{base['avg_r']:+.3f}"])
    avgs = [d["avg_r"] for d in dl.values()] + [base["avg_r"]]
    lines += ["## DL_RANGE 邻域（信号层 avgR）", "",
              "| 参数 | 信号数 | 触发数 | 触发率 | 胜率 | avgR |",
              "|---|---|---|---|---|---|"]
    lines += ["|" + "|".join(str(c) for c in r) + "|" for r in rows]
    spread = (max(avgs) - min(avgs)) / abs(base["avg_r"]) if base["avg_r"] else 0
    lines += ["", f"邻域极差 = max−min = {max(avgs):.3f} − {min(avgs):.3f} "
                  f"（相对基线 {spread:.1%}）｜{'稳健' if spread <= 0.2 else '⚠️ 悬崖'}"]

    print("== T-020 变体（触发集过滤）==", flush=True)
    t = t020_variants()
    for k, v in t.items():
        print(f"  {k}: {v}", flush=True)
    lines += ["", "## T-020 阈值邻域（触发集 avgR）", "",
              "| 阈值 | 触发数 | 胜率 | avgR |", "|---|---|---|---|",
              f"| 1.1 | {t['T020_1.1']['n_trig']} | {t['T020_1.1']['win_rate']:.1%} "
              f"| {t['T020_1.1']['avg_r']:+.3f} |",
              f"| 基线 1.2 | {base['n_trig']} | {base['win_rate']:.1%} | {base['avg_r']:+.3f} |",
              f"| 1.3 | {t['T020_1.3']['n_trig']} | {t['T020_1.3']['win_rate']:.1%} "
              f"| {t['T020_1.3']['avg_r']:+.3f} |"]

    print("== 风险额变体（资金层）==", flush=True)
    rv = risk_variants()
    for k, v in rv.items():
        print(f"  {k}: {v}", flush=True)
    lines += ["", "## 风险额邻域（资金层 8401×999）", "",
              "| 风险额 | 成交 | 总收益 | avgR |", "|---|---|---|---|",
              f"| 0.0225 | {rv['risk_0.0225']['n_exec']} "
              f"| {rv['risk_0.0225']['total_ret_pct']:+.1f}% "
              f"| {rv['risk_0.0225']['avg_r']:+.3f} |",
              f"| 基线 0.025 | 199 | +112.4% | +0.380 |",
              f"| 0.0275 | {rv['risk_0.0275']['n_exec']} "
              f"| {rv['risk_0.0275']['total_ret_pct']:+.1f}% "
              f"| {rv['risk_0.0275']['avg_r']:+.3f} |"]

    os.makedirs(os.path.dirname(OUTD), exist_ok=True)
    with open(OUTD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告: {OUTD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
