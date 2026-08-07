"""观察池 + 模式假设自动验证（R-037① / R-036① · 2026-08-08 老板拍板）

投资研究员四层漏斗（外部资料→观察池→模式假设→核心策略库）落地：
- **观察池.csv**：模拟条件单自动登记（sim_auto_open 挂钩），状态随 sim_check
  自动流转（跟踪中 → 成交跟踪 / 未触发撤销 / 兑现 / 出池），零手工录入。
- **假设自动验证**：内置基础假设（评级档 S/A/B、环境档 1R/0.5R、成交延迟档）
  + 自定义假设登记——每日自动从观察池已了结样本统计，满 MIN_SAMPLES 笔自动
  判定「有效 / 证伪提示」，替代"等投资研究员手动翻数据"。

使用：python main.py track observe（观察池总览）/ track hypothesis-check（假设验证）
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 观察池目录（投资研究员知识库管理权内）
OBSERVE_DIR = Path(__file__).resolve().parent.parent.parent / "策略" / "知识库" / "观察池"
OBSERVE_FILE = OBSERVE_DIR / "观察池.csv"
HYPOTHESIS_FILE = OBSERVE_DIR / "假设.csv"

OBSERVE_COLUMNS = ["trade_id", "date", "symbol", "name", "grade", "trigger",
                   "stop", "status", "exit_reason", "r", "note"]
# 状态机：跟踪中 → 成交跟踪（触发成交后）/ 未触发撤销；成交跟踪 → 兑现(盈利) / 出池(亏损)
# 简化：跟踪中(挂单) / 成交(open) / 兑现(R>0 closed) / 出池(R≤0 closed) / 撤销(未触发)
MIN_SAMPLES = 5  # 假设判定最小样本（不足提示积累，不误判）


def _ensure():
    OBSERVE_DIR.mkdir(parents=True, exist_ok=True)
    for path, cols in ((OBSERVE_FILE, OBSERVE_COLUMNS), (HYPOTHESIS_FILE, None)):
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                if cols:
                    csv.writer(f).writerow(cols)
                else:
                    f.write("编号,假设描述,分组字段,状态,验证记录\n")


def _read_all() -> list[dict]:
    _ensure()
    with open(OBSERVE_FILE, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_all(rows: list[dict]) -> None:
    _ensure()
    with open(OBSERVE_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OBSERVE_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def register(trade_id: str, date: str, symbol: str, name: str, grade: str,
             trigger: float, stop: float) -> None:
    """模拟条件单登记（sim_auto_open 挂钩）：观察池新增「跟踪中」行"""
    rows = _read_all()
    if any(r.get("trade_id") == trade_id for r in rows):
        return  # 幂等
    rows.append({"trade_id": trade_id, "date": date, "symbol": symbol,
                 "name": name, "grade": grade, "trigger": f"{trigger:.2f}",
                 "stop": f"{stop:.2f}", "status": "跟踪中", "exit_reason": "",
                 "r": "", "note": "模拟条件单挂单"})
    _write_all(rows)


def update(trade_id: str, status: str, exit_reason: str = "",
           r: float | None = None, note: str = "") -> None:
    """观察池状态流转（sim_check 挂钩）：
    成交 → status=成交；撤销 → 未触发撤销；平仓 → 兑现(R>0)/出池(R≤0)"""
    rows = _read_all()
    for row in rows:
        if row.get("trade_id") == trade_id:
            row["status"] = status
            if exit_reason:
                row["exit_reason"] = exit_reason
            if r is not None:
                row["r"] = f"{r:.2f}"
            if note:
                row["note"] = note
            break
    _write_all(rows)


def summarize(rows: list[dict] | None = None) -> str:
    """观察池总览（track observe）"""
    rows = rows if rows is not None else _read_all()
    if not rows:
        return "观察池空（模拟线条件单成交后自动登记）"
    counts = {}
    closed_r = []
    for r in rows:
        st = r.get("status", "?")
        counts[st] = counts.get(st, 0) + 1
        if r.get("status") in ("兑现", "出池"):
            try:
                closed_r.append(float(r["r"]))
            except (TypeError, ValueError):
                pass
    out = [f"观察池共 {len(rows)} 笔："]
    out.append("  " + " | ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if closed_r:
        avg = sum(closed_r) / len(closed_r)
        win = sum(1 for x in closed_r if x > 0) / len(closed_r) * 100
        out.append(f"  已了结 {len(closed_r)} 笔：avgR {avg:+.2f} | 胜率 {win:.0f}%")
    else:
        out.append("  已了结 0 笔（表现统计待积累）")
    out.append("  最近记录：")
    for r in rows[-5:]:
        out.append(f"    {r['date']} {r['symbol']} {r['name']} "
                   f"[{r['grade']}] 触发{r['trigger']} → {r['status']}")
    return "\n".join(out)


def hypothesis_check(rows: list[dict] | None = None) -> str:
    """模式假设自动验证（track hypothesis-check）

    内置基础假设：评级档（S vs A vs B）、环境档（1R vs 0.5R）、成交延迟档
    （0-1 天 vs 2-3 天，对应延迟分布结论：0 天突破质量最高）。
    判定：样本 ≥MIN_SAMPLES 且 avgR > 0 → 「有效」；avgR < 0 → 「证伪提示」；
    样本不足 → 「积累中」。结果供投资研究员人工复核后升入核心策略库。
    """
    rows = rows if rows is not None else _read_all()
    closed = [r for r in rows if r.get("status") in ("兑现", "出池")]
    if not closed:
        return "观察池暂无已了结样本，假设验证待积累（模拟线成交后自动喂数据）"

    def stats(rs: list[float]) -> str:
        if len(rs) < MIN_SAMPLES:
            return f"样本{len(rs)}<{MIN_SAMPLES} 积累中"
        avg = sum(rs) / len(rs)
        verdict = "✅ 有效" if avg > 0 else "⚠️ 证伪提示"
        return f"{len(rs)}笔 avgR {avg:+.2f} → {verdict}"

    # 按 trade_id 关联 sim_journal 取 grade/env/成交延迟（观察池行含 grade，延迟需 journal）
    journal = {}
    from 分析决策.跟踪 import sim_trading
    for j in sim_trading._read_all():
        journal[j.get("trade_id", "")] = j

    groups = {
        "S 级 vs A/B 级": {"S": [], "A/B": []},
        "环境档 1R vs 0.5R": {"1R": [], "0.5R": []},
        "成交延迟 0-1 天 vs 2-3 天": {"0-1天": [], "2-3天": []},
    }
    for r in closed:
        tid = r.get("trade_id", "")
        j = journal.get(tid, {})
        try:
            rv = float(r["r"])
        except (TypeError, ValueError):
            continue
        grade = r.get("grade", "")
        groups["S 级 vs A/B 级"]["S" if grade == "S" else "A/B"].append(rv)
        env = j.get("env_scale", "")
        groups["环境档 1R vs 0.5R"]["1R" if env == "1.0" else "0.5R"].append(rv)
        # 成交延迟：journal date(成交日) - created_date
        try:
            created = str(j.get("created_date") or "")[:10]
            filled = str(j.get("date") or "")[:10]
            from datetime import date as _d
            d0 = _d.fromisoformat(created) if created else None
            d1 = _d.fromisoformat(filled) if filled else None
            if d0 and d1:
                gap = (d1 - d0).days
                key = "0-1天" if gap <= 1 else "2-3天"
                groups["成交延迟 0-1 天 vs 2-3 天"][key].append(rv)
        except ValueError:
            pass

    out = ["模式假设自动验证（观察池已了结样本）："]
    for title, sub in groups.items():
        out.append(f"  {title}:")
        for k, rs in sub.items():
            out.append(f"    {k}: {stats(rs)}")
    out.append("  说明：avgR>0 且样本≥5 判「有效」，人工复核后升核心策略库；")
    out.append("        样本不足不误判（小样本结论不可信——方法论铁律）。")
    return "\n".join(out)
