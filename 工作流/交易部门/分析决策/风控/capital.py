"""资金管理 — 记录当前资金体量，按比例计算风险管理"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "journal"
CAPITAL_FILE = CONFIG_DIR / "capital.json"

RISK_RATIO = 0.015  # 单笔风险 = 总资金 × 1.5%


def _ensure():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not CAPITAL_FILE.exists():
        with open(CAPITAL_FILE, "w") as f:
            json.dump({"capital": 5600, "risk_ratio": RISK_RATIO}, f)


def get_capital() -> float:
    """读取当前资金"""
    _ensure()
    try:
        with open(CAPITAL_FILE, "r") as f:
            return json.load(f).get("capital", 5600)
    except (json.JSONDecodeError, FileNotFoundError):
        return 5600


def set_capital(amount: float) -> None:
    """更新资金"""
    _ensure()
    data = {"capital": amount, "risk_ratio": RISK_RATIO}
    with open(CAPITAL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def max_risk_per_trade() -> float:
    """单笔最大允许风险（元）"""
    return round(get_capital() * RISK_RATIO, 2)


def calc_lots(risk_per_share: float) -> int:
    """根据每股风险和总资金，计算可买手数"""
    max_risk = max_risk_per_trade()
    if risk_per_share <= 0:
        return 0
    lots = max(1, int(max_risk / risk_per_share / 100))
    return lots


def calc_trade_fee(amount: float, is_etf: bool = False) -> float:
    """计算交易手续费

    股票：万1.3，最低1元
    ETF：万0.5，最低0.5元
    印花税：仅卖出，万5（股票，ETF免）
    """
    if is_etf:
        rate = 0.00005
        minimum = 0.5
        stamp = 0.0
    else:
        rate = 0.00013
        minimum = 1.0
        stamp = amount * 0.0005  # 卖出印花税 万5

    commission = max(amount * rate, minimum)
    return round(commission + stamp, 2)
