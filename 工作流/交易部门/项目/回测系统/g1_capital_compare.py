"""R-080 G1 资金层对照（2026-08-13）

定参段（2020-2022）vs 验证段（2023-2026）同配置资金层对照：
8401 × 0.025 风险比例 × 999 上限仓（V4 官方参数），min_date 分窗。
判据：验证段总收益/回撤相对定参段的衰减（悬崖阈值：邻域 max-min 报告）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))       # 项目/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 交易部门根

from 回测系统.r44_position_grid import run_one  # noqa: E402

CAL = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                   "数据", "backtest_calib_2020-2022", "signals.csv")
VAL = os.path.join(os.path.dirname(__file__), "..", "..", "产出", "输出",
                   "数据", "backtest_final_20260806", "signals.csv")


def main() -> int:
    out = {}
    for name, path, md in [("定参段(2020-2022)", CAL, "2020-01-01"),
                           ("验证段(2023-2026)", VAL, "2023-01-01")]:
        m, _ = run_one(path, 8401.0, 0.025, 999, min_date=md, return_raw=True)
        out[name] = m
        print(f"[{name}] 成交 {m.get('n_exec')} 笔 | 总收益 {m.get('total_ret_pct', 0):+.1f}% "
              f"| avgR {m.get('avg_r', 0):+.3f} | 峰值回撤 {m.get('dd_peak_pct', 0):.1f}% "
              f"| 平均持仓 {m.get('avg_positions', 0):.1f} 仓", flush=True)
    # 衰减
    c, v = out["定参段(2020-2022)"], out["验证段(2023-2026)"]
    decay_ret = 1 - v.get("total_ret_pct", 0) / c.get("total_ret_pct", 1) if c.get("total_ret_pct") else 0
    print(f"总收益衰减（验证段相对定参段）: {decay_ret:+.1%}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
