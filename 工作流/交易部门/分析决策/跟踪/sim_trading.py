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

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "交易日志"
SIM_FILE = JOURNAL_DIR / "sim_journal.csv"

SIM_COLUMNS = [
    "trade_id", "date", "symbol", "name", "direction", "market",
    "entry_price", "stop_loss", "volume", "grade_at_entry",
    "ty_high", "ty_low", "status",
    "exit_price", "exit_date", "exit_reason", "r_multiple", "pnl",
    "env_scale", "phase",
]

# G3 当日头寸统一（补完计划第二批 · 2026-08-06 定案）：
# 当日档进程内缓存（date -> scale）——同日多次开仓档位一致；
# 跨会话一致性由 journal 的 env_scale 列兜底（sim_open 先查当日已开仓记录）。
# G3 分步建仓（2026-08-06 定案，2024-06-29 周会原文）：0.5R = 分步建仓第一步
# （非终局减半）——phase 列标注：
#   "half"       = 0.5R 起步，待下一根收线确认（确认 → 补 0.5R 总 1R；不确认 → 平仓）
#   "confirmed"  = 分步已确认并补至 1R（之后走正常四层面出场）
#   ""           = 直接 1R 全仓（正常环境日，不分步）
_day_env_cache: dict[str, float] = {}


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


def _sync_r_curve(rows: list[dict]) -> int:
    """虚拟盘线出场 R 自动同步 r_curve.csv（2026-08-07 老板拍板双线记录）

    双线同账本：模拟盘 R 自动录入（note="sim"），实盘线用 rcurve record
    （note="live"）——dual_line 双线对照读取同一 r_curve 账本。
    防重复：只同步本次变 closed 且 r_multiple 非空的行。
    """
    from 分析决策.跟踪.r_curve import add_record as rcurve_add
    n = 0
    for r in rows:
        if r.get("status") != "closed":
            continue
        try:
            rm = float(r.get("r_multiple") or 0)
        except (TypeError, ValueError):
            continue
        if r.get("_synced"):
            continue
        try:
            rcurve_add(str(r.get("exit_date") or r.get("date"))[:10], rm,
                       entry=float(r.get("entry_price") or 0) or None,
                       stop=float(r.get("stop_loss") or 0) or None,
                       exit_price=float(r.get("exit_price") or 0) or None,
                       symbol=str(r.get("symbol", "")), note="sim")
            r["_synced"] = True
            n += 1
        except Exception:  # noqa: BLE001 - 同步失败不阻断模拟主流程
            continue
    return n


