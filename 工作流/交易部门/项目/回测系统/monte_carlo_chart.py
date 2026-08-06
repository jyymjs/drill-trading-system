#!/usr/bin/env python3
"""蒙特卡洛输出标准②：净值曲线图版式（2026-08-06 老板拍板"精准复刻"）

黑底路径堆叠图（老板版式，记忆 monte-carlo-report-style.md）：
  - 画布：绘图区纯深黑 #121212，外围浅灰卡片边框
  - 路径层：海量模拟净值路径（每条 = 初始资金 + 逐笔 R×每笔风险额累计），
    淡雾霾蓝细实线 α≈0.1~0.15——密集区呈深蓝雾状云、底部聚拢右侧向上发散
  - 基准线：Ruin Line 正红细实线（< 初始×25%，图例 `Ruin Line (<金额)`）
    + Initial Capital 浅灰虚线
  - 极值轮廓线（顶层压全部路径）：Global Best 亮青蓝粗实线 / Global Worst
    暗红粗实线（贴破产线上方）
  - 左上角黑色半透明圆角图例框（白色 4 条）
  - 坐标：Y 轴 `Account Equity` 白色纵向文字 + 7 档刻度（按模拟规模自适应）；
    X 轴交易步数；淡灰极细虚线网格
  - 顶部居中大写标题 `MONTE CARLO: Middle 100.0% Range`

数据源：monte_carlo.simulate 输出（samples = 每场景逐笔 R 序列，直接重建路径）。
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 配色（老板版式）
BG = "#121212"              # 画布深黑
PATH_C = "#7fa8c9"          # 淡雾霾蓝
PATH_A = 0.12               # 路径透明度
RUIN_C = "#ff2d2d"          # 正红
INIT_C = "#b0b0b0"          # 浅灰
BEST_C = "#4de8ff"          # 亮青蓝/冰蓝
WORST_C = "#8b2f2f"         # 暗红
GRID_C = "#3a3a3a"          # 淡灰网格
TEXT_C = "#ffffff"          # 白


def plot_equity_paths(result: dict, initial_capital: float = 5600.0,
                      risk_amt: float = 112.0,
                      ruin_threshold_pct: float = 0.25,
                      title: str = "MONTE CARLO: Middle 100.0% Range",
                      save: bool = True,
                      out_path: str | Path | None = None) -> str:
    """净值曲线图（老板版式精准复刻）

    Args:
        result: monte_carlo.simulate 输出（需含 samples）
        initial_capital: 初始资金（元）
        risk_amt: 每笔风险金额（元，R × risk_amt → 权益金额）
        ruin_threshold_pct: 破产线 = 初始资金 × 比例（默认 25%）
        title: 顶部标题（默认老板版式）
        save: 是否保存 PNG
        out_path: 输出路径（默认 交易部门/产出/输出/蒙特卡洛-净值曲线.png）

    Returns:
        PNG 路径（save=False 返回空串）
    """
    if "error" in result or "samples" not in result:
        return ""
    samples = result["samples"]           # (n_sim, n_trades) R 序列（已含费用）
    n_sim, n_steps = samples.shape
    curves = np.cumsum(samples, axis=1) * risk_amt + initial_capital

    # 极值路径（按终值）
    finals = curves[:, -1]
    best_i = int(np.argmax(finals))
    worst_i = int(np.argmin(finals))

    fig, ax = plt.subplots(figsize=(16, 10), dpi=110)
    fig.patch.set_facecolor("#222222")          # 外围浅灰卡片边框（略深一档）
    ax.set_facecolor(BG)

    # 路径层（全部场景，低透明度）
    x = np.arange(n_steps)
    for i in range(n_sim):
        ax.plot(x, curves[i], color=PATH_C, alpha=PATH_A, linewidth=0.4)

    # 基准线
    ruin_val = initial_capital * ruin_threshold_pct
    ax.axhline(ruin_val, color=RUIN_C, linewidth=1.2, linestyle="-",
               label=f"Ruin Line (<{ruin_val:,.0f})")
    ax.axhline(initial_capital, color=INIT_C, linewidth=1.2, linestyle="--",
               label="Initial Capital")

    # 极值轮廓线（顶层）
    ax.plot(x, curves[best_i], color=BEST_C, linewidth=2.6,
            label="Global Best")
    ax.plot(x, curves[worst_i], color=WORST_C, linewidth=2.6,
            label="Global Worst")

    # 坐标轴
    ymax = float(curves.max())
    ax.set_ylim(0, ymax * 1.06)
    y_ticks = np.linspace(0, ymax, 7)           # 7 档刻度（结构对齐 0/2.5M/…/15M）
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([f"{v:,.0f}" for v in y_ticks], color=TEXT_C, fontsize=9)
    ax.set_ylabel("Account Equity", color=TEXT_C, fontsize=11,
                  rotation=90, labelpad=10)
    x_ticks = np.linspace(0, n_steps, 7, dtype=int)   # 结构对齐 0/25/…/150
    ax.set_xticks(x_ticks)
    ax.set_xticklabels([str(v) for v in x_ticks], color=TEXT_C, fontsize=9)
    ax.tick_params(axis="both", colors=TEXT_C)

    # 网格（淡灰极细虚线）
    ax.grid(color=GRID_C, linewidth=0.4, linestyle=":", alpha=0.6)

    # 图例框（左上角黑色半透明圆角）
    leg = ax.legend(loc="upper left", facecolor="#000000", edgecolor="#555555",
                    labelcolor=TEXT_C, fontsize=10, framealpha=0.6,
                    fancybox=True)
    leg.get_frame().set_alpha(0.6)

    # 顶部标题（大写无衬线居中）
    ax.set_title(title, color=TEXT_C, fontsize=18, pad=16, fontweight="bold")

    # 边框
    for spine in ax.spines.values():
        spine.set_color("#555555")

    fig.tight_layout()
    if save:
        out = Path(out_path) if out_path else (
            _ROOT / "产出" / "输出" / "蒙特卡洛-净值曲线.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=110, facecolor=fig.get_facecolor(),
                    bbox_inches="tight")
        plt.close(fig)
        return str(out)
    plt.close(fig)
    return ""


def main() -> int:
    """自检：小样本演示净值曲线图"""
    from 分析决策.跟踪.monte_carlo import simulate
    rng = np.random.default_rng(7)
    demo_rs = rng.normal(0.4, 1.2, 60).tolist()
    mc = simulate([{"r_multiple": r} for r in demo_rs], n_simulations=3000)
    p = plot_equity_paths(mc, save=True)
    print(f"净值曲线图 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
