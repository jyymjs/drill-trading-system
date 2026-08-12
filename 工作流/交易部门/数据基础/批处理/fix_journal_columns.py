"""R-080 G3 前置修复：trade_journal/sim_journal 列序统一（2026-08-13）

背景：R-075 扩列时 trade_journal.py TRADE_COLUMNS 与 sim_trading.py SIM_COLUMNS
列序不同（volume/grade_at_entry 与 highest/lowest 顺序互换）——注释声称"同构"
但实际不一致；sim_journal.csv 历史行因此错位（status 值落进 highest 列）。

修复：① 统一列序 = SIM_COLUMNS（volume, grade_at_entry, ty_high, ty_low,
highest, lowest, status）② 重排两个 CSV 的表头与数据行 ③ 纠错 sim_journal
错位行（highest 列值 ∈ {open, closed} → 移到 status 列）。

用法：python -X utf8 数据基础/批处理/fix_journal_columns.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import csv
from pathlib import Path

JOURNAL_DIR = Path(__file__).resolve().parents[2] / "分析决策" / "交易日志"
SIM_COLUMNS = [
    "trade_id", "date", "symbol", "name", "direction", "market",
    "entry_price", "stop_loss", "trail_stop", "volume", "grade_at_entry",
    "ty_high", "ty_low", "highest", "lowest", "status",
    "exit_price", "exit_date", "exit_reason", "r_multiple", "pnl",
    "env_scale", "phase", "created_date",
]
# 旧 TRADE_COLUMNS 顺序（迁移源）
OLD_TRADE_COLUMNS = [
    "trade_id", "date", "symbol", "name", "direction", "market",
    "entry_price", "stop_loss", "trail_stop", "highest", "lowest",
    "volume", "grade_at_entry", "ty_high", "ty_low", "status",
    "exit_price", "exit_date", "exit_reason", "r_multiple", "pnl",
    "env_scale", "phase", "created_date",
]


def _reorder(path: Path, old_cols: list[str], new_cols: list[str],
             fix_status_shift: bool = False) -> tuple[int, int]:
    """按 old_cols→new_cols 重排文件；fix_status_shift：纠正 sim 错位行"""
    rows = list(csv.reader(open(path, encoding="utf-8-sig", newline="")))
    if not rows:
        return 0, 0
    header = rows[0]
    data = rows[1:]
    fixed = shifted = 0
    out = [new_cols]
    for r in data:
        r = (r + [""] * len(new_cols))[:len(new_cols)]   # 补齐缺列
        if fix_status_shift and r[old_cols.index("highest")] in ("open", "closed"):
            # 错位行：highest 位置的值 = 状态 → 重排
            shifted += 1
            r = r[:]
        row = [r[old_cols.index(c)] for c in new_cols]
        if fix_status_shift and row[new_cols.index("status")] not in ("open", "closed"):
            # 仍无状态 → 尝试从旧 highest 位找回
            if r[old_cols.index("highest")] in ("open", "closed"):
                row[new_cols.index("status")] = r[old_cols.index("highest")]
                row[new_cols.index("highest")] = ""
                fixed += 1
        out.append(row)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(out)
    return shifted, fixed


def main() -> int:
    live = JOURNAL_DIR / "trade_journal.csv"
    sim = JOURNAL_DIR / "sim_journal.csv"
    print("修复前 sim_journal 抽样：", flush=True)
    for line in open(sim, encoding="utf-8-sig").read().splitlines()[:2]:
        print("  ", line[:120], flush=True)

    for path, old, fix in ((live, OLD_TRADE_COLUMNS, False),
                           (sim, SIM_COLUMNS, True)):
        if not path.exists():
            print(f"跳过（不存在）: {path}", flush=True)
            continue
        shifted, fixed = _reorder(path, old, SIM_COLUMNS, fix)
        print(f"{path.name}: 重排完成（错位行 {shifted}，修复状态 {fixed}）", flush=True)

    print("\n修复后 sim_journal 抽样：", flush=True)
    for line in open(sim, encoding="utf-8-sig").read().splitlines()[:2]:
        print("  ", line[:120], flush=True)
    print("\n下一步：trade_journal.py TRADE_COLUMNS 改为 SIM_COLUMNS 顺序（代码同步）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
