"""交易纪律守护者 — 交易前检查清单 + 违规记录 + 盘后纪律报告

用户承认会有感性冲动，所以系统必须强制执行纪律。
TradeGuardian 在每次 scan/diagnose 时自动运行，输出纪律报告。
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"
VIOLATIONS_FILE = JOURNAL_DIR / "violations.json"


# ── 初始化 ──

def _ensure_journal():
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    if not VIOLATIONS_FILE.exists():
        with open(VIOLATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def _load_violations() -> list:
    _ensure_journal()
    try:
        with open(VIOLATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_violations(violations: list):
    _ensure_journal()
    with open(VIOLATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(violations, f, ensure_ascii=False, indent=2)


# ── 交易前检查清单 ──

def pre_trade_checklist(grade: str, scores: dict, risk_amount: float,
                        price: float, stop_price: float) -> dict:
    """交易前检查清单

    在每次执行交易前调用，返回检查结果。
    任何一项 FAIL → 系统建议不交易。

    Args:
        grade: 策略评级 (S/A/B/C)
        scores: 策略各维度评分 {"PT平台测试": ("S", "..." ), ...}
        risk_amount: 预计风险金额（元）
        price: 当前价格
        stop_price: 止损价

    Returns:
        {"passed": bool, "checks": list[{"name": ..., "status": "PASS"/"FAIL", "detail": ...}]}
    """
    checks = []

    # 1. 评级检查
    if grade in ("S", "A"):
        checks.append({"name": "评级检查", "status": "PASS",
                        "detail": f"当前评级{grade}级，合格"})
    else:
        checks.append({"name": "评级检查", "status": "FAIL",
                        "detail": f"当前评级{grade}级，仅S/A级可交易"})

    # 2. PT 检查
    pt = scores.get("PT平台测试", ("C", ""))[0]
    if pt in ("S", "A"):
        checks.append({"name": "PT平台测试", "status": "PASS",
                        "detail": f"平台测试{pt}级，≥2次有效测试"})
    else:
        checks.append({"name": "PT平台测试", "status": "FAIL",
                        "detail": f"平台测试{pt}级，不满足最低要求"})

    # 3. TY 检查
    ty = scores.get("TY统一区间", ("C", ""))[0]
    if ty in ("S", "A", "B"):
        checks.append({"name": "TY统一区间", "status": "PASS",
                        "detail": f"统一区间{ty}级"})
    else:
        checks.append({"name": "TY统一区间", "status": "FAIL",
                        "detail": "无窄幅整理区间"})

    # 4. DN 检查
    dn = scores.get("DN动能", ("C", ""))[0]
    if dn in ("S", "A", "B"):
        checks.append({"name": "DN动能", "status": "PASS",
                        "detail": f"动能{dn}级"})
    else:
        checks.append({"name": "DN动能", "status": "FAIL",
                        "detail": "动能不足，不参与"})

    # 5. SF 释放级别
    sf = scores.get("SF释放级别", ("C", ""))[0]
    if sf in ("S", "A"):
        checks.append({"name": "SF释放级别", "status": "PASS",
                        "detail": f"释放级别{sf}级"})
    else:
        checks.append({"name": "SF释放级别", "status": "FAIL",
                        "detail": "释放级别过高或已完全释放"})

    # 6. 止损距离检查
    if stop_price > 0 and price > stop_price:
        risk_pct = (price - stop_price) / price
        if risk_pct <= 0.05:
            checks.append({"name": "止损距离", "status": "PASS",
                            "detail": f"止损距离{risk_pct:.1%}，合理"})
        else:
            checks.append({"name": "止损距离", "status": "FAIL",
                            "detail": f"止损距离{risk_pct:.1%}，过大"})
    else:
        checks.append({"name": "止损距离", "status": "FAIL", "detail": "无有效止损"})

    # 7. 风险金额检查
    if risk_amount <= 150:
        checks.append({"name": "风险金额", "status": "PASS",
                        "detail": f"风险¥{risk_amount:.0f}，在¥150以内"})
    else:
        checks.append({"name": "风险金额", "status": "FAIL",
                        "detail": f"风险¥{risk_amount:.0f}，超过¥150上限"})

    passed = all(c["status"] == "PASS" for c in checks)

    return {"passed": passed, "checks": checks}


# ── 违规记录 ──

def record_violation(code: str, name: str, action: str,
                     blocked_by: list[str], user_response: str = "accepted_block") -> None:
    """记录一次违规操作

    每次用户试图跳过规则操作时，记录到 violations.json。
    """
    violations = _load_violations()
    violations.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "code": code,
        "name": name,
        "action": action,
        "blocked_by": blocked_by,
        "user_response": user_response,
    })
    _save_violations(violations)


# ── 盘后纪律报告 ──

def discipline_report() -> str:
    """生成当日纪律报告

    在 scan 命令执行完毕后自动输出。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    violations = _load_violations()
    today_violations = [v for v in violations if v["date"].startswith(today)]

    lines = []
    lines.append("=" * 50)
    lines.append(f"【纪律报告】{today}")

    if not today_violations:
        lines.append("  状态: ✅ 无违规操作，继续保持")
    else:
        lines.append(f"  状态: ⚠️ 今日有 {len(today_violations)} 次违规记录")
        for v in today_violations:
            lines.append(f"    涉及: {v['code']} {v['name']}")
            lines.append(f"    动作: {v['action']}")
            lines.append(f"    被阻止原因: {', '.join(v['blocked_by'])}")
            lines.append(f"    用户反应: {v['user_response']}")
    lines.append("=" * 50)

    return "\n".join(lines)