def check_affordability(price: float, risk_per_share: float,
                        risk_scale: float = 1.0) -> tuple[int, str]:
    """可买性检查：资金上限与风险上限取 min，整手向下取整

    G3 0.5R 环境仓位（补完计划 · 2026-08-06）：risk_scale 按个股环境质量
    传入（经验型模式/知识卡.md「环境不好（右下角）→ 0.5R」，判定见
    indicators.environment_quality），缩放单笔风险额后倒推手数。
    分步建仓（G3 2026-08-06 定案）补仓同用 risk_scale=0.5（等额 0.5R）。

    Returns: (股数, 拒绝原因) — 股数 <100 表示不可买
    """
    balance = get_capital()
    risk_amt = max_risk_per_trade(scale=risk_scale)
    if price <= 0 or risk_per_share <= 0:
        return 0, "参数无效"
    shares = int(min(balance // price, risk_amt // risk_per_share) / 100) * 100
    if shares < 100:
        return 0, f"买不起（每股风险{risk_per_share:.2f}元 / 资金{balance:.0f}元）"
    return shares, ""


def _market_env_scale(date: str | None = None) -> float | None:
    """当日市场环境档：上证指数 60 日窗口 environment_quality → 缩放系数

    G3 当日头寸统一（2026-08-06 定案）：知识卡 经验型模式/知识卡.md「同一
    市场环境头寸统一：不混合 1R 和 0.5R」（2024-06-01 周会原文：如果持仓都
    是 0.5R → 说明这次普遍状态不好 → 保持全是 0.5R；进的都是一 R → 市场
    表现都很好）。故当日档位由"市场整体环境"决定（指数右下角 = 大盘弱势 →
    当日全池 0.5R），而非逐股独立判定（逐股判定会致同日 A 票 1R / B 票 0.5R
    并存，违反知识卡）。
    判定与个股 environment_quality 同函数同语义（60 日窗口低点下移/反弹无力/
    横盘死水），对象换为上证指数（index_data.load_index_daily，本地缓存）。

    Args:
        date: 当日日期（YYYY-MM-DD，当前仅用于日志语义，预留）

    Returns:
        0.5 / 1.0；指数数据不可得 → None（调用方回退个股判定）
    """
    try:
        from 分析决策.分析.indicators import environment_quality
        from 分析决策.市场环境.index_data import load_index_daily
        idx = load_index_daily("上证指数")
        if idx is None or len(idx) < 30:
            return None
        env = environment_quality(idx)
        return 0.5 if env["quality"] in ("weak", "bad") else 1.0
    except Exception:  # noqa: BLE001 - 指数不可得 → 回退个股判定（放行侧，不误伤开仓）
        return None


def _env_risk_scale(code: str, date: str | None = None) -> tuple[float, str]:
    """G3 当日头寸统一判定：当日市场环境档 → 风险缩放系数

    优先链：当日进程内缓存（同日多次开仓一致）→ 当日市场环境（上证指数
    60 日窗口，见 _market_env_scale）→ 回退个股自身环境判定（原 G3 逻辑，
    指标同源 indicators.environment_quality）。任何一步数据不可得 → 默认 1R
    （放行侧，不因数据问题误伤开仓）。

    Args:
        code: 股票代码（回退路径用）
        date: 当日日期（YYYY-MM-DD；None=系统今日）

    Returns:
        (scale, 说明文本)：1.0=正常 1R，0.5=环境弱 0.5R
    """
    today = date or datetime.now().strftime("%Y-%m-%d")
    cached = _day_env_cache.get(today)
    if cached is not None:
        return cached, f"当日市场环境统一→{'0.5R' if cached == 0.5 else '1R'}"
    s = _market_env_scale()
    if s is not None:
        _day_env_cache[today] = s
        return s, f"当日市场环境统一→{'0.5R' if s == 0.5 else '1R'}"
    # 回退：个股自身环境判定（原 G3；指数不可得时首笔锚定当日档）
    try:
        from 分析决策.分析.indicators import environment_quality
        from 数据基础.数据.fetcher import get_daily_kline
        df = get_daily_kline(code, use_cache=True)
        if df is None or len(df) < 30:
            _day_env_cache[today] = 1.0
            return 1.0, "数据不足→1R"
        env = environment_quality(df)
        if env["quality"] in ("weak", "bad"):
            _day_env_cache[today] = 0.5
            return 0.5, f"个股环境{env['quality']}→0.5R"
        _day_env_cache[today] = 1.0
        return 1.0, "个股环境好→1R"
    except Exception:  # noqa: BLE001 - 判定异常 → 默认 1R（放行侧）
        _day_env_cache[today] = 1.0
        return 1.0, "环境判定异常→1R"


def _day_open_scale() -> float | None:
    """当日已开仓记录的 env_scale（跨会话统一锚）

    当日 journal 已有开仓记录 → 返回其档位（取最弱：当日出现过 0.5R →
    当日统一 0.5R，与 2024-06-01"保持全是 0.5R"语义一致）；
    无当日记录 → None（首笔开仓走 _env_risk_scale 判定）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    scales = [float(r["env_scale"]) for r in _read_all()
              if r.get("date") == today and r.get("status") == "open"
              and r.get("env_scale")]
    if not scales:
        return None
    return min(scales)


def _check_half_position(df, r: dict) -> dict:
    """0.5R 分步建仓·下一根收线确认检查（G3 · 2024-06-29 周会原文）

    开仓日 = journal 记录日期；确认收线 = 开仓日下一根 K 线。
    确认规则单一来源：indicators.half_position_confirm_delay2（2026-08-06 老板
    拍板替换 strict 为生产规则：首根 reject 且存在第二根（T+2）→ 以 T+1 为
    开仓日二次判定——确认 → 补 0.5R / T+2 触止损 → 止损出场 / T+2 仍未确认 →
    以 T+2 收盘平仓；首根 reject 且无 T+2（数据只到首根）→ wait 等待二次确认，
    次日数据到位再判；首根即确认/触止损 → 同 strict 即时处理）。

    Returns:
        {"action": "add"/"exit_stop"/"exit_reject"/"wait"/"hold",
         "close": float, "exit_date": str, "reason": str,
         "add_shares": int, "add_price": float}
        add        → 收线确认，补仓 add_shares 股 @ add_price（总 1R）
        exit_stop  → 确认日触止损（层面1 优先，按止损价平仓）
        exit_reject→ 收线未确认 → 0.5R 马上平仓（按确认日收盘价）
        wait       → 收线未出现 / 首根 reject 等待二次确认（T+2 未到位），持有等待
        hold       → 已确认但补仓受限（资金/整手不足）→ 保持 0.5R 持有
    """
    from 分析决策.分析.indicators import half_position_confirm_delay2
    entry_price = float(r["entry_price"])
    stop = float(r["stop_loss"])
    dates = df["日期"].astype(str).str[:10].values
    entry_idx = None
    for i, d in enumerate(dates):
        if d == r["date"]:
            entry_idx = i
            break
    if entry_idx is None or entry_idx + 1 >= len(df):
        return {"action": "wait", "close": 0.0, "exit_date": "", "reason": "收线未出现",
                "add_shares": 0, "add_price": 0.0}
    verdict = half_position_confirm_delay2(df, entry_price, stop, entry_idx + 1)
    exit_date = str(dates[verdict["conf_idx_used"]])[:10]
    if verdict["stopped"]:
        return {"action": "exit_stop", "close": stop, "exit_date": exit_date,
                "reason": verdict["reason"], "add_shares": 0, "add_price": 0.0}
    if verdict["wait"]:
        return {"action": "wait", "close": 0.0, "exit_date": "", "reason": "收线未出现",
                "add_shares": 0, "add_price": 0.0}
    if verdict["reject"]:
        if not verdict["second_checked"]:
            # 首根 reject 且 T+2 未到位（实时模拟逐日推进）→ 等待二次确认
            return {"action": "wait", "close": 0.0, "exit_date": "",
                    "reason": "首根未确认，等待延迟二次确认(T+2)",
                    "add_shares": 0, "add_price": 0.0}
        return {"action": "exit_reject", "close": verdict["close"], "exit_date": exit_date,
                "reason": f"分步建仓收线未确认({verdict['reason']})→0.5R平仓",
                "add_shares": 0, "add_price": 0.0}
    # 确认 → 补仓 0.5R（等额；可买性检查防止资金/整手不足时盲目翻倍）
    add_price = verdict["close"]
    risk_ps = entry_price - stop
    add_shares, reason = check_affordability(add_price, risk_ps, risk_scale=0.5)
    if add_shares < 100:
        return {"action": "hold", "close": add_price, "exit_date": "",
                "reason": f"确认但补仓不可买({reason})，保持 0.5R",
                "add_shares": 0, "add_price": 0.0}
    return {"action": "add", "close": add_price, "exit_date": "",
            "reason": "收线确认，补仓 0.5R", "add_shares": add_shares, "add_price": add_price}


def sim_open(code: str, price: float, stop: float, grade: str = "",
             name: str = "", ty_high: float = 0, ty_low: float = 0,
             risk_scale: float | None = None) -> str:
    """模拟开仓（多头）。返回结果文本。

    Args:
        risk_scale: 风险缩放系数（None=按当日头寸统一自动判定 1R/0.5R，G3
            2026-08-06：先查当日已开仓记录档位 → 无则按当日市场环境判定；
            手动指定则优先且不参与统一逻辑）。
            0.5R = 分步建仓第一步（2026-08-06 定案 · 2024-06-29 周会原文）：
            当日开 0.5R 的持仓进入 phase="half"，下一根收线确认后补 0.5R
            （总 1R）；收线不确认 → 马上平仓（非终局减半语义）。
    """
    risk_ps = price - stop
    if risk_ps <= 0:
        return f"❌ 止损价({stop})须低于进场价({price})"
    if risk_scale is None:
        # 当日头寸统一（2024-06-01 知识卡）：同日不混合 1R 和 0.5R。
        # 当日已有开仓记录 → 统一沿用当日档（含 0.5R 强制当日全降）；
        # 无记录 → 当日市场环境判定（指数 60 日窗口）并写入记录。
        day_scale = _day_open_scale()
        if day_scale is not None:
            risk_scale = day_scale
            env_note = f"当日统一沿用{'0.5R' if day_scale == 0.5 else '1R'}"
        else:
            risk_scale, env_note = _env_risk_scale(code)
    else:
        env_note = f"手动缩放{risk_scale:g}R"
    shares, reason = check_affordability(price, risk_ps, risk_scale=risk_scale)
    if shares < 100:
        return f"❌ {code} 不可买：{reason}"

    rows = _read_all()
    tid = f"SIM{datetime.now():%Y%m%d%H%M%S}"
    phase = "half" if risk_scale == 0.5 else ""
    rows.append({
        "trade_id": tid, "date": datetime.now().strftime("%Y-%m-%d"),
        "symbol": code, "name": name, "direction": "long", "market": "stock",
        "entry_price": price, "stop_loss": stop, "volume": shares,
        "grade_at_entry": grade, "ty_high": ty_high, "ty_low": ty_low,
        "status": "open", "exit_price": "", "exit_date": "", "exit_reason": "",
        "r_multiple": "", "pnl": "", "env_scale": risk_scale, "phase": phase,
    })
    _write_all(rows)
    phase_note = ("，分步起步：下一根收线确认后补至 1R，未确认则平仓"
                  if phase == "half" else "")
    return (f"✅ 模拟开仓 {code}({name or '无名'}) 评级{grade or '—'}\n"
            f"  进场 {price} | 止损 {stop} | 风险 {risk_ps:.2f}元/股 | {shares}股\n"
            f"  单笔风险 {risk_ps * shares:.0f}元（上限{max_risk_per_trade(scale=risk_scale):.0f}元）"
            f" | {env_note}{phase_note} | ID {tid}")


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
        # G3 分步建仓（2026-08-06 · 2024-06-29 周会原文）：phase=="half" 的持仓
        # 先做"下一根收线确认"——确认 → 补 0.5R（总 1R，phase→confirmed 后走
        # 正常四层面出场）；不确认 → 马上平仓（0.5R）；触止损 → 层面1 平仓。
        # 补仓后（confirmed）与直接 1R 持仓同路径。
        phase = r.get("phase", "")
        if phase == "half":
            step = _check_half_position(df, r)
            if step["action"] == "wait":
                out.append(f"  {code}: 0.5R分步待确认（{step['reason']}，持有等待）")
                continue
            if step["action"] == "hold":
                out.append(f"  {code}: {step['reason']}")
                continue
            if step["action"] == "add":
                old_v = int(r["volume"])
                old_e = float(r["entry_price"])
                add_v = step["add_shares"]
                add_p = step["add_price"]
                new_v = old_v + add_v
                # 加权平均成本（两笔等额 0.5R）：R 基准 = 总股数 × 每股风险（结构止损不变）
                r["volume"] = str(new_v)
                r["entry_price"] = str(round((old_e * old_v + add_p * add_v) / new_v, 4))
                r["phase"] = "confirmed"
                out.append(f"  {code}: ✅ 分步确认，补 0.5R（{add_v}股@{add_p:.2f}），"
                           f"总仓位 1R（{new_v}股）")
                changed += 1
                continue
            # exit_stop / exit_reject → 平仓（0.5R 半仓）
            exit_price = float(step["close"])
            pnl = (exit_price - float(r["entry_price"])) * int(r["volume"])
            fee_in = calc_trade_fee(float(r["entry_price"]) * int(r["volume"]))
            fee_out = calc_trade_fee(exit_price * int(r["volume"]))
            pnl -= fee_in + fee_out
            risk_amt = (float(r["entry_price"]) - float(r["stop_loss"])) * int(r["volume"])
            r_mult = pnl / risk_amt if risk_amt > 0 else 0
            r["status"] = "closed"
            r["exit_price"] = f"{exit_price:.2f}"
            r["exit_date"] = step["exit_date"]
            r["exit_reason"] = step["reason"]
            r["r_multiple"] = f"{r_mult:.2f}"
            r["pnl"] = f"{pnl:.2f}"
            out.append(f"  {code}: 🎯 {step['reason']} R={r_mult:+.2f} 盈亏{pnl:+,.0f}元")
            changed += 1
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
        n_synced = _sync_r_curve(rows)   # 双线自动同步（2026-08-07）
        if n_synced:
            out.append(f"  📈 R 值曲线已自动同步 {n_synced} 笔（虚拟盘线，note=sim）")
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
    [r for r in closed if float(r["r_multiple"]) <= 0]
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
