"""输出渲染：signals.csv（utf-8-sig，无时间戳列）+ report.md + params.json

可复现纪律：固定浮点格式（%.4f）、稳定列序、文件无时间戳列；
同参数连跑两次 diff signals.csv 应零差异。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from 回测系统.params import BacktestParams
from 回测系统.quality import check_cost_stress, check_half_consistency
from 回测系统.stats import StatBlock, merge_monthly, mode_stats
from 回测系统.tracking import TrackedRecord

# 六条件顺序（信号 CSV 与报告统一）
SCORE_KEYS = ("PT平台测试", "TY统一区间", "DN动能", "DL独立结构", "LK轮廓质量", "SF释放级别")
SCORE_SHORT = {"PT平台测试": "PT", "TY统一区间": "TY", "DN动能": "DN",
               "DL独立结构": "DL", "LK轮廓质量": "LK", "SF释放级别": "SF"}


def signals_to_frame(records: list[TrackedRecord], holds: list[int]) -> pd.DataFrame:
    """信号主表 → DataFrame（每信号一行，hold 结果动态列）"""
    rows = []
    for rec in records:
        sig = rec.signal
        row = {
            "mode": sig.mode,
            "code": sig.code,
            "date": sig.date.strftime("%Y-%m-%d"),
            "grade": sig.grade,
        }
        for key in SCORE_KEYS:
            row[SCORE_SHORT[key]] = sig.score_grade(key)
        row["close"] = sig.close
        row["trigger"] = sig.trigger
        row["stop"] = sig.stop
        row["risk"] = sig.risk
        # C1 财报日避让（2026-08-05 老板拍板）：持仓期跨预约披露日 → 警示文本（空=无）
        row["prbook_warn"] = rec.prbook_warn or ""
        for hold in holds:
            oc = rec.outcomes[hold]
            row[f"triggered_{hold}d"] = int(oc.triggered)
            row[f"entry_{hold}d"] = oc.entry_price
            row[f"exit_{hold}d"] = oc.exit_price
            row[f"exit_date_{hold}d"] = oc.exit_date.strftime("%Y-%m-%d") if oc.exit_date is not None else ""
            row[f"stopped_{hold}d"] = int(oc.stopped)
            row[f"r_{hold}d"] = oc.r
        rows.append(row)
    # 稳定排序：mode → date → code → grade（可复现）
    rows.sort(key=lambda r: (r["mode"], r["date"], r["code"], r["grade"]))
    return pd.DataFrame(rows, columns=[c for c in rows[0]] if rows else [])


def write_signals_csv(path: Path, records: list[TrackedRecord], holds: list[int]) -> None:
    """写 signals.csv（utf-8-sig + BOM，Excel 直接打开；固定浮点格式）"""
    df = signals_to_frame(records, holds)
    if df.empty:
        pd.DataFrame(columns=["mode", "code", "date", "grade"]).to_csv(path, index=False, encoding="utf-8-sig")
        return
    df.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.4f")


def _fmt_rate(v: float) -> str:
    return f"{v:.1%}" if 0 <= v <= 1 else f"{v:.4f}"


def _grade_table(buckets: dict[str, StatBlock], mode: str, holds: list[int]) -> str:
    """S/A/B 分列 × hold 统计表（markdown）"""
    lines = ["| 评级 | hold | 信号数 | 触发率 | 参与 | 胜率 | 平均R | 累计R | 最大回撤 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for grade in ("S", "A", "B"):
        for hold in holds:
            b = buckets.get(f"{mode}|{grade}|{hold}")
            if b is None:
                continue
            lines.append(
                f"| {grade} | {hold}d | {b.n_signals} | {_fmt_rate(b.trigger_rate)} | "
                f"{b.n_participate} | {_fmt_rate(b.win_rate)} | {b.avg_r:.4f} | "
                f"{b.total_r:.4f} | {b.max_drawdown:.4f} |"
            )
    return "\n".join(lines)


def _monthly_table(monthly: dict) -> str:
    if not monthly:
        return "_（无信号）_"
    lines = ["| 月份 | 信号数 |", "|---|---|"]
    for month, count in monthly.items():
        lines.append(f"| {month} | {count} |")
    return "\n".join(lines)


def _mode_section(records: list[TrackedRecord], buckets: dict[str, StatBlock],
                  mode: str, holds: list[int]) -> str:
    label = {"normal": "6条件已突破", "prebreak": "5条件预突破"}[mode]
    m = mode_stats(records, mode, holds)
    monthly = merge_monthly(buckets, mode)
    n_signals = len({(rec.signal.code, str(rec.signal.date))
                     for rec in records if rec.signal.mode == mode})
    lines = [
        f"### {mode}（{label}）",
        "",
        f"- 信号数：**{n_signals}** 笔（×{len(holds)} 个观察窗 = 统计组合 {m['n_signals']} 笔）",
        f"- 触发率（prebreak）：**{_fmt_rate(m['trigger_rate'])}**（{m['n_triggered']}/{m['n_signals']}）"
        if mode == "prebreak" else "- 进场：信号日 T 收盘价成交，全部参与统计",
        f"- 参与统计：{m['n_participate']} 笔",
        f"- 胜率（R>0）：**{_fmt_rate(m['win_rate'])}**（{m['n_win']}/{m['n_participate']}）",
        f"- 平均R：**{m['avg_r']:.4f}**　累计R：{m['total_r']:.4f}",
        f"- 最大回撤（累计R曲线）：**{m['max_drawdown']:.4f}R**",
        "",
        "#### S/A/B 分列",
        "",
        _grade_table(buckets, mode, holds),
        "",
        "#### 月度分布（信号集中度）",
        "",
        _monthly_table(monthly),
    ]
    return "\n".join(lines)


def _consistency_section(records: list[TrackedRecord], holds: list[int]) -> str:
    """D1 分段一致性检查节（方案 D 类 2026-08-05 老板拍板）"""
    rows = []
    for label, mode, grade in (("全样本（跨模式/评级）", None, None),
                               ("仅 S 级（硬门槛：必须前后半同为正）", None, "S")):
        r = check_half_consistency(records, holds, mode=mode, grade=grade)
        if r["start"] is None:
            rows.append(f"| {label} | _ | _ | _（无参与统计信号）_ |")
            continue
        rows.append(f"| {label} | {r['front_total_r']:.4f} | {r['back_total_r']:.4f} | "
                    f"**{r['verdict']}** {r['reason']} |")

    # 区间时长说明（取全样本行）
    r0 = check_half_consistency(records, holds)
    if r0["start"] is not None:
        total_years = r0["total_days"] / 365.25
        front_years = r0["front_days"] / 365.25
        back_years = r0["back_days"] / 365.25
        head = (f"信号区间 `{r0['start']} ~ {r0['end']}`（{total_years:.1f} 年），"
                f"按时间中点切前后两半：前半 `{r0['start']} ~ {r0['mid']}`（{front_years:.1f} 年）/"
                f"后半 `{r0['mid']} ~ {r0['end']}`（{back_years:.1f} 年）")
    else:
        head = "信号区间：无参与统计信号"

    return "\n".join([
        "## 分段一致性检查（D1 · 防过拟合）",
        "",
        ("> 回测区间按时间切前后两半（各 ≥1.5 年，覆盖牛熊段）；两半累计 R 同为正 → 合格；"
        "前正后负 → 过拟合嫌疑（标黄）；前负后正 → 风格适应慢（正常接受，不判罪）；"
        "S 级策略必须前后半同为正。（方案 D 类 2026-08-05 老板拍板）"),
        "",
        f"- {head}",
        "",
        "| 口径 | 前半累计R | 后半累计R | 判定 |",
        "|---|---|---|---|",
        *rows,
        "",
    ])


def _stress_section(base_records: list[TrackedRecord],
                    stress_records: list[TrackedRecord],
                    holds: list[int]) -> str:
    """D2 2倍成本压力测试节（方案 D 类 2026-08-05 老板拍板）"""
    r = check_cost_stress(base_records, stress_records, holds)
    b, s = r["base"], r["stress"]
    return "\n".join([
        "## 2 倍成本压力测试（D2 · 成本敏感体检）",
        "",
        ("> 佣金万1.3+印花税万5+滑点全 ×2（万2.6+万10+滑点翻倍万2）同参数重跑（main.py 双跑引擎，"
        "非推算）；2 倍成本下年化 R 仍为正 → 抗压合格，≤0 → 利润太薄实盘必亏。（方案 D 类 2026-08-05 老板拍板）"),
        "",
        f"- 信号跨度 {r['years']:.2f} 年（1R 等权累计口径，跨模式/评级/hold）",
        "",
        "| 指标 | 基线（1 倍成本） | 2 倍成本压力 |",
        "|---|---|---|",
        f"| 参与笔数 | {b['n_participate']} | {s['n_participate']} |",
        f"| 累计R | {b['total_r']:.4f} | {s['total_r']:.4f} |",
        f"| 年化R | {b['annual_r']:.4f} | **{s['annual_r']:.4f}** |",
        f"| 平均R | {b['avg_r']:.4f} | {s['avg_r']:.4f} |",
        f"| 最大回撤（累计R曲线） | {b['max_drawdown']:.4f} | {s['max_drawdown']:.4f} |",
        "",
        f"判定：**{r['verdict']}** {r['reason']}",
        "",
    ])


def _prbook_section(records: list[TrackedRecord], gate_counts: dict | None,
                    params: BacktestParams) -> str:
    """C1 财报日避让节（2026-08-05 老板拍板 · 优化方案 C1 定案第3条·第一层）

    第一层口径：信号日 = 该股预约披露日 → 不新开仓（否决）；持仓期跨披露日 → 警示
    （记录到本报告，不强制平仓）。评级与执行分离——grade() 评级不受影响。
    """
    n_warn = sum(1 for rec in records if rec.prbook_warn)
    gc = gate_counts or {}
    state = "开（正式接入）" if params.prbook_gate else "关（对照）"
    lines = [
        "## 财报日避让（C1 第一层 · 预约披露日）",
        "",
        (f"> 2026-08-05 老板拍板执行《量化体系优化方案》C1 项：预约披露日不新开仓；"
        f"已持仓跨披露日 → 警示不强制平仓。本回测 C1 开关：**{state}**。"),
        "",
        f"- 披露日否决：**{gc.get('veto_prbook', 0)}** 笔（信号日=预约披露日，未开仓）",
        f"- 持仓警示：**{n_warn}** 笔（持仓期内跨过预约披露日，详见 signals.csv prbook_warn 列）",
        f"- 无披露数据放行：{gc.get('prbook_missing', 0)} 笔（数据缺失不误杀）",
        "",
    ]
    return "\n".join(lines)


def _regime_section(records: list[TrackedRecord], index_df, holds: list[int]) -> str:
    """市场状态分段节（T-021 · 防'牛市滤镜' 2026-08-06 老板拷问驱动）

    规则（market_regime.py 注释即规则，白话版）：
      - 用上证指数日线判定；牛 = 收盘站上 120 日均线 且 20 日均线高于 60 日
        （多头排列 + 半年线之上）；熊 = 收盘跌破 120 日均线；其余 = 震荡
      - 信号按"信号日"归属；只用 ≤ 信号日的指数数据（无前视，T+1 决策时点）
      - 每段统计口径与全报告一致：1R 等权累计，胜 = R>0，盈亏比 = 盈利R和/亏损R和
      - "未知" = 信号日不在指数日历（指数数据缺失，不参与牛熊占比）
    """
    if index_df is None:
        return ("## 市场状态分段（防'牛市滤镜' · T-021）\n\n"
                "> 未提供指数数据（上证指数缓存缺失/加载失败），本节跳过。\n\n")
    from 回测系统.market_regime import REGIMES, regime_stats

    st = regime_stats(records, index_df, holds)
    if not st:
        return ("## 市场状态分段（防'牛市滤镜' · T-021）\n\n"
                "> 指数数据为空，本节跳过。\n\n")

    total_sig = sum(b["n_signals"] for b in st.values())
    lines = [
        "## 市场状态分段（防'牛市滤镜' · T-021）",
        "",
        ("> 回测区间内按上证指数市场状态分段统计（规则：牛 = 收盘站上 120 日均线且 20 日均线 "
         "> 60 日均线；熊 = 收盘跌破 120 日均线；其余 = 震荡。信号按信号日归属，只用当日及之前"
         "指数数据，无前视）。看策略在各市场环境下的真实表现，防'整段牛市'的滤镜效应。"),
        "",
        f"- 统计组合共 **{total_sig}** 笔（信号数 × hold 观察窗，口径同总览）",
        "",
        "| 市场状态 | 时段 | 笔数 | 胜率 | 平均R | 累计R | 盈亏比 | 最大回撤 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for regime in REGIMES:
        b = st[regime]
        span = f"{b['start'].date()} ~ {b['end'].date()}" if b["start"] is not None else "_（无信号）_"
        pf = "∞" if b["profit_factor"] is None and b["total_r"] > 0 else (
            "-" if b["profit_factor"] is None else f"{b['profit_factor']:.4f}")
        lines.append(
            f"| {regime} | {span} | {b['n_participate']} | {_fmt_rate(b['win_rate'])} | "
            f"{b['avg_r']:.4f} | {b['total_r']:.4f} | {pf} | {b['max_drawdown']:.4f} |"
        )
    if "未知" in st and st["未知"]["n_signals"]:
        u = st["未知"]
        lines += [
            "",
            f"- 未知段：{u['n_signals']} 笔组合信号（信号日不在指数日历，未计入上表占比）",
        ]
    lines += ["", ""]
    return "\n".join(lines)


def _compare_table(records: list[TrackedRecord], holds: list[int]) -> str:
    """normal vs prebreak 对比段"""
    lines = ["| 指标 | normal | prebreak |", "|---|---|---|"]
    for mode in ("normal", "prebreak"):
        m = mode_stats(records, mode, holds)
        if mode == "normal":
            normal = m
        else:
            pre = m
            lines.append(f"| 信号数 | {normal['n_signals']} | {pre['n_signals']} |")
            lines.append(f"| 触发率 | - | {_fmt_rate(pre['trigger_rate'])} |")
            lines.append(f"| 参与统计 | {normal['n_participate']} | {pre['n_participate']} |")
            lines.append(f"| 胜率 | {_fmt_rate(normal['win_rate'])} | {_fmt_rate(pre['win_rate'])} |")
            lines.append(f"| 平均R | {normal['avg_r']:.4f} | {pre['avg_r']:.4f} |")
            lines.append(f"| 累计R | {normal['total_r']:.4f} | {pre['total_r']:.4f} |")
            lines.append(f"| 最大回撤 | {normal['max_drawdown']:.4f} | {pre['max_drawdown']:.4f} |")
    return "\n".join(lines)


def write_report(path: Path, records: list[TrackedRecord],
                 buckets: dict[str, StatBlock], params: BacktestParams,
                 meta: dict | None = None,
                 stress_records: list[TrackedRecord] | None = None,
                 index_df=None) -> None:
    """写 report.md（含数据与样本口径说明 + D1/D2 质检节，满足风险提示要求）

    Args:
        records: 基线引擎产出
        buckets: 统计分桶
        params: 回测参数
        meta: 运行元信息（股票数等）
        stress_records: D2 2倍成本（cost_multiplier=2.0）重跑产出；None=省略 D2 节
        index_df: 上证指数日线（market_regime 市场状态分段用；None=跳过该节）
    """
    holds = params.holds
    modes = sorted({rec.signal.mode for rec in records} | {"normal", "prebreak"})
    meta = meta or {}

    lines = [
        "# 回测报告（策略 V2 历史验证 · 时光机）",
        "",
        f"- 运行区间：`{params.start or '缓存起点'} ~ {params.end or '缓存终点'}`（--start/--end 只过滤信号记录，不改网格）",
        f"- 策略：{params.strategy}（钻潜评级策略 V2，同源复用 grade()/prebreak_grade()，零逻辑重写）",
        f"- 模式：{params.mode}　步长：{params.interval} 交易日　观察窗：{'/'.join(str(h) for h in holds)}d",
        f"- 信号评级：{'/'.join(params.grades)}　并发：{params.max_workers} 进程",
        f"- 股票数：{meta.get('processed', '?')} 只（跳过 {meta.get('skipped', 0)}）",
        "",
        "## 数据与样本口径说明（重要）",
        "",
        "- **数据源**：`data/cache/` CSV 直读（pytdx 优先，baostock/akshare 兜底缓存），pytdx 不复权、其余前复权，长期窗口价格可能失真（复权不一致）。",
        "- **区间限制**：缓存单只 ≤800 根 ≈ 3 年；深历史（>3 年）回测列为二期。",
        "- **样本口径**：股票池为当前存活标的，退市/ST 退市股不在池内，胜率会**系统性高估**；结论只用于相对比较（S vs A vs B、normal vs prebreak），不作绝对胜率承诺。",
        "- **成交简化**：v1 按信号日 T 收盘价成交（prebreak=触发价），未模拟涨跌停无法买入、滑点、手续费——胜率偏乐观。",
        "- **出场简化**：v1 仅「止损 + hold 到期收盘」两种出场；出场六层体系留后续版本。",
        "- **止损口径**：normal = max(2×ATR14, 2%×进场价)；prebreak = 策略原生 trigger/stop（同源）。",
        "- **无前视**：评级窗口一律 `df.iloc[:t+1]` 先截断后评级；指标全序列一次向量化（向后看算子，等价性由单测证明）。",
        "",
        f"## 总览（统计组合 {sum(b.n_signals for b in buckets.values())} 笔 = 信号数 × hold 观察窗；去重信号数见各模式段）",
        "",
    ]

    for mode in modes:
        lines.append(_mode_section(records, buckets, mode, holds))
        lines.append("")

    if "normal" in modes and "prebreak" in modes:
        lines += ["## normal vs prebreak 对比", "", _compare_table(records, holds), ""]

    # 市场状态分段（T-021 防"牛市滤镜" 2026-08-06；指数数据缺失则跳过）
    lines += [_regime_section(records, index_df, holds)]

    # C1 财报日避让（2026-08-05 老板拍板执行优化方案 C1 第一层）
    lines += [_prbook_section(records, meta.get("gate_counts"), params)]

    # D1 分段一致性（2026-08-05 方案 D 类质检，基于主记录）
    lines += [_consistency_section(records, holds)]
    # D2 2倍成本压力（main.py 双跑引擎产出，未跑则省略）
    if stress_records is not None:
        lines += [_stress_section(records, stress_records, holds)]

    lines += ["---", "", "_本报告由 backtest/main.py run 生成；参数快照见 params.json；仅做验证，不做寻优，任何参数调整须经老板书面同意。_"]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
