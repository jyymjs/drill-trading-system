"""模拟实盘回测·信号源生成（2026-08-06 老板拍板 R-010 模拟实盘）

口径 = 老板实盘约束画像（2026-08-05 定 + 08-06 调整）的信号层：
  - 全市场（duckdb 主库 5203 只）
  - 区间 2023-07-01 ~ 2026-07-31（3 年）
  - mode=prebreak（5 条件预突破）+ 评级 S
  - dn_confirm=1.5（突破日量比 >1.5 才计入，老板已确认）
  - 四道闸门全开（B1 环境/C3 量能/C4 情绪/C1 财报——现行系统默认）
  - hold 5/10/20（sim_capital 主口径 20d）
资金执行层（5600 元/1.5% 风险/2 持仓/整手）由 sim_capital.py 完成。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "C:/Users/32032/Desktop/deepseek/工作流/.claude/worktrees/task/sim-capital-20260806/工作流/交易部门/项目")
sys.path.insert(0, "C:/Users/32032/Desktop/deepseek/工作流/.claude/worktrees/task/sim-capital-20260806/工作流/交易部门")
os.chdir("C:/Users/32032/Desktop/deepseek/工作流/.claude/worktrees/task/sim-capital-20260806/工作流/交易部门")

from pathlib import Path

from 回测系统.engine import BacktestEngine
from 回测系统.params import BacktestParams
from 回测系统.report import write_report, write_signals_csv
from 回测系统.stats import group_stats

OUT = Path("产出/输出/sim_capital_20260806_full")
params = BacktestParams(
    start="20230701", end="20260731", strategy="zuanqian_strategy",
    mode="prebreak", interval=5, holds=[5, 10, 20], grades=["S"],
    codes=None, max_workers=6, output_dir=str(OUT),
    env_gate=True, volume_filter=True, prbook_gate=True, sentiment_gate=True,
    dn_confirm=1.5,   # 突破日量比 > 1.5 才计入（老板 2026-08-06 确认）
    verify_samples=20,
)


def main():
    params.validate()
    engine = BacktestEngine(params)
    result = engine.run()

    print("\n===== GATE COUNTS =====")
    for k, v in sorted(result.gate_counts.items()):
        print(f"  {k}: {v}")
    print(f"processed={result.processed} skipped={result.skipped}")
    print(f"records={len(result.records)}")
    if result.failed_codes:
        print("failed:", result.failed_codes[:5])

    OUT.mkdir(parents=True, exist_ok=True)
    write_signals_csv(OUT / "signals.csv", result.records, params.holds)
    buckets = group_stats(result.records, params.holds)
    write_report(OUT / "report.md", result.records, buckets, params,
                 meta={"processed": result.processed, "skipped": result.skipped,
                       "gate_counts": result.gate_counts})
    with open(OUT / "gate_counts.json", "w", encoding="utf-8") as f:
        json.dump(result.gate_counts, f, ensure_ascii=False, indent=2)
    print("DONE: report.md + signals.csv + gate_counts.json →", OUT)


if __name__ == "__main__":
    main()
