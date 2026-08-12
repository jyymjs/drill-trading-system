"""实盘执行卡（2026-08-06 老板确认四连包①：1R/0.5R 双路径执行卡）

扫描报告增强板块（实盘前置），四个板块：

1. 当日环境档：上证指数 60 日窗口 environment_quality → 「1R 日 / 0.5R 日」。
   判定单一来源：sim_trading._market_env_scale（指数数据不可得 → None → 1R 日，放行侧）。

2. 挂单指引卡：当日环境档决定新候选挂单路径——
   - 1R 日：新候选按 1R 挂单（风险额 = 当日资金 × 2%，5600 元 → 112 元，按当日资金实算）；
   - 0.5R 日：新候选按 0.5R 试探挂单（风险额半额 = 56 元）+ 次日收线确认流程说明
     （确认 → 补 0.5R 至 1R；不确认 → 平仓——2024-06-29 周会原文，见
     indicators.half_position_confirm 模块注释，知识卡 经验型模式/知识卡.md 仓位与环境节）。
   - 排序（2026-08-06 老板拍板质量优先）：sort_by="risk_mid" 时候选按每股风险
     居中排序（|每股风险-1.5| 升序，T-032 实验定案：同日多候选选谁的标准，
     +88.8% → +107.3%）；"none"= 扫描原序。

3. 分步建仓持仓卡：在持 0.5R 试探仓（sim_journal phase=="half" 的行；兼容
   trade_journal 带 phase 列的 open 行）→ half_position_confirm 三条件判定 →
   动作指令（补 0.5R 挂单价 / 平仓 / 持有等待 / 触止损）。
   判定逻辑与模拟层同源：复用 sim_trading._check_half_position（不复制）。

4. 系统状态行（2026-08-06 老板拍板全自动+熔断式）：连败预警（连续止损 ≥5 笔）、
   在持仓位与板块分散提示、账户数据校验——审核层移除后仅系统级警报介入。

落盘（2026-08-06 全自动执行链）：full_card 默认写 `产出/输出/执行卡_YYYYMMDD.md`
（T-022 同日覆盖），扫描计划任务自动落盘——「扫描输出即挂单依据，不审核」。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from 分析决策.跟踪 import sim_trading
from 分析决策.风控.capital import get_capital, get_risk_ratio
from 分析决策.分析.scanner import structure_broken

_CARD_DIR = Path(__file__).resolve().parent.parent.parent / "产出" / "输出"
_SCAN_DIR = Path(__file__).resolve().parent.parent.parent / "数据基础" / "扫描输出"


def env_day_scale() -> tuple[float, str]:
    """当日环境档（1R 日 / 0.5R 日）

    Returns:
        (scale, 说明)：1.0 = 1R 日（环境 good），0.5 = 0.5R 日（环境 weak/bad）；
        指数数据不可得 → (1.0, "指数数据不可得→默认1R日")（放行侧，不因数据问题误伤挂单）
    """
    s = sim_trading._market_env_scale()
    if s is None:
        return 1.0, "指数数据不可得→默认1R日"
    return s, ("环境好(非右下角)→1R日" if s == 1.0 else "环境弱(右下角)→0.5R日")


def _env_capital_ctx(capital: float | None = None,
                     risk_ratio: float | None = None) -> dict:
    """环境档/资金/风险额共享上下文（R-076 抽法：order_card 与新版式同源，
    防 R-074 式口径漂移）。"""
    scale, note = env_day_scale()
    cap = capital if capital is not None else get_capital()
    rr = risk_ratio if risk_ratio is not None else get_risk_ratio()
    return {"scale": scale, "note": note, "cap": cap, "risk_ratio": rr,
            "risk_amt": round(cap * rr * scale, 2)}


def order_card(candidates: list[dict], capital: float | None = None,
               risk_ratio: float | None = None, sort_by: str = "risk_mid") -> str:
    """挂单指引卡：当日环境档 → 新候选 1R/0.5R 挂单指引

    Args:
        candidates: prebreak 候选（需含 code/name/触发价/止损价/每股风险/评级，
            与 scanner prebreak 输出结构一致）
        capital: 当日资金（缺省读取 capital.json）
        risk_ratio: 单笔风险比例（缺省 None = get_risk_ratio() 资金阶梯动态值，
            V4 定案 2026-08-12：风险额 0.025×当前资金，无限制上限（≥2 万降档未采纳）
        sort_by: 候选排序（2026-08-06 老板拍板质量优先，T-032 定案）：
            "risk_mid"=每股风险居中优先（|每股风险-1.5| 升序，默认）；
            "none"=扫描原序

    Returns:
        指引卡文本（含「1R 日/0.5R 日」标注与逐票挂单指引；无候选 → 相应说明）
    """
    _ctx = _env_capital_ctx(capital, risk_ratio)
    scale, note = _ctx["scale"], _ctx["note"]
    cap, risk_ratio, risk_amt = _ctx["cap"], _ctx["risk_ratio"], _ctx["risk_amt"]
    today = datetime.now().strftime("%Y-%m-%d")
    W = 76
    line = "-" * W
    label = "1R 日" if scale == 1.0 else "0.5R 日"
    if sort_by == "risk_mid":
        candidates = sorted(candidates,
                            key=lambda r: abs((r.get("每股风险", 0) or 0) - 1.5))
    out = [line, f"〔挂单指引卡〕{today} 当日环境档 = {label}（{note}）".center(W), line]
    # R-046 配置行（2026-08-11 老板拍板：风险额 = 0.025×资金，上限无限制）
    out.append(f"  💰 当前配置：资金 {cap:.0f} 元 | 单笔风险额 {risk_amt:.0f} 元"
               f"（{cap:.0f}×{risk_ratio:.4%}{'×0.5' if scale != 1.0 else ''}）"
               f" | 持仓上限：无限制（有 S 级候选就买）")
    # R-051 挂单资金占用校验（2026-08-11 老板拍板采纳；R-074 复核修订 2026-08-12）：
    # 已持占用 + 待补仓（仅确认通过）+ 触发占用（云单挂单中 + 当日有效新候选）
    # vs 余额——防多笔试探仓触发后资金链断裂。修订三点（交易部复核 P1-1）：
    #   ① 待补仓只算"确认通过可补"（平仓中/未确认不占）
    #   ② 新候选排除破位票（600285 类现价≤止损不可挂，不占）
    #   ③ 补上已挂单触发占用（300453 1952——漏计会低估资金需求）
    _held = _open_hold_cost()
    _pend = _open_pending_add()
    # 新候选触发占用：有效候选（触发/止损/风险 >0 且结构未破——R-073 破位原语）
    _trig_new = sum(float(r.get("触发价", 0) or 0) * 100 for r in candidates
                    if (r.get("触发价", 0) or 0) > 0 and (r.get("止损价", 0) or 0) > 0
                    and (r.get("每股风险", 0) or 0) > 0
                    and not structure_broken(float(r.get("price", 0) or r.get("现价", 0) or 0),
                                             float(r.get("止损价", 0) or 0),
                                             float(r.get("TY低", 0) or 0) or None)["broken"])
    _trig_orders = _pending_orders_cost()   # 云单「挂单中」触发占用
    _trig = _trig_new + _trig_orders
    _total = _held + _pend + _trig
    _left = cap - _total
    _flag = " ⚠️ 超支风险" if _left < 0 else ""
    _cash = cap - _held   # 真实现金估算（R-051 补仓判定用：预算 - 已投入）
    out.append(f"  💰 资金占用校验：已持 {_held:.0f} + 待补仓 {_pend:.0f}"
               f" + 触发占用 {_trig:.0f}（新候选 {_trig_new:.0f} + 已挂单 {_trig_orders:.0f}）"
               f" = {_total:.0f} 元 / 资金 {cap:.0f} 元（剩余 {_left:.0f} 元）{_flag}")
    out.append(f"  💵 可用现金约 {_cash:.0f} 元（预算 {cap:.0f} - 已投入 {_held:.0f}）"
               f"——补仓判定以此为准（R-051 修订口径）")
    out.append(f"  新候选挂单路径：{'1R 正常挂单' if scale == 1.0 else '0.5R 试探挂单'}")
    if scale != 1.0:
        out.append("  流程（0.5R 试探 → 次日收线确认，2024-06-29 周会原文）：")
        out.append("    次日收盘 ①≥进场价（收下去）②≥开仓日收盘（动能延续）"
                   "③非放量阴线（量比≤1.5 或收阳）")
        out.append("    → 三条件全满足：补 0.5R 至总 1R（等额挂单）；任一不满足：平仓"
                   "（优势不突出，动能无法接受）")
        out.append("    止损优先：次日最低 ≤ 止损价 → 层面1 止损出场（先于确认判定）")
    out.append(line)
    if not candidates:
        out.append("  今日无新候选（挂单指引无内容）")
        out.append(line)
        return "\n".join(out)
    # 池校验集（2026-08-07 修复 600001 污染）：循环外取一次，避免每票重复拉取
    try:
        from 数据基础.配置.stock_pool import get_stock_codes
        known_codes = set(get_stock_codes())
    except (ImportError, TypeError):
        known_codes = None  # 校验不可用 → 信任上游参数校验
    for r in candidates:
        code = r.get("code", "?")
        name = r.get("name", "")
        trigger = r.get("触发价", 0) or 0
        stop = r.get("止损价", 0) or 0
        risk_ps = r.get("每股风险", 0) or 0
        grade = r.get("评级", "?")
        # 候选有效性校验（2026-08-07 修复 600001 污染）：参数无效或池外票
        # （触发/止损/每股风险必须 >0 且 code 在股票池）→ 标注跳过，不参与挂单。
        # 背景：08-07 执行卡曾出现 600001（池外票、触发 10.00/止损 0.00，
        # 与测试数据同形）——执行卡落盘污染，挂单指引不可信。
        valid = trigger > 0 and stop > 0 and risk_ps > 0
        if valid and known_codes is not None:
            valid = code in known_codes
        if not valid:
            out.append(f"  ⚠️ [{grade}] {code} {name} | 参数无效/池外票——已剔除"
                       "（不参与挂单；请核对数据源）")
            continue
        shares, reason = sim_trading.check_affordability(trigger, risk_ps,
                                                         risk_scale=scale)
        if shares < 100:
            note_s = f"不可买（{reason}）"
        else:
            note_s = f"挂单 {shares} 股（风险 {risk_ps * shares:.0f} 元 ≤ {risk_amt:.0f} 元）"
        out.append(f"  [{grade}] {code} {name} | 触发 {trigger:.2f} | 止损 {stop:.2f}"
                   f" | 每股风险 {risk_ps:.2f} | {label}: {note_s}")
        # R-053 量能状态标注（2026-08-11 老板拍板；R-070 阈值 1.5→1.2）：挂单时可见——
        # 当前量比 <1.2 表示量能不足（T-020 起步线 1.2）；突破日需量比>1.5 才确认
        # （dn_confirm 口径保持 1.5——双阈值区分）；仅参考，最终以触发日收盘后确认判定
        cur_ratio = r.get("当前量比", 0) or 0
        vol_th = r.get("放量阈值", 0) or 0
        if cur_ratio > 0:
            flag = "✅" if cur_ratio > 1.2 else "⚠️"
            out.append(f"      📋 量能状态：当前量比 {cur_ratio} {flag}"
                       f"（放量阈值 {vol_th:.0f} 手 = 前20日均量×1.2；突破日量比>1.5 才确认）")
        if shares >= 100:
            # 云条件单录入参数（2026-08-08 老板提供券商可用单型：股价条件-突破/回落）
            # 买入 =「股价条件-突破」（≥触发价买入）；止损 =「股价条件-回落」（≤止损价卖出）
            # 有效期与模拟线条件单同语义（SIM_PENDING_EXPIRE_DAYS=5 交易日，未触发重挂）
            out.append(f"      📋 云条件单（到价自动执行，直接录入券商）：")
            out.append(f"         买入「股价条件-突破」：价格 ≥ {trigger:.2f} → 买 {shares} 股")
            out.append(f"         止损「股价条件-回落」：价格 ≤ {stop:.2f} → 卖出全部")
            out.append(f"         有效期建议：3 个交易日（未触发请重挂）")
    if scale != 1.0:
        out.append(f"  ※ {label}：挂单量按 0.5R 半额风险预算（{risk_amt:.0f} 元）计算；"
                   "次日确认后补仓等额。")
    out.append(line)
    return "\n".join(out)


def _open_hold_cost() -> float:
    """实盘已持 open 持仓的资金占用（Σ 进场价×股数，只读 trade_journal）

    R-051 挂单资金占用校验用（2026-08-11 老板拍板采纳）。
    只读实盘账本——模拟盘 10 万名义资金与实盘 8,401 无关，不得混入校验。
    """
    from 分析决策.跟踪.trade_journal import get_all_trades as _get_live
    rows = _get_live()
    return sum(float(r.get("entry_price", 0) or 0) * int(r.get("volume", 0) or 0)
               for r in rows if r.get("status") == "open")


def _open_pending_add() -> float:
    """实盘待补仓资金（仅"确认通过可补"的 half 等额款，Σ 补仓价×补仓股数）

    R-051 挂单资金占用校验用。与 position_card 同源判定
    （sim_trading._check_half_position，含资金可买性检查）：
    平仓中（exit_reject/exit_stop）与未确认（wait/hold）不占用补仓款——
    R-074 复核修正（2026-08-12）：曾把全部 half 进场额当待补款
    （603970 平仓中仍占 1058、600315 未确认占 1833 → 误报超支）。
    """
    from 分析决策.跟踪.trade_journal import get_all_trades as _get_live
    from 数据基础.数据.fetcher import get_daily_kline
    rows = [r for r in _get_live()
            if r.get("status") == "open" and r.get("phase") == "half"]
    total = 0.0
    for r in rows:
        try:
            df = get_daily_kline(str(r.get("symbol", "")), use_cache=True)
            step = sim_trading._check_half_position(df, r, capital=None)
            if step["action"] == "add":
                total += float(step.get("add_price", 0) or 0) * int(step.get("add_shares", 0) or 0)
        except Exception:  # noqa: BLE001 - 单仓数据异常不阻断整体校验
            continue
    return total


def _scan_map(scan_dir: str | Path | None = None) -> tuple[dict | None, str]:
    """最新扫描批次 S 级映射（R-076 抽法：cloud_order_reminder / cloud_orders_status 共用）

    Returns:
        (scan_map, batch)：code → {trigger/stop/price/ty_high/ty_low/vol}；
        无批次 → (None, "")
    """
    import csv
    sdir = (Path(scan_dir) if scan_dir
            else Path(__file__).resolve().parent.parent.parent / "数据基础" / "扫描输出")
    scan_map: dict[str, dict] | None = {}
    batch = ""
    try:
        files, batch = _latest_scan_files(sdir)
        if not files:
            scan_map = None
        else:
            for f in files:
                with open(f, encoding="utf-8-sig") as fh:
                    for rec in csv.DictReader(fh):
                        code = str(rec.get("code", "")).strip()
                        if code in scan_map:
                            continue
                        trig = rec.get("触发价", "")
                        stop = rec.get("止损价", "")
                        if code and trig:
                            # R-072：扩列 price/TY高/TY低——现价维度（000429 教训）
                            scan_map[code] = {"trigger": float(trig) or 0.0,
                                              "stop": float(stop) or 0.0,
                                              "price": float(rec.get("price", 0) or 0),
                                              "ty_high": float(rec.get("TY高", 0) or 0),
                                              "ty_low": float(rec.get("TY低", 0) or 0),
                                              # R-074 复核 P1-2：量比口径（缩量不撤单）
                                              "vol": float(rec.get("当前量比", 0) or 0)}
    except (OSError, ValueError):
        scan_map = None
    return scan_map, batch


def _cloud_orders(track_file: str | Path | None = None) -> list[dict]:
    """云单表结构化解析（R-076：表头列名定位 + 缺列容错，兼容新旧格式）

    返回每行：code/name/date/trigger/stop/vol/status/sell(卖出条件单)/expire(到期日)/
    note(备注)。状态列前的 6 列与旧正则一致（正则消费方不受影响）。
    """
    f = (Path(track_file) if track_file
         else Path(__file__).resolve().parent.parent / "交易日志" / "云条件单跟踪.md")
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8").splitlines()
    hdr = next((i for i, ln in enumerate(lines)
                if ln.startswith("|") and "买入触发价" in ln), None)
    if hdr is None:
        return []
    cols = [c.strip() for c in lines[hdr].strip("|").split("|")]

    def ci(name: str) -> int | None:
        return cols.index(name) if name in cols else None

    i_sell, i_expire, i_status, i_note = ci("卖出条件单"), ci("到期日"), ci("状态"), ci("备注")
    orders = []
    for ln in lines[hdr + 2:]:
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if not cells or len(cells[0]) < 6 or not cells[0][:6].isdigit():
            continue

        def get(ix: int | None) -> str:
            return cells[ix].strip() if ix is not None and ix < len(cells) else ""

        orders.append({
            "code": cells[0][:6], "name": cells[0][6:].strip(),
            "date": cells[1] if len(cells) > 1 else "",
            "trigger": cells[2].replace("≥", "").replace("**", "").strip() if len(cells) > 2 else "",
            "stop": cells[3].replace("≤", "").replace("**", "").strip() if len(cells) > 3 else "",
            "vol": cells[4] if len(cells) > 4 else "",
            "status": get(i_status).replace("**", ""),
            "sell": get(i_sell).replace("**", ""), "expire": get(i_expire),
            "note": get(i_note),
        })
    return orders


def _pending_orders_cost() -> float:
    """实盘已挂「挂单中」买入单触发占用（Σ 触发价×股数，读云单跟踪表）

    R-074 复核修正（2026-08-12）：占用校验漏计已挂单（300453 1952 元）——
    触发占用 = 云单挂单中 + 当日有效新候选，两者都要算。
    R-076：改用 _cloud_orders 结构化解析（兼容旧格式正则）
    """
    total = 0.0
    for o in _cloud_orders():
        if "挂单中" in o["status"] and o["trigger"]:
            try:
                total += float(o["trigger"]) * int(o["vol"] or 0)
            except ValueError:
                continue
    return total


def _latest_scan_files(scan_dir: Path) -> tuple[list[Path], str]:
    """最新扫描批次的全部文件（主文件 + 变体）——R-067 校准/全览共用。

    返回 (files, batch)：files 按 主文件 → _broken → _c23 → _vol → _grade 顺序
    （S 级全览原因标注的优先级）；batch = 批次时间戳（如 20260811_182407）。
    """
    import re
    all_files = sorted((p for p in scan_dir.glob("scan_result_*.csv")
                        if re.match(r"scan_result_\d{8}_\d{6}", p.stem)),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not all_files:
        return [], ""
    # 取最新时间戳批次（stem 前 15 字符 scan_result_YYYYMMDD_HHMMSS）
    batch = all_files[0].stem.replace("scan_result_", "")[:15]
    same_batch = [p for p in all_files
                  if p.stem.replace("scan_result_", "")[:15] == batch]
    order = {"": 0, "_broken": 1, "_c23": 2, "_vol": 3, "_grade": 4}
    same_batch.sort(key=lambda p: order.get(
        p.stem.replace("scan_result_", "")[15:], 9))
    return same_batch, batch


def scan_s_overview(scan_dir: str | Path | None = None) -> str:
    """扫描 S 级全览（R-067 2026-08-12 老板拍板：当天全部 S 级列出 + 买/不买原因，
    不静默过滤——000429 曾被误判"不在候选"，实为放量不达标仍在 S 级池）

    读取最新批次全部文件（主文件 + _broken/_c23/_vol/_grade 变体）→ 汇总 S 级
    （按 code 去重，原因取优先级最高者：主文件合格 → 已突破 → C23 → 放量）→
    每只标注买/不买 + 原因。

    Returns:
        板块文本（无 S 级 → 一行说明）
    """
    import csv
    W = 76
    line = "-" * W
    today = datetime.now().strftime("%Y-%m-%d")
    sdir = (Path(scan_dir) if scan_dir
            else Path(__file__).resolve().parent.parent.parent / "数据基础" / "扫描输出")
    files, batch = _latest_scan_files(sdir)
    out = [line, f"〔扫描 S 级全览〕{today}（批次 {batch or '无'}）".center(W), line]
    if not files:
        out.append("  未找到扫描结果（数据基础/扫描输出），无法列出 S 级")
        out.append(line)
        return "\n".join(out)
    # 按文件优先级聚合 S 级（code → {触发, 止损, 原因, 名称}）
    s_level: dict[str, dict] = {}
    tag_map = {"": "✅ 合格候选（可挂单）",
               "_broken": "❌ 已突破（现价≥触发价，追高不买）",
               "_c23": "❌ C23 不达标",
               "_vol": "❌ 放量不达标（量比≤1.2，不新挂单（已挂单除外）；突破日确认量能）"}
    for f in files:
        suffix = f.stem.replace("scan_result_", "")[15:]
        try:
            with open(f, encoding="utf-8-sig") as fh:
                for rec in csv.DictReader(fh):
                    code = str(rec.get("code", "")).strip()
                    if not code or rec.get("评级", "").strip() != "S":
                        continue
                    if code in s_level:
                        continue   # 优先级已定（更高优先级文件先读）
                    trig = rec.get("触发价", "")
                    stop = rec.get("止损价", "")
                    reason = tag_map.get(suffix, "")
                    if suffix == "_c23":
                        reason += f"（{rec.get('C23原因', '') or '不达标'}）"
                    s_level[code] = {"trigger": trig, "stop": stop,
                                     "reason": reason, "name": rec.get("name", ""),
                                     "price": rec.get("price", ""),
                                     "ty_low": rec.get("TY低", "")}
        except (OSError, ValueError):
            continue
    if not s_level:
        out.append("  当日无 S 级候选（全部不达标或未扫描到）")
        out.append(line)
        return "\n".join(out)
    for code, info in s_level.items():
        # R-073 补漏（2026-08-12）：合格候选（主文件）加破位检查——现价 ≤ 止损
        # 结构已坏（600285 案例：大跌 -9.2% 现价 20.47 破止损 21.90 仍标"可挂单"）
        if not info["reason"].startswith("✅"):
            out.append(f"  {info['reason']}  {code} {info['name']} | "
                       f"触发 {float(info['trigger']):.2f} | 止损 {float(info['stop']):.2f}"
                       if info['trigger'] else f"  {info['reason']}  {code} {info['name']}")
            continue
        _sb = structure_broken(float(info["price"] or 0), float(info["stop"] or 0),
                               float(info["ty_low"] or 0) or None)
        if _sb["broken"]:
            out.append(f"  🔴 已破位（现价≤止损/平台下沿，不可挂单）  {code} {info['name']} | "
                       f"触发 {float(info['trigger']):.2f} | 止损 {float(info['stop']):.2f} | {_sb['reason']}")
        else:
            out.append(f"  {info['reason']}  {code} {info['name']} | "
                       f"触发 {float(info['trigger']):.2f} | 止损 {float(info['stop']):.2f}")
    out.append(line)
    return "\n".join(out)


def cloud_order_reminder(track_file: str | Path | None = None,
                         scan_dir: str | Path | None = None) -> str:
    """云条件单持续埋伏 + 每日校准（R-065 2026-08-12 老板拍板，替代 3 日到期提醒）

    读 云条件单跟踪.md 的「挂单中」行 → 与最新扫描结果（主 scan_result）
    对比触发价/止损 → 三态：
      ✅ 触发价一致     → 持续埋伏中（无需操作）
      ⚠️ 触发价过时     → **建议撤单**，按最新口径重挂（显示最新触发价/止损）
      ⚠️ 不在最新候选   → **建议撤单**（S 级消失，不再埋伏）
    老板拍板（R-065）：任何需要操作的状态必须显式提示"撤单"。
    已成交行的止损单（保护仓）不提醒——长期有效；只有「挂单中」的买入单提醒。

    Args:
        track_file: 云单跟踪表路径（测试注入；缺省 = 交易日志/云条件单跟踪.md）
        scan_dir: 扫描输出目录（测试注入；缺省 = 数据基础/扫描输出）

    Returns:
        提醒板块文本（无挂单中单 → 一行说明）
    """
    import re
    import csv
    W = 76
    line = "-" * W
    today = datetime.now().strftime("%Y-%m-%d")
    f = (Path(track_file) if track_file
         else Path(__file__).resolve().parent.parent / "交易日志" / "云条件单跟踪.md")
    sdir = (Path(scan_dir) if scan_dir
            else Path(__file__).resolve().parent.parent.parent / "数据基础" / "扫描输出")
    out = [line, f"〔云条件单持续埋伏〕{today}".center(W), line]
    pending = []
    if f.exists():
        for ln in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\| (\d{6}) (.+?) \| (\d{4}-\d{2}-\d{2}) \| ≥ ([\d.]+) \| ≤ ([\d.]+) \| (\d+) \| (.+?) \|", ln)
            if m:
                code, name, gd, trig, stop, vol, status = m.groups()
                if "挂单中" in status:
                    pending.append({"code": code, "name": name, "date": gd,
                                    "trigger": float(trig), "stop": float(stop),
                                    "vol": int(vol)})
    if not pending:
        out.append("  无挂单中的买入条件单（持续埋伏池为空）")
        out.append(line)
        return "\n".join(out)
    # 校准基准 = 最新批次全部文件并集（主文件 + _broken/_c23/_vol 变体）——
    # R-067（2026-08-12）：000429 在主文件缺失/被放量过滤时仍在变体 S 级池
    # （触发价一致 → 应"已校准持续埋伏"而非误判"不在候选"）；批次过时仍告警
    scan_map: dict[str, dict] | None = {}
    scan_batch = ""
    try:
        files, scan_batch = _latest_scan_files(sdir)
        if not files:
            scan_map = None
        else:
            for f in files:
                with open(f, encoding="utf-8-sig") as fh:
                    for rec in csv.DictReader(fh):
                        code = str(rec.get("code", "")).strip()
                        if code in scan_map:
                            continue
                        trig = rec.get("触发价", "")
                        stop = rec.get("止损价", "")
                        if code and trig:
                            # R-072：扩列 price/TY高/TY低——现价维度（000429 教训：
                            # 只比对触发价导致破止损挂单误报"持续埋伏"）
                            scan_map[code] = {"trigger": float(trig) or 0.0,
                                              "stop": float(stop) or 0.0,
                                              "price": float(rec.get("price", 0) or 0),
                                              "ty_high": float(rec.get("TY高", 0) or 0),
                                              "ty_low": float(rec.get("TY低", 0) or 0),
                                              # R-074 复核 P1-2：量比口径——挂单依据信号日
                                              # 放量，今日缩量不撤单（突破日才确认量能）
                                              "vol": float(rec.get("当前量比", 0) or 0)}
    except (OSError, ValueError):
        scan_map = None
    if scan_map is not None:
        # R-065 防再犯（08-12 实测 08-11 主文件缺失静默读 08-10 旧批次）：
        # 批次日期 < 今日 且 已过 19:00（扫描 18:00 后应已产出）→ 过时告警；
        # 19:00 前（扫描未到点/周末）仅提示批次，不算异常
        from datetime import datetime as _dt
        _batch_date = scan_batch[:8] if len(scan_batch) >= 8 else ""
        _now = _dt.now()
        _stale = bool(_batch_date and _batch_date < today.replace("-", "")
                      and _now.weekday() < 5 and _now.hour >= 19)
        if _stale:
            out.append(f"  ⚠️ **扫描数据过时**：最新批次 {scan_batch}（{_batch_date}）"
                       f"——今日未生成主扫描文件，校准基准不可靠，请检查扫描链路")
        else:
            out.append(f"  ℹ️ 校准基准：扫描批次 {scan_batch}")
    for p in pending:
        if scan_map is None:
            out.append(f"  ⚠️ {p['code']} {p['name']} 买入单 ≥{p['trigger']:.2f}："
                       f"未找到最新扫描结果，无法校准 → 请核对最新执行卡参数后决定撤单/重挂")
            continue
        s = scan_map.get(p["code"])
        if s is None:
            out.append(f"  ⚠️ {p['code']} {p['name']} 买入单 ≥{p['trigger']:.2f}："
                       f"已不在最新扫描 S 级候选（结构变化/参数失效）→ "
                       f"**建议撤单，不再埋伏**")
            continue
        # R-072 四态升级：① 破止损/TY低 → 🔴 挂单失效（000429 教训根因）
        sb = structure_broken(s["price"], p["stop"], s.get("ty_low"))
        if sb["broken"]:
            out.append(f"  🔴 {p['code']} {p['name']} 买入单 ≥{p['trigger']:.2f}："
                       f"**挂单失效——{sb['reason']} → **建议撤单，不再埋伏**")
            continue
        # ② 触发价/止损一致性比对（止损也一并比对，审核口径 abs<0.005）
        trig_ok = abs(s["trigger"] - p["trigger"]) < 0.005
        stop_ok = abs(s["stop"] - p["stop"]) < 0.005
        if not (trig_ok and stop_ok):
            out.append(f"  ⚠️ {p['code']} {p['name']} 买入单 ≥{p['trigger']:.2f}/≤{p['stop']:.2f}："
                       f"**参数过时**——最新口径 ≥{s['trigger']:.2f}（止损 {s['stop']:.2f}）"
                       f"→ **建议撤单，按最新口径重挂**")
            continue
        # ③ 有效：贴价/贴止损提示（R-072：<0.5% 贴价优质线 dist_asc；<1% 贴止损警示）
        tips = []
        if s["trigger"] > 0 and s["price"] > 0:
            td = (s["trigger"] - s["price"]) / s["price"] * 100.0
            if td < 0.5:
                tips.append(f"🟢 贴价候选（距触发 {td:.1f}%）")
        # R-074 复核 P1-2：今日缩量（量比≤1.2）不撤单——挂单依据信号日放量，
        # 与 S 级全览"放量不达标"口径统一说明，防误撤单（300453 案例）
        if s.get("vol", 0) > 0 and s["vol"] <= 1.2:
            tips.append(f"⚠️ 今日量比 {s['vol']:.2f} 缩量（挂单依据信号日放量——"
                        f"突破日才确认量能，**不撤单**）")
        buf = sb["buffer_pct"]
        if buf is not None and buf < 1.0:
            tips.append(f"🟡 贴止损（缓冲 {buf:.1f}%）")
        tip_s = "；".join(tips)
        out.append(f"  ✅ {p['code']} {p['name']} 买入单 ≥{p['trigger']:.2f}："
                   f"参数已校准，持续埋伏中（{p['vol']} 股 0.5R 试探，无需操作"
                   + (f"；{tip_s}" if tip_s else "") + "）")
    out.append(line)
    return "\n".join(out)


def _iter_half_positions(rows: list[dict] | None = None) -> list[dict]:
    """在持 0.5R 试探仓（phase=="half" 且 status=="open"）

    数据源：sim_journal（模拟层，G3 定案后由 sim_open 写入 phase 列）+
    trade_journal（实盘层，2026-08-10 补漏：注释声称的"兼容 trade_journal
    带 phase 列的行"此前未实现——实盘 0.5R 仓进不了分步确认卡，本次补上；
    trade_journal 已扩为同构 21 列，判定逻辑一致）。

    每行附 `_source` 来源标记（"live"/"sim"，2026-08-11 新增）：
    position_card 据此选择补仓资金口径——实盘行走 capital.json
    （8401×0.025），模拟行走 SIM_CAPITAL（10 万名义）；此前实盘行补仓量
    错用模拟线口径（600833 100 股实盘仓提示补 2300 股，已修）。
    """
    if rows is None:
        rows = sim_trading._read_all()
        from 分析决策.跟踪.trade_journal import get_all_trades as _get_live
        rows = rows + _get_live()
    out = []
    for r in rows:
        if r.get("phase") == "half" and r.get("status") == "open":
            r = dict(r)
            r["_source"] = "live" if str(r.get("trade_id", "")).startswith("LIVE") else "sim"
            out.append(r)
    return out


def position_card(rows: list[dict] | None = None) -> str:
    """分步建仓持仓卡：在持 0.5R 试探仓 → 动作指令（补 0.5R 挂单价 / 平仓 / 等待）

    判定逻辑单一来源：sim_trading._check_half_position（内部复用
    indicators.half_position_confirm 三条件 + 止损层面1 优先；此处不复制）。

    Args:
        rows: 持仓行（缺省读取 sim_journal）；测试可注入

    Returns:
        持仓卡文本（无在持 0.5R 仓 → 一行说明）
    """
    halves = _iter_half_positions(rows)
    W = 76
    line = "-" * W
    today = datetime.now().strftime("%Y-%m-%d")
    out = [line, f"〔分步建仓持仓卡〕{today} 在持 0.5R 试探仓 {len(halves)} 笔".center(W), line]
    if not halves:
        out.append("  无在持 0.5R 试探仓（今日无分步确认动作）")
        out.append(line)
        return "\n".join(out)

    from 数据基础.数据.fetcher import get_daily_kline

    for r in halves:
        code = r.get("symbol", "?")
        name = r.get("name", "") or ""
        try:
            df = get_daily_kline(code, use_cache=True)
        except Exception:  # noqa: BLE001 - 数据获取失败 → 卡片标注待数据
            out.append(f"  {code} {name}: 数据获取失败，稍后重试")
            continue
        if df is None or len(df) < 2:
            out.append(f"  {code} {name}: 数据不足，无法判定（明日重试）")
            continue
        # 补仓资金口径（2026-08-11）：实盘行 → capital.json（None）；模拟行 → SIM_CAPITAL
        capital = None if r.get("_source") == "live" else sim_trading.SIM_CAPITAL
        step = sim_trading._check_half_position(df, r, capital=capital)
        act = step["action"]
        entry = float(r["entry_price"])
        stop = float(r["stop_loss"])
        held = int(r.get("volume", 0))
        if act == "add":
            # R-051 规则（2026-08-11 老板拍板采纳）：确认补仓时余额不足 → 维持 0.5R 不补
            # R-074 修订（2026-08-12 交易部复核 P1-1）：add = 可买性检查已过（资金够），
            # 不再固定打印"余额不足"误导（600833 曾因固定文本误判不补）
            add_cost = float(step.get("add_price", 0) or 0) * int(step.get("add_shares", 0) or 0)
            out.append(f"  ✅ {code} {name}: 收线确认（{step['reason']}）→ "
                       f"补 0.5R 挂单 {step['add_shares']} 股 @ {step['add_price']:.2f}"
                       f"（等额，总 {held + step['add_shares']} 股 = 1R，约需 {add_cost:.0f} 元）")
            out.append(f"      📋 R-051 补仓资金：可买性检查通过（约需 {add_cost:.0f} 元）✅ 执行补仓")
        elif act == "exit_stop":
            out.append(f"  🛑 {code} {name}: 确认日触止损（{step['reason']}）→ 按止损 {stop:.2f} 平仓")
        elif act == "exit_reject":
            out.append(f"  ❌ {code} {name}: 收线未确认（{step['reason']}）→ "
                       f"按确认日收盘 {step['close']:.2f} 平仓（0.5R 试探止步）")
        elif act == "hold":
            out.append(f"  ⏸ {code} {name}: {step['reason']}（保持 0.5R {held} 股，明日再看）")
        else:  # wait
            out.append(f"  ⏳ {code} {name}: {step['reason']}（进场 {entry:.2f} / "
                       f"止损 {stop:.2f}），持有 0.5R 等待确认")
        # 止盈云条件单（2026-08-08 升级：对应券商「回落卖出」单型——上涨中回落卖出，
        # G5 TTP 回落 36% 自动化；目标价 = 进场 + 5R（G7 三区间 5R 界））
        risk_ps = entry - stop
        if risk_ps > 0 and act in ("add", "hold", "wait"):
            ttp_target = entry + 5.0 * risk_ps
            out.append(f"      📋 止盈云条件单「回落卖出」：价格达 {ttp_target:.2f} 后"
                       f"回落 36% → 卖出（5R 目标，G7 界）")
        # R-053 突破质量判定显示（2026-08-11 老板拍板：开仓日收盘站稳 + 放量双条件；
        # 数据来自 _check_half_position 返回值，此处只展示不复制——单一来源约定）
        if step.get("open_close_ok") is not None:
            ab = (f"      📋 突破质量：收盘站稳{'✅' if step['open_close_ok'] else '❌'}"
                  f"（开仓日收 ≥ 触发价）| 放量 {step.get('vol_ratio')} "
                  f"{'✅' if step.get('vol_ok') else '❌'}（量比>1.5 达标）")
            out.append(ab)
    out.append(line)
    return "\n".join(out)


def _position_status(r: dict) -> dict | None:
    """单仓动态止损状态计算（R-076 共享 helper——protect_card 渲染与
    positions_overview 聚合同源，防 600833 型口径打架；protect_card 逐字保真）。

    Returns:
        dict（code/name/live/entry/stop/v/r_now/df_pos/len_df_pos/head 等）；
        数据异常 → None（调用方跳过该仓）
    """
    from 分析决策.风控.exit_manager import Position, evaluate_exit
    from 数据基础.数据.fetcher import get_daily_kline

    code = r.get("symbol", "?")
    name = r.get("name", "") or ""
    live = str(r.get("trade_id", "")).startswith("LIVE")
    try:
        df = get_daily_kline(code, use_cache=True)
    except Exception:  # noqa: BLE001 - 数据失败不阻断其他持仓
        return None
    if df is None or len(df) < 2:
        return None
    entry = float(r["entry_price"])
    # R-068 修复：trail_stop 空值经 pandas 读成 NaN（NaN 为 truthy → or 失效）
    _ts = r.get("trail_stop")
    stop = float(r["stop_loss"] if (_ts is None or _ts == ""
                                    or (isinstance(_ts, float) and _ts != _ts))
                 else _ts)
    # R-054 审核 P0-3：df 从进场日切片（防进场前 K 线污染拐点判定）
    entry_idx = next((i for i, d in enumerate(
        df["日期"].astype(str).str[:10].values) if d == r["date"]), None)
    df_pos = df.iloc[entry_idx:] if entry_idx is not None else df
    pos = Position(symbol=code, direction="long", market="stock",
                   entry_price=entry, initial_stop=float(r["stop_loss"]),
                   current_stop=stop, volume=int(r.get("volume", 0)),
                   ty_high=float(r.get("ty_high") or 0),
                   ty_low=float(r.get("ty_low") or 0),
                   grade_at_entry=r.get("grade_at_entry", ""))
    # V4 审核 P0-2：注入持有期极值（journal highest/lowest 持久化列；
    # 旧行缺列 → 以进场价初始化——层面3/4 需正确极值才不失真）
    pos.highest_price = float(r.get("highest") or entry)
    pos.lowest_price = float(r.get("lowest") or entry)
    # V4 审核 P1-6 修复：传 df_pos（此前算而未用）
    # R-060（2026-08-12 老板拍板）：主动出场用全量 df——短持仓不再静默失效
    v = evaluate_exit(pos, df_pos, active_df=df)
    latest = df.iloc[-1]
    r_now = pos.current_r_multiple(float(latest["收盘"]))
    src = "实盘" if live else "模拟"
    head = f"  {code} {name}（{src} {r.get('volume')} 股 @{entry:.2f}）| R={r_now:+.2f}"
    return {"code": code, "name": name, "live": live, "entry": entry, "stop": stop,
            "v": v, "r_now": r_now, "df_pos": df_pos, "len_df_pos": len(df_pos),
            "head": head, "latest_close": float(latest["收盘"])}


def protect_card(rows: list[dict] | None = None) -> str:
    """持仓保护卡（R-054 2026-08-11 老板拍板）：全部 open 持仓的动态止损状态
    ——现止损 → 建议止损（1R 平保/移动获利/TTP 依据，exit_manager.evaluate_exit
    唯一来源不复制）+ 当前 R。老板每日照卡改券商云单止损价（实盘只能手动）。

    Args:
        rows: 持仓行（缺省读 sim_journal + trade_journal 全部 open）
    """
    if rows is None:
        rows = sim_trading._read_all()
        from 分析决策.跟踪.trade_journal import get_all_trades as _get_live
        rows = rows + _get_live()
    opens = [r for r in rows if r.get("status") == "open"]
    W = 76
    line = "-" * W
    today = datetime.now().strftime("%Y-%m-%d")
    out = [line, f"〔持仓保护卡〕{today} 动态止损（R-054 老板拍板）".center(W), line]
    if not opens:
        out.append("  无在持仓（动态止损不适用）")
        out.append(line)
        return "\n".join(out)
    for r in opens:
        st = _position_status(r)
        if st is None:
            continue
        code, name, live, entry, stop, v, r_now = (
            st["code"], st["name"], st["live"], st["entry"], st["stop"],
            st["v"], st["r_now"])
        head = st["head"]
        if v.get("should_exit"):
            # V4 审核 P1-5：主动出场卖出建议（实盘人工执行入口）
            # R-068（2026-08-12 老板拍板）：短持仓（切片 <21 根）的主动出场 =
            # 参考信号——R-062 自动执行口径明确"持仓 ≥21 根才自动触发"（R-063
            # 审计：短持仓触发砍肉 -231pp）——只标注参考，不构成"建议卖出"
            # （600833 教训：曾据保护卡信号误建议开盘卖，实际应继续持有）
            _short_active = (st["len_df_pos"] < 21 and "主动出场" in v.get("reason", ""))
            if _short_active:
                out.append(f"{head}\n      ⚠️ 主动出场**参考信号**（持仓 {st['len_df_pos']} 根 <21——"
                           f"策略自动执行不触发，**继续持有**；止损保护 "
                           f"{v.get('stop_update') or stop:.2f}）")
            else:
                out.append(f"{head}\n      ⚠️ 主动出场信号 → 建议卖出（{v['reason']}）")
        elif v.get("stop_update"):
            out.append(f"{head}\n      止损 {stop:.2f} → 建议 {v['stop_update']:.2f}"
                       f"（{v['reason']}）——照此改云单止损价")
        else:
            out.append(f"{head}\n      止损维持 {stop:.2f}（未达上移条件）")
    out.append(line)
    return "\n".join(out)


def _data_freshness_line() -> str:
    """执行卡时间检查（R-073，2026-08-12 老板拍板：开启执行单先查当前时间）。
    生成时刻 vs 数据最后日期：收盘后（16:00 起，留数据源延迟缓冲）数据必须为当日——08-12 事故：
    06:48 旧数据批次生成当日执行卡，18:05 去重跳过未重扫，收盘后拿着昨日数据决策。
    盘中/盘前数据为最近交易日属正常；收盘后仍非当日 → 显著告警。"""
    now = datetime.now()
    db_date = "查询失败"
    try:
        import duckdb
        con = duckdb.connect(r"数据基础\行情数据\t017_p2.duckdb", read_only=True)
        db_date = str(con.execute("select max(date) from daily").fetchone()[0])
        con.close()
    except Exception:
        pass
    today = now.strftime("%Y-%m-%d")
    if now.hour >= 16 and db_date != today:
        return (f"  🕒 生成 {now.strftime('%H:%M')} | 🔴 **数据截至 {db_date}，"
                f"已过收盘仍非当日 {today}**——旧数据执行卡，请先跑数据更新再决策")
    if now.hour >= 16:
        return f"  🕒 生成 {now.strftime('%H:%M')} | 数据截至 {db_date}（当日 ✅）"
    if db_date != today and db_date != "查询失败":
        return f"  🕒 生成 {now.strftime('%H:%M')} | 数据截至 {db_date}（盘中/盘前，最近交易日属正常）"
    return f"  🕒 生成 {now.strftime('%H:%M')} | 数据截至 {db_date}"


def system_status(rows: list[dict] | None = None) -> str:
    """系统状态行（2026-08-06 老板拍板全自动+熔断式警报——审核层移除后仅系统级介入）

    警报项（触发仅提醒，不停止交易——实盘三纪律：连亏期别慌）：
      ① 连败预警：sim_journal 已平仓连续止损 ≥5 笔（蒙卡最大连败 18-22，5 为提醒线）
      ② 在持仓位 + 板块分散提示（同板块持仓 ≥3 只时人工目检——R-046 无限制上限下板块集中风险更大，G15 人工纪律保留）
      ③ 账户校验：journal 无任何记录 → 提示（数据缺失时执行卡结论不可信）

    Args:
        rows: journal 行（缺省读取 sim_journal）；测试可注入
    """
    rows = rows if rows is not None else sim_trading._read_all()
    out = [_data_freshness_line()]
    closed = [r for r in rows if r.get("status") == "closed"]
    losing = 0
    for r in reversed(closed):
        try:
            rm = float(r.get("r_multiple", 0) or 0)
        except (TypeError, ValueError):
            continue
        if rm < 0:
            losing += 1
        else:
            break
    if losing >= 5:
        out.append(f"  ⚠️ 熔断预警：连续止损 {losing} 笔（提醒线 5）——不停止交易，"
                   "按三纪律：连亏期别慌（大赢家在路上），请老板关注")
    open_rows = [r for r in rows if r.get("status") == "open"]
    if open_rows:
        codes = "、".join(f"{r.get('symbol', '?')}" for r in open_rows)
        out.append(f"  ℹ️ 在持仓位（{len(open_rows)} 仓）：{codes}"
                   "——人工目检板块分散（同板块 ≥2 只请留意）")
    if not rows:
        out.append("  ⚠️ 账户校验：sim_journal 无记录——数据缺失，本卡挂单指引仅供参考，"
                   "核对数据后再执行")
    return "\n".join(out)


def _half_step(rows: list[dict] | None = None) -> list[dict]:
    """0.5R 试探仓判定步骤（R-076 抽法：position_card 与 action_plan 同源）"""
    halves = _iter_half_positions(rows)
    steps = []
    from 数据基础.数据.fetcher import get_daily_kline
    for r in halves:
        code = r.get("symbol", "?")
        try:
            df = get_daily_kline(code, use_cache=True)
        except Exception:  # noqa: BLE001
            continue
        if df is None or len(df) < 2:
            continue
        capital = None if str(r.get("trade_id", "")).startswith("LIVE") else sim_trading.SIM_CAPITAL
        step = sim_trading._check_half_position(df, r, capital=capital)
        # R-076d：name 空兜底（云单表补，仍缺 → 代码）
        _nm = (r.get("name", "") or "").strip()
        if not _nm:
            _nm = next((o["name"] for o in _cloud_orders() if o["code"] == code), "") or code
        steps.append({"code": code, "name": _nm,
                      "row": r, "step": step, "df": df})
    return steps


def action_plan(rows: list[dict] | None = None) -> str:
    """今日行动清单（R-076 决策导航式置顶——老板第一眼看到今天干什么）

    优先级：🔴 必须做（补仓/平仓/止损）→ 🟡 关注（确认日/贴价/临期）→ ℹ️ 信息（埋伏）。
    推导来源：_check_half_position 判定 + 云单表状态 + 到期日（单一来源，不复制）。
    """
    out = []
    # ① 持仓判定 → 动作指令
    for h in _half_step(rows):
        code, name, step = h["code"], h["name"], h["step"]
        act = step["action"]
        row = h["row"]
        sell = next((o["sell"] for o in _cloud_orders() if o["code"] == code), "")
        _sell_txt = f"（卖单 {sell}）" if sell else ""
        if act == "add":
            cost = float(step.get("add_price", 0) or 0) * int(step.get("add_shares", 0) or 0)
            out.append(f"🔴 [补仓] {code} {name}：确认通过，挂 {step['add_shares']} 股 "
                       f"@ {step['add_price']:.2f}（约 {cost:.0f} 元）{_sell_txt}")
        elif act == "exit_reject":
            out.append(f"🔴 [平仓] {code} {name}：确认不通过，按 {step.get('close', 0):.2f} 卖出"
                       f"（0.5R 试探止步）{_sell_txt}")
        elif act == "exit_stop":
            out.append(f"🔴 [止损] {code} {name}：确认日触止损，按 {float(row['stop_loss']):.2f} 出场")
        elif act == "wait":
            out.append(f"🟡 [确认] {code} {name}：{step.get('reason', '等待收线')}（进场 "
                       f"{float(row['entry_price']):.2f} / 止损 {float(row['stop_loss']):.2f}）")
        elif act == "hold":
            out.append(f"🟡 [受限] {code} {name}：{step.get('reason', '补仓受限')}——保持 0.5R")
    # ② 云单挂单中：贴价 / 临期
    try:
        scan_map, _ = _scan_map()
    except Exception:  # noqa: BLE001
        scan_map = None
    for o in _cloud_orders():
        if "挂单中" not in o["status"]:
            continue
        s = scan_map.get(o["code"]) if scan_map else None
        price = float(s["price"]) if s and s.get("price") else 0.0
        trig = float(o["trigger"] or 0)
        if price > 0 and trig > 0:
            td = (trig - price) / price * 100.0
            if td < 0.5:
                out.append(f"🟡 [贴价] {o['code']} {o['name']}：买 ≥{trig:.2f}（现价 {price:.2f}，"
                           f"距触发 {td:.1f}%——随时可能成交）")
        exp = o.get("expire", "")
        if exp and exp != "长期" and len(exp) == 5:
            out.append(f"🟡 [临期] {o['code']} {o['name']}：买入单到期 {exp}——到期前未触发请重挂")
    if not out:
        out.append("ℹ️ 今日无需操作（全部持仓确认完成/挂单有效）")
    return "\n".join(out)


def positions_overview(rows: list[dict] | None = None,
                       candidates: list[dict] | None = None) -> str:
    """持仓一表化 + 资金段（R-076：每股 1 主行——代码/进场/现价/止损/R/状态/下一步
    + 卖单状态；从行（补仓参数/止盈/突破质量）非平凡才印。与 protect_card /
    order_card 同源数据（_position_status / _half_step / _env_capital_ctx）。"""
    if rows is None:
        rows = sim_trading._read_all()
        from 分析决策.跟踪.trade_journal import get_all_trades as _get_live
        rows = rows + _get_live()
    opens = [r for r in rows if r.get("status") == "open"]
    orders = {o["code"]: o for o in _cloud_orders()}
    steps = {h["code"]: h for h in _half_step(rows)}
    # R-076d：journal name 空兜底（云单表 name 补齐，仍缺 → 代码）
    for r in opens:
        if not (r.get("name") or "").strip():
            r["name"] = orders.get(str(r.get("symbol", "")), {}).get("name", "") or r.get("symbol", "")
    _ctx = _env_capital_ctx()
    _held = _open_hold_cost()
    _pend = _open_pending_add()
    _trig_o = _pending_orders_cost()
    _trig_n = sum(float(c.get("触发价", 0) or 0) * 100 for c in (candidates or [])
                  if (c.get("触发价", 0) or 0) > 0 and (c.get("止损价", 0) or 0) > 0
                  and (c.get("每股风险", 0) or 0) > 0
                  and not structure_broken(float(c.get("price", 0) or c.get("现价", 0) or 0),
                                           float(c.get("止损价", 0) or 0),
                                           float(c.get("TY低", 0) or 0) or None)["broken"])
    _cash = _ctx["cap"] - _held
    out = [f"## 持仓（{len(opens)} 笔）"]
    _mv = 0.0   # 持仓市值（Σ 最新收盘价×股数——券商 App 口径，老板对账用）
    if not opens:
        out.append("无在持仓")
    for r in opens:
        st = _position_status(r)
        if st is None:
            continue
        code, name = st["code"], st["name"]
        entry, stop, r_now = st["entry"], st["stop"], st["r_now"]
        o = orders.get(code, {})
        sell = (o.get("sell", "") or "").replace("**", "")
        expire = o.get("expire", "") or ""
        _sell_txt = f"｜ 卖单 {sell}" + (f"(到期{expire})" if expire else "") if sell else ""
        h = steps.get(code)
        act = h["step"]["action"] if h else ""
        # 注意：不用 dict 字面量映射——f-string 会提前求值所有分支（缺 close 键炸）
        if act == "add":
            _act_txt = f"✅确认→补仓@{h['step']['add_price']:.2f}"
        elif act == "exit_reject":
            _act_txt = f"❌确认不通过→平仓@{h['step'].get('close', 0):.2f}"
        elif act == "exit_stop":
            _act_txt = "🛑触止损→出场"
        elif act == "wait":
            _act_txt = "⏳确认中"
        elif act == "hold":
            _act_txt = "⏸受限持有"
        else:
            _act_txt = ""
        # 止损显示建议值（有 stop_update 用建议——券商卖单按建议挂）
        _disp_stop = st["v"].get("stop_update") or stop
        _mv += st["latest_close"] * int(r.get("volume", 0) or 0)
        out.append(f"  {code} {name} 进{entry:.2f} 现{st['latest_close']:.2f} "
                   f"损{_disp_stop:.2f} R{r_now:+.2f} {_act_txt}{_sell_txt}")
        # 从行：非平凡才印（补仓参数/止盈目标/突破质量/止损建议）
        if act == "add" and h:
            cost = float(h["step"]["add_price"]) * int(h["step"]["add_shares"])
            _cash_txt = (f"现金够 ✅" if cost <= _cash
                         else f"现金缺 {cost - _cash:.0f}（等回款）")
            out.append(f"    📋 补 {h['step']['add_shares']} 股 @ {h['step']['add_price']:.2f}"
                       f"（约 {cost:.0f} 元，{_cash_txt}）")
        if st["v"].get("should_exit"):
            _short = st["len_df_pos"] < 21 and "主动出场" in st["v"].get("reason", "")
            out.append(f"    ⚠️ {'主动出场参考信号（短持仓，继续持有）' if _short else '主动出场→建议卖出'}"
                       f"（{st['v'].get('reason', '')}）")
        elif st["v"].get("stop_update"):
            out.append(f"    📋 止损 {stop:.2f} → 建议 {st['v']['stop_update']:.2f}"
                       f"（{st['v'].get('reason', '')}）")
    # 资金段（券商 App 口径优先——老板对账标准，2026-08-12 老板指出"资金 8401"对不上）：
    #   总资产 = 现金 + 持仓市值；累计盈亏 = 市值 - 成本（≈ App 摊薄浮盈亏）
    #   预算口径（本金 8401 与占用）作为第二行，用于补仓/挂单决策
    cap = _ctx["cap"]
    _asset = _cash + _mv
    _pnl = _mv - _held
    _total = _held + _pend + _trig_n + _trig_o
    _gap = max(_total - cap, 0)
    # 老板拍板（2026-08-12）：资金段表格展示——项/数值两列，券商 App 可对账
    out.append("## 资金")
    out.append("| 项 | 数值 |")
    out.append("|---|---|")
    out.append(f"| 总资产（现金+市值）| ~{_asset:.0f} 元 |")
    out.append(f"| 可用现金 | ~{_cash:.0f} 元 |")
    out.append(f"| 持仓市值 | {_mv:.0f} 元 |")
    out.append(f"| 累计盈亏（市值-成本）| {_pnl:+.0f} 元 |")
    out.append("| 本金（预算口径）| {:.0f} 元 |".format(cap))
    out.append(f"| 已持成本 | {_held:.0f} 元 |")
    out.append(f"| 待补仓 | {_pend:.0f} 元 |")
    out.append(f"| 触发占用（新候选 {_trig_n:.0f} + 已挂单 {_trig_o:.0f}）| {_trig_n + _trig_o:.0f} 元 |")
    if _gap > 0:
        out.append(f"| 预算缺口 | {_gap:.0f} 元 ⚠️ 优先补仓，挂单排队等回款 |")
    return "\n".join(out)


def cloud_orders_status() -> str:
    """云单状态全表（R-076：买入+卖出+到期+校准 一行一单——补上旧板块缺失的
    卖出单/到期日维度；校准三态简化版：✅有效/⚠️参数过时/🔴破位/不在候选）"""
    orders = _cloud_orders()
    out = ["## 云单状态"]
    if not orders:
        out.append("云单表为空（无记录）")
        return "\n".join(out)
    try:
        scan_map, batch = _scan_map()
    except Exception:  # noqa: BLE001
        scan_map, batch = None, ""
    if batch:
        out.append(f"  ℹ️ 校准基准：批次 {batch}")
    for o in orders:
        stat = o["status"]
        trig = f"买≥{o['trigger']}" if o["trigger"] else "—"
        sell = (o["sell"] or "—").replace("**", "")
        exp = o["expire"] or "—"
        cal = ""
        if "挂单中" in stat and scan_map:
            s = scan_map.get(o["code"])
            if s is None:
                cal = "⚠️ 不在候选"
            else:
                sb = structure_broken(s["price"], float(o["stop"] or 0) or 0, s.get("ty_low"))
                if sb["broken"]:
                    cal = f"🔴 破位失效（{sb['reason']}）"
                elif abs(s["trigger"] - float(o["trigger"] or 0)) >= 0.005:
                    cal = "⚠️ 参数过时"
                else:
                    cal = "✅ 有效"
                    if s.get("vol", 0) > 0 and s["vol"] <= 1.2:
                        cal += f"（量比 {s['vol']:.2f} 缩量不撤）"
        out.append(f"  {o['code']} {o['name']} {trig}/{o['vol']}股 损{o['stop']}"
                   f" ｜ 卖 {sell} ｜ 到期 {exp} ｜ {stat}{(' ' + cal) if cal else ''}")
    return "\n".join(out)


def full_card(candidates: list[dict], rows: list[dict] | None = None,
              write_file: bool = True, sort_by: str = "risk_mid") -> str:
    """完整执行卡（R-076 决策导航式 5 段：头部 → 今日行动 → 持仓+资金 →
    云单状态 → 市场机会）

    老板拍板（2026-08-12）：信息完整（补仓/止损/平仓/到期不用问）+ 可扫读
    （今日行动置顶、每股 1 主行）。旧板块函数（order_card/position_card/
    protect_card/cloud_order_reminder）保留函数体（测试与交易部复核用），
    full_card 输出不再拼接旧板块文本。

    2026-08-06 全自动执行链：默认落盘 `产出/输出/执行卡_YYYYMMDD.md`。
    """
    _ctx = _env_capital_ctx()
    scale, note = _ctx["scale"], _ctx["note"]
    label = "1R 日" if scale == 1.0 else "0.5R 日"
    _f = _data_freshness_line().replace("  🕒 ", "🕒 ", 1)
    head = (f"# 实盘执行卡 {datetime.now().strftime('%Y-%m-%d')} "
            f"（{datetime.now().strftime('%A')[:3]}）· {label}（{note}）\n"
            f"{_f} ｜ 批次 {_latest_scan_files(_SCAN_DIR)[1] or '无'}\n")
    sec_action = "## 今日行动\n" + action_plan(rows)
    sec_pos = positions_overview(rows, candidates)
    sec_cloud = cloud_orders_status()
    sec_market = "## 市场机会（S 级）\n" + _s_overview_body()
    card = head + "\n" + sec_action + "\n\n" + sec_pos + "\n\n" + sec_cloud \
        + "\n\n" + sec_market
    if write_file:
        _CARD_DIR.mkdir(parents=True, exist_ok=True)
        fname = _CARD_DIR / f"执行卡_{datetime.now().strftime('%Y%m%d')}.md"
        fname.write_text(card, encoding="utf-8")
    return card


def _s_overview_body() -> str:
    """S 级全览正文（R-076：scan_s_overview 的板块体抽离，去标题行——
    新版式市场机会段复用同口径）"""
    import csv
    sdir = _SCAN_DIR
    files, batch = _latest_scan_files(sdir)
    if not files:
        return "  未找到扫描结果（数据基础/扫描输出），无法列出 S 级"
    s_level: dict[str, dict] = {}
    tag_map = {"": "✅ 合格候选（可挂单）",
               "_broken": "❌ 已突破（现价≥触发价，追高不买）",
               "_c23": "❌ C23 不达标",
               "_vol": "❌ 放量不达标（量比≤1.2，不新挂单（已挂单除外）；突破日确认量能）"}
    # R-076d：主文件 name 映射——变体文件 name 空时兜底（08-11 变体曾 name 全空）
    main_names: dict[str, str] = {}
    for f in files:
        if f.stem.replace("scan_result_", "")[15:] != "":
            continue
        try:
            with open(f, encoding="utf-8-sig") as fh:
                for rec in csv.DictReader(fh):
                    main_names[str(rec.get("code", "")).strip()] = rec.get("name", "") or ""
        except (OSError, ValueError):
            pass
    for f in files:
        suffix = f.stem.replace("scan_result_", "")[15:]
        try:
            with open(f, encoding="utf-8-sig") as fh:
                for rec in csv.DictReader(fh):
                    code = str(rec.get("code", "")).strip()
                    if not code or rec.get("评级", "").strip() != "S" or code in s_level:
                        continue
                    reason = tag_map.get(suffix, "")
                    if suffix == "_c23":
                        reason += f"（{rec.get('C23原因', '') or '不达标'}）"
                    _nm = (rec.get("name", "") or "").strip() or main_names.get(code, "") or code
                    s_level[code] = {"trigger": rec.get("触发价", ""),
                                     "stop": rec.get("止损价", ""), "reason": reason,
                                     "name": _nm,
                                     "price": rec.get("price", ""),
                                     "ty_low": rec.get("TY低", "")}
        except (OSError, ValueError):
            continue
    if not s_level:
        return "  当日无 S 级候选（全部不达标或未扫描到）"
    out = []
    for code, info in s_level.items():
        if not info["reason"].startswith("✅"):
            out.append(f"  {info['reason']}  {code} {info['name']} | "
                       f"触发 {float(info['trigger']):.2f} | 止损 {float(info['stop']):.2f}"
                       if info['trigger'] else f"  {info['reason']}  {code} {info['name']}")
            continue
        sb = structure_broken(float(info["price"] or 0), float(info["stop"] or 0),
                              float(info["ty_low"] or 0) or None)
        if sb["broken"]:
            out.append(f"  🔴 已破位（现价≤止损/平台下沿，不可挂单）  {code} {info['name']} | "
                       f"触发 {float(info['trigger']):.2f} | 止损 {float(info['stop']):.2f}"
                       f" | {sb['reason']}")
        else:
            out.append(f"  {info['reason']}  {code} {info['name']} | "
                       f"触发 {float(info['trigger']):.2f} | 止损 {float(info['stop']):.2f}")
    return "\n".join(out)


def main() -> int:
    """命令行入口：python -m 分析决策.跟踪.execution_card"""
    print(full_card([]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
