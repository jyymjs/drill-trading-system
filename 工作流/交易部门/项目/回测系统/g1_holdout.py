"""R-080 G1 样本外验证框架（2026-08-13 · 骨架版，复用 r48_grid 窗口机制）

设计：
  定参段 = 2020-01-01 ~ 2022-12-31（V4 参数冻结）
  验证段 = 2023-01-01 ~ 2026-07-31（现有 514 笔信号即此段）
  对照   = 两段信号层（avgR/胜率/触发数）+ 资金层（收益/回撤）→ 相对指数超额衰减

依赖（TODO 下次会话完成）：
  ① 定参段信号生成：现有 signals.csv 是 2023+（3y 窗口）——需探索信号生成管线
     （r43_grid._build_enriched_cache / main.py scan 评级 → prebreak 信号），
     补 2020-2022 段信号
  ② 扰动包络：DL 阈值 ±10% / T-020 1.2±0.1 / 风险额 0.025±10% → 邻域收益
     （悬崖阈值：邻域 max-min 报告）
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SIGNALS = ROOT / "产出" / "输出" / "数据" / "backtest_final_20260806" / "signals.csv"

# 分窗（与 r48_grid STARTS 机制对齐）
WINDOWS = {
    "calib": ("2020-01-01", "2022-12-31"),   # 定参段
    "valid": ("2023-01-01", None),           # 验证段（None = 数据截止）
}


def main() -> int:
    from 项目.回测系统.r48_grid import prefilter_signals, read_signals

    print("G1 样本外验证骨架——信号源:", SIGNALS)
    sig = read_signals(str(SIGNALS))
    print(f"现有信号 {len(sig)} 笔，日期范围: "
          f"{sig['date'].min() if 'date' in sig.columns else '?'} ~ "
          f"{sig['date'].max() if 'date' in sig.columns else '?'}")
    print("\n⚠️ 定参段（2020-2022）信号缺失——需先探索信号生成管线补信号")
    print("验证段（2023+）信号 = 现有 514 笔（backtest_final_20260806）")
    print("\n后续步骤（下次会话）：")
    print("  1. 探索信号生成管线 → 生成 2020-2022 定参段信号")
    print("  2. 两段分别跑 资金升级回测_8401.py（V4 参数冻结）")
    print("  3. 对照：信号层（avgR/胜率/触发数）+ 资金层（收益/回撤）+ 相对指数超额")
    print("  4. 扰动包络：DL/T-020/风险额 ±10% 邻域 → 悬崖阈值报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
