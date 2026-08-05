"""R-009 模块3：模拟交易流水线（sim-open / sim-check / sim-stats）

模拟/小仓验证阶段：信号 → 可买性检查 → 模拟开仓 → 四层面出场（exit_manager 同源）
→ 过程指标对比回测。验证「代码执行 = 实盘执行」的一致性。

记录文件：journal/sim_journal.csv（独立于实盘 trade_journal.csv）
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from 分析决策.风控 import exit_manager as em
from 分析决策.风控.capital import calc_trade_fee, get_capital, max_risk_per_trade
from 分析决策.风控.position import Position

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"
SIM_FILE = JOURNAL_DIR / "sim_journal.csv"

SIM_COLUMNS = [
    "trade_id", "date", "symbol", "name", "direction", "market",
    "entry_price", "stop_loss", "volume", "grade_at_entry",
    "ty_high", "ty_low", "status",
    "exit_price", "exit_date", "exit_reason", "r_multiple", "pnl",
]


def _ensure():
    JOURNAL_DIR.mkdir(exist_ok=True)
    if not SIM_FILE.exists():
        with open(SIM_FILE, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(SIM_COLUMNS)


def _read_all() -> list[dict]:
    _ensure()
    with open(SIM_FILE, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_all(rows: list[dict]) -> None:
    _ensure()
    with open(SIM_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SIM_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def check_affordability(price: float, risk_per_share: float) -> tuple[int, str]:
    """可买性检查：资金上限与风险上限取 min，整手向下取整

    Returns: (股数, 拒绝原因) — 股数 <100 表示不可买
    """
    balance = get_capital()
    risk_amt = max_risk_per_trade()
    if price <= 0 or risk_per_share <= 0:
        return 0, "参数无效"
    shares = int(min(balance // price, risk_amt // risk_per_share) / 100) * 100
    if shares < 100:
        return 0, f"买不起（每股风险{risk_per_share:.2f}元 / 资金{balance:.0f}元）"
    return shares, ""


def sim_open(code: str, price: float, stop: float, grade: str = "",
             name: str = "", ty_high: float = 0, ty_low: float = 0) -> str:
    """模拟开仓（多头）。返回结果文本。"""
    risk_ps = price - stop
    if risk_ps <= 0:
        return f"❌ 止损价({stop})须低于进场价({price})"
    shares, reason = check_affordability(price, risk_ps)
    if shares < 100:
        return f"❌ {code} 不可买：{reason}"

    rows = _read_all()
    tid = f"SIM{datetime.now():%Y%m%d%H%M%S}"
    rows.append({
        "trade_id": tid, "date": datetime.now().strftime("%Y-%m-%d"),
        "symbol": code, "name": name, "direction": "long", "market": "stock",
        "entry_price": price, "stop_loss": stop, "volume": shares,
        "grade_at_entry": grade, "ty_high": ty_high, "ty_low": ty_low,
        "status": "open", "exit_price": "", "exit_date": "", "exit_reason": "",
        "r_multiple": "", "pnl": "",
    })
    _write_all(rows)
    return (f"✅ 模拟开仓 {code}({name or '无名'}) 评级{grade or '—'}\n"
            f"  进场 {price} | 止损 {stop} | 风险 {risk_ps:.2f}元/股 | {shares}股\n"
            f"  单笔风险 {risk_ps * shares:.0f}元（上限{max_risk_per_trade():.0f}元）| ID {tid}")


def sim_check() -> str:
    """每日检查：拉最新K线，四层面出场判断，出场则记录"""
    rows = _read_all()
    open_rows = [r for r in rows if r["status"] == "open"]
    if not open_rows:
        return "无持仓中的模拟交易"

    from 数据基础.数据.fetcher import get_daily_kline
    out = []
    changed = 0
    for r in open_rows:
        code = r["symbol"]
        try:
            df = get_daily_kline(code, use_cache=True)
        except Exception as e:
            out.append(f"  {code}: 数据获取失败 {e}")
            continue
        if df is None or len(df) < 3:
            out.append(f"  {code}: 数据不足")
            continue
        pos = Position(symbol=code, direction="long", market="stock",
                       entry_price=float(r["entry_price"]),
                       initial_stop=float(r["stop_loss"]),
                       current_stop=float(r["stop_loss"]),
                       volume=int(r["volume"]),
                       ty_high=float(r["ty_high"] or 0),
                       ty_low=float(r["ty_low"] or 0),
                       grade_at_entry=r["grade_at_entry"])
        verdict = em.evaluate_exit(pos, df)
        latest = df.iloc[-1]
        if verdict["should_exit"]:
            exit_price = verdict["exit_price"] or float(latest["收盘"])
            pnl = (exit_price - float(r["entry_price"])) * pos.volume
            fee_in = calc_trade_fee(float(r["entry_price"]) * pos.volume)
            fee_out = calc_trade_fee(exit_price * pos.volume)
            pnl -= fee_in + fee_out
            risk_amt = pos.risk_per_share() * pos.volume
            r_mult = pnl / risk_amt if risk_amt > 0 else 0
            r["status"] = "closed"
            r["exit_price"] = exit_price
            r["exit_date"] = str(latest["日期"])[:10]
            r["exit_reason"] = verdict["reason"]
            r["r_multiple"] = f"{r_mult:.2f}"
            r["pnl"] = f"{pnl:.2f}"
            out.append(f"  {code}: 🎯 出场 [{verdict['reason'][:40]}] R={r_mult:+.2f} 盈亏{pnl:+,.0f}元")
            changed += 1
        else:
            updates = f"止损移至{verdict['stop_update']}" if verdict.get("stop_update") else "持有中"
            out.append(f"  {code}: {updates}（现{float(latest['收盘']):.2f}，R={pos.current_r_multiple(float(latest['收盘'])):+.2f}）")
    if changed:
        _write_all(rows)
    return "\n".join(out)


def sim_stats() -> str:
    """过程指标：执行一致性 + 胜率/平均R/连败，对比回测"""
    rows = _read_all()
    closed = [r for r in rows if r["status"] == "closed"]
    open_n = len([r for r in rows if r["status"] == "open"])
    if not closed:
        return f"模拟交易共 {len(rows)} 笔（未平仓 {open_n}），暂无已平仓记录"

    rs = [float(r["r_multiple"]) for r in closed]
    wins = [r for r in closed if float(r["r_multiple"]) > 0]
    losses = [r for r in closed if float(r["r_multiple"]) <= 0]
    # 连败
    max_streak = cur = 0
    for r in closed:
        cur = cur + 1 if float(r["r_multiple"]) <= 0 else 0
        max_streak = max(max_streak, cur)
    # 执行一致性：exit_reason 非空且按规则（非"人为干预"）占比
    rule_exits = [r for r in closed if r["exit_reason"] and "人为" not in r["exit_reason"]]
    exec_rate = len(rule_exits) / len(closed) * 100

    avg_r = sum(rs) / len(rs)
    win_rate = len(wins) / len(closed) * 100
    total_pnl = sum(float(r["pnl"]) for r in closed)

    W = 74
    line = "-" * W
    out = [line, "模拟交易过程指标（R-009 模块3）".center(W), line]
    out.append(f"  已平仓            {len(closed):>4} 笔 | 持仓中 {open_n} 笔")
    out.append(f"  胜率              {win_rate:>6.1f}%（回测预期 ~50% prebreak/20d）")
    out.append(f"  平均 R            {avg_r:>+6.3f}（回测全样本 0.506）")
    out.append(f"  累计盈亏          {total_pnl:>+8.2f} 元")
    out.append(f"  最大连败          {max_streak:>4} 笔（蒙特卡洛最坏 21 笔）")
    out.append(f"  执行一致性        {exec_rate:>6.1f}%（目标 ≥95%——按规则出场占比）")
    out.append(line)
    out.append("  判定口径：前 50 笔只看执行一致性；100 笔才看收益是否符合回测预期")
    out.append(line)
    return "\n".join(out)
