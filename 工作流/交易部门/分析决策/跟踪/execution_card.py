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
from 分析决策.风控.capital import get_capital

_CARD_DIR = Path(__file__).resolve().parent.parent.parent / "产出" / "输出"


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


def order_card(candidates: list[dict], capital: float | None = None,
               risk_ratio: float = 0.02, sort_by: str = "risk_mid") -> str:
    """挂单指引卡：当日环境档 → 新候选 1R/0.5R 挂单指引

    Args:
        candidates: prebreak 候选（需含 code/name/触发价/止损价/每股风险/评级，
            与 scanner prebreak 输出结构一致）
        capital: 当日资金（缺省读取 capital.json；5600 → 1R 风险额 112 元）
        risk_ratio: 单笔风险比例（实盘线定稿 2%，G9 2026-08-06 老板拍板）
        sort_by: 候选排序（2026-08-06 老板拍板质量优先，T-032 定案）：
            "risk_mid"=每股风险居中优先（|每股风险-1.5| 升序，默认）；
            "none"=扫描原序

    Returns:
        指引卡文本（含「1R 日/0.5R 日」标注与逐票挂单指引；无候选 → 相应说明）
    """
    scale, note = env_day_scale()
    cap = capital if capital is not None else get_capital()
    risk_amt = round(cap * risk_ratio * scale, 2)
    today = datetime.now().strftime("%Y-%m-%d")
    W = 76
    line = "-" * W
    label = "1R 日" if scale == 1.0 else "0.5R 日"
    if sort_by == "risk_mid":
        candidates = sorted(candidates,
                            key=lambda r: abs((r.get("每股风险", 0) or 0) - 1.5))
    out = [line, f"〔挂单指引卡〕{today} 当日环境档 = {label}（{note}）".center(W), line]
    out.append(f"  新候选挂单路径：{'1R 正常挂单' if scale == 1.0 else '0.5R 试探挂单'}"
               f" | 单笔风险额 {risk_amt:.0f} 元（{cap:.0f}×{risk_ratio:.0%}"
               f"{'×0.5' if scale != 1.0 else ''}）")
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


def _iter_half_positions(rows: list[dict] | None = None) -> list[dict]:
    """在持 0.5R 试探仓（phase=="half" 且 status=="open"）

    数据源：sim_journal（模拟层，G3 定案后由 sim_open 写入 phase 列）；
    兼容 trade_journal 带 phase 列的行（实盘录入 0.5R 时手动标注 phase=half，
    见 README 说明——判定逻辑一致，不复制）。
    """
    if rows is None:
        rows = sim_trading._read_all()
    return [r for r in rows if r.get("phase") == "half" and r.get("status") == "open"]


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
        step = sim_trading._check_half_position(df, r)
        act = step["action"]
        entry = float(r["entry_price"])
        stop = float(r["stop_loss"])
        held = int(r.get("volume", 0))
        if act == "add":
            out.append(f"  ✅ {code} {name}: 收线确认（{step['reason']}）→ "
                       f"补 0.5R 挂单 {step['add_shares']} 股 @ {step['add_price']:.2f}"
                       f"（等额，总 {held + step['add_shares']} 股 = 1R）")
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
    out.append(line)
    return "\n".join(out)


def system_status(rows: list[dict] | None = None) -> str:
    """系统状态行（2026-08-06 老板拍板全自动+熔断式警报——审核层移除后仅系统级介入）

    警报项（触发仅提醒，不停止交易——实盘三纪律：连亏期别慌）：
      ① 连败预警：sim_journal 已平仓连续止损 ≥5 笔（蒙卡最大连败 18-22，5 为提醒线）
      ② 在持仓位 + 板块分散提示（5 仓同板块 ≥2 时人工目检，G15 人工纪律保留）
      ③ 账户校验：journal 无任何记录 → 提示（数据缺失时执行卡结论不可信）

    Args:
        rows: journal 行（缺省读取 sim_journal）；测试可注入
    """
    rows = rows if rows is not None else sim_trading._read_all()
    out = []
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


def full_card(candidates: list[dict], rows: list[dict] | None = None,
              write_file: bool = True, sort_by: str = "risk_mid") -> str:
    """完整执行卡（系统状态 + 挂单指引卡 + 分步建仓持仓卡）

    2026-08-06 全自动执行链：默认落盘 `产出/输出/执行卡_YYYYMMDD.md`
    （T-022 同日覆盖；计划任务与手动调用同款）——扫描输出即挂单依据，不审核。
    """
    card = "〔系统状态〕\n" + system_status(rows) + "\n" \
        + order_card(candidates, sort_by=sort_by) + "\n" + position_card(rows)
    if write_file:
        _CARD_DIR.mkdir(parents=True, exist_ok=True)
        fname = _CARD_DIR / f"执行卡_{datetime.now().strftime('%Y%m%d')}.md"
        fname.write_text(card, encoding="utf-8")
    return card


def main() -> int:
    """命令行入口：python -m 分析决策.跟踪.execution_card"""
    print(full_card([]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
