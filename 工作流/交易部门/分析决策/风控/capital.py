"""资金管理 — 记录当前资金体量，按比例计算风险管理"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "交易日志"
CAPITAL_FILE = CONFIG_DIR / "capital.json"

# 资金配置（R-050 定案 2026-08-11 老板拍板，替代 R-046 单一比例口径）：
# 「风险比例 = 0.025（2.5%）× 当前资金 + 仓位上限 = 无限制（只要有 S 级候选就买）」
# ——依据 R-050 档位扩展与归因（交易部审核通过）：8401 档 0.025 近 7 年回撤 -16.3%
# 安全（收益 +1534%/盈亏比 4.24，蒙卡 1 万次 100% 盈利 0% 破产）；26 年口径无顶
# （0.03~0.04 平台 DDR 110~158）但近 7 年 0.030 起失控。
# ⚠️ 资金 ≥2 万降档 0.012855 的建议（R-050 实测 30k 档 0.016 即破 -20% 线）
# 老板 2026-08-11 暂不采纳、存疑待复议——当前统一只用 0.025，资金涨大后重新评估。
# 注入机制（R-046 保留）：不定额不定时，每次注入老板同步金额 → apply_inject() 登记
# → 风险额按 0.025×新资金自动重算；资金回落不自动降风险额。
# 旧口径（R-039：8401/108 元/5 仓/月注入 3000；R-046：单一 0.012855）已废弃，
# 文档级联见策略版本存档。
RISK_RATIO = 0.025           # 单笔风险比例（R-050 定案：2.5%，老板拍板）


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


def get_risk_ratio() -> float:
    """当前风险比例（R-050 定案 2026-08-11：统一 0.025；老板暂不采纳 ≥2 万降档）

    capital.json 的 risk_ratio 字段可覆盖（apply_inject 同步）；缺省 0.025。
    资金 ≥2 万降档 0.012855 的建议存疑待复议（R-050 实测 30k 档 0.016 破线）。
    max_risk_per_trade 与执行卡/模拟线统一读此值。
    """
    _ensure()
    try:
        with open(CAPITAL_FILE, "r") as f:
            return float(json.load(f).get("risk_ratio", RISK_RATIO))
    except (json.JSONDecodeError, FileNotFoundError, TypeError, ValueError):
        return RISK_RATIO


def set_capital(amount: float, risk_ratio: float | None = None) -> None:
    """更新资金（通用；risk_ratio 缺省保持现值）"""
    _ensure()
    rr = risk_ratio if risk_ratio is not None else get_risk_ratio()
    data = {"capital": amount, "risk_ratio": rr}
    with open(CAPITAL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def apply_inject(inject_amount: float) -> dict:
    """注入登记（R-046 不定额注入机制 · 老板每次注入后同步调用）

    新资金 = 现值 + 注入额；风险额按 0.025 × 新资金自动重算（R-050 定案统一比例，
    ≥2 万降档暂不采纳）。资金回落（亏损致净值下跌）不自动降风险额——避免频繁
    波动；净值大幅回落 >20% 时由总助提醒、老板拍板。

    Args:
        inject_amount: 本次注入金额（元，>0）

    Returns:
        {"capital": 新资金, "risk_ratio": 新比例, "risk_amt": 新单笔风险额}
    """
    if inject_amount <= 0:
        raise ValueError(f"注入金额必须 >0，收到 {inject_amount}")
    new_capital = round(get_capital() + inject_amount, 2)
    new_ratio = RISK_RATIO  # R-050 统一比例（连续，不因注入次数变化）
    set_capital(new_capital, new_ratio)
    return {"capital": new_capital, "risk_ratio": new_ratio,
            "risk_amt": round(new_capital * new_ratio, 2)}


def max_risk_per_trade(scale: float = 1.0) -> float:
    """单笔最大允许风险（元）

    G3 0.5R 环境仓位（补完计划 · 2026-08-06 接入）：
    经验型模式/知识卡.md 仓位与环境「环境好（非右下角）→ 正常 1R；
    环境不好（右下角）→ 0.5R」（2024-06-22/29）。环境判定见
    indicators.environment_quality（个股 60 日窗口右下角特征），
    scale=0.5 由调用方按环境质量传入。与 B1 环境闸门（gate.py，大盘指数
    当日跌幅执行层否决/降级）维度不同：B1 管大盘"做不做"，G3 管个股
    环境"做多少"，两者互补不重复。

    Args:
        scale: 风险缩放系数（1.0=正常 1R，0.5=环境弱 0.5R）

    Returns:
        单笔最大允许风险金额（元）
    """
    return round(get_capital() * get_risk_ratio() * scale, 2)


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


# ══════════════════════════════════════════════════════════
# 资金管理升级（内训第26节 + 2024周会，2026-08-04 补课代码化）
# 老师核心：
#   1) 单笔风险 = 可承受最大亏损 ÷ 常见连续止损次数（分母留余量）
#   2) 固定金额（每笔亏一样多）优于固定仓位
#   3) 复利方案：单利 / 余额复利 / 向上复利（水上按峰值、水下余额）
# ══════════════════════════════════════════════════════════

DEFAULT_MAX_STREAK = 10   # 常见连续止损次数（分母留余量：内训"7 用 10"口径）
DEFAULT_MAX_DRAWDOWN = 0.20  # 可承受最大回撤（老师：击穿性风险必须先封死）


def calc_risk_by_drawdown(capital: float, max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN,
                          max_streak: int = DEFAULT_MAX_STREAK) -> float:
    """由可承受回撤 + 连亏次数推导单笔风险比例

    公式（内训第26节）：单笔风险 = 可承受最大亏损 ÷ 常见连续止损次数
    例：可承受 20% 回撤 ÷ 10 次连亏（留余量）= 2% 单笔风险

    Args:
        capital: 总资金
        max_drawdown_pct: 可承受最大回撤（比例）
        max_streak: 常见连续止损次数（分母留余量，老师"7 次用 10"）

    Returns:
        单笔风险比例（如 0.02 = 2%）
    """
    if max_streak <= 0:
        return RISK_RATIO
    risk = max_drawdown_pct / max_streak
    return round(risk, 4)


def simulate_compounding(capital: float, risk_pct: float, trades: int,
                         win_rate: float = 0.5, avg_win_rr: float = 2.0,
                         avg_loss_rr: float = 1.0, mode: str = "balance") -> dict:
    """资金复利模拟（内训第26节三种方案）

    Args:
        capital: 初始资金
        risk_pct: 单笔风险比例
        trades: 交易次数
        win_rate: 胜率
        avg_win_rr: 平均盈利 R 倍数
        avg_loss_rr: 平均亏损 R 倍数
        mode: "simple"=单利 / "balance"=余额复利 / "peak"=向上复利（水上按峰值，水下余额）

    Returns:
        {"final": float, "peak": float, "max_drawdown": float}
    """
    bal = capital
    peak = capital
    low_water = capital  # 水下基准（向上复利：水上按峰值，水下按余额）
    max_dd = 0.0
    import random
    random.seed(7)
    for i in range(trades):
        # 风险基数：固定金额（老师：固定金额优于固定仓位）
        if mode == "simple":
            risk_base = capital  # 单利：始终按初始资金
        elif mode == "peak":
            risk_base = max(peak, bal) if bal >= low_water else bal
        else:
            risk_base = bal
        risk_amt = risk_base * risk_pct
        if random.random() < win_rate:
            bal += risk_amt * avg_win_rr
        else:
            bal -= risk_amt * avg_loss_rr
        peak = max(peak, bal)
        max_dd = max(max_dd, peak - bal)
        if mode == "peak" and bal < low_water:
            low_water = bal
    return {"final": round(bal, 2), "peak": round(peak, 2), "max_drawdown": round(max_dd, 2)}


def risk_scheme_suggest(capital: float) -> dict:
    """资金方案建议（老师口径完整输出）

    Returns:
        {"risk_pct": float, "risk_amount": float, "mode": str, "rationale": str}
    """
    risk_pct = calc_risk_by_drawdown(capital)
    return {
        "risk_pct": risk_pct,
        "risk_amount": round(capital * risk_pct, 2),
        "mode": "fixed_amount",
        "rationale": f"单笔风险{risk_pct:.1%} = 可承受回撤{DEFAULT_MAX_DRAWDOWN:.0%} ÷ {DEFAULT_MAX_STREAK}次连亏（分母留余量，内训26节口径）",
    }
