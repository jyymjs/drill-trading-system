#!/usr/bin/env python3
"""C5 移动止损 开/关 对照实验（2026-08-05 老板拍板 · 方案 C5 第2波）

同信号集对照：读基线 signals.csv（moving_stop=关 的出场结果）→ 重载K线 →
用 track_signal 以 moving_stop=True 重跑同一批信号（仅出场逻辑变化，信号集不变）
→ 对比 胜率/平均R/盈亏比/最大回撤/止损单占比/误伤 → 数据说话：是否值得正式接入。

口径（与回测引擎同源）：
  - 成本：佣金万1.3 + 印花税万5（enable_cost=True，与基线 CSV 同口径）
  - 统计：胜=R>0；prebreak 仅触发者参与；最大回撤=1R等权累计R曲线
  - 误伤分析：开版被止损请出、关版未止损的单（"提前请出"）；其中关版 R>0 = 误伤（本会赢）

用法:
    python 回测系统/c5_trail_compare.py --signals output/backtest/full_full/signals.csv
    python 回测系统/c5_trail_compare.py --signals ... --output-dir output/backtest/c5_trail_compare
    python 回测系统/c5_trail_compare.py --signals ... --codes 000001 600000   # 子集冒烟
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保交易部根目录在路径中（与 main.py 同款）
_HERE = os.path.dirname(os.path.abspath(__file__))   # 项目/回测系统
sys.path.insert(0, os.path.dirname(_HERE))            # 项目/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 交易部根

import pandas as pd

from 回测系统.adapters.data_provider import CacheDataProvider
from 回测系统.tracking import Signal, track_signal

# CSV 简写列 → 六条件全名（与 report.py SCORE_SHORT 反向）
SCORE_SHORT = {"PT平台测试": "PT", "TY统一区间": "TY", "DN动能": "DN",
               "DL独立结构": "DL", "LK轮廓质量": "LK", "SF释放级别": "SF"}
FULL_BY_SHORT = {v: k for k, v in SCORE_SHORT.items()}


def load_signals(csv_path: Path, only_codes: set[str] | None = None) -> tuple[list[Signal], list[dict], list[int]]:
    """读基线 signals.csv → (Signal 列表, CSV 关版参照表)

    refs[i] = {hold: {"exit": float, "r": float, "stopped": bool}}（同源自检用：
    重算的 moving_stop=关 结果必须与基线 CSV 一致，否则对照实验不成立）。
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str})
    holds = sorted({int(c.split("_")[1].rstrip("d")) for c in df.columns
                    if c.startswith("triggered_")})
    sigs, refs = [], []
    for _, row in df.iterrows():          # 中文列名：iterrows 按列名访问（itertuples 属性名被 ASCII 化）
        code = str(row["code"]).zfill(6)  # CSV 前导零丢失（000408→408），补回 6 位
        if only_codes and code not in only_codes:
            continue
        scores = {full_name: (str(row[short_col]), "")
                  for full_name, short_col in SCORE_SHORT.items()}
        sigs.append(Signal(
            code=code, date=pd.Timestamp(row["date"]), mode=row["mode"], grade=row["grade"],
            scores=scores, close=float(row["close"]), trigger=float(row["trigger"]),
            stop=float(row["stop"]), risk=float(row["risk"]),
        ))
        refs.append({h: {"exit": float(row[f"exit_{h}d"]), "r": float(row[f"r_{h}d"]),
                         "stopped": bool(row[f"stopped_{h}d"])} for h in holds})
    return sigs, refs, holds


def _max_drawdown(r_list: list[float]) -> float:
    """累计 R 曲线最大回撤（与 stats._max_drawdown_from_r 同口径）"""
    peak = max_dd = cum = 0.0
    for r in r_list:
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 4)


def _metrics(rs: list[float], stopped: list[bool]) -> dict:
    """一组参与样本的统计（胜率/平均R/累计R/盈亏比/最大回撤/止损单占比）"""
    n = len(rs)
    n_win = sum(1 for r in rs if r > 0)
    gains = sum(r for r in rs if r > 0)
    losses = -sum(r for r in rs if r < 0)
    pf = round(gains / losses, 4) if losses > 0 else (99.0 if gains > 0 else 0.0)
    return {
        "n": n,
        "win_rate": round(n_win / n, 4) if n else 0.0,
        "avg_r": round(sum(rs) / n, 4) if n else 0.0,
        "total_r": round(sum(rs), 4),
        "profit_factor": pf,
        "max_dd": _max_drawdown(rs),
        "stop_rate": round(sum(stopped) / n, 4) if n else 0.0,
    }


def run_compare(signals: list[Signal], refs: list[dict], holds: list[int],
                provider: CacheDataProvider) -> dict:
    """双跑同一信号集（moving_stop 关/开）→ 分桶统计 + 误伤分析 + 同源自检

    Returns:
        {"buckets": {(mode, hold): {"off": dict, "on": dict}}, "early": dict,
         "n_signals": int, "same_source": {"checked": int, "ok": int}}
    """
    # 按 code 预加载 K 线（每只一次）
    klines = {}
    for sig in signals:
        if sig.code not in klines:
            klines[sig.code] = provider.load(sig.code)

    buckets: dict = {}
    early_all = {"off_r": [], "on_r": []}      # 跨桶提前请出样本（供汇总）
    same_source = {"checked": 0, "ok": 0, "mismatches": []}
    n_sig = 0
    for sig, ref in zip(signals, refs):
        n_sig += 1
        df = klines[sig.code]
        for hold in holds:
            oc_off = track_signal(sig, df, hold, enable_cost=True, moving_stop=False)
            oc_on = track_signal(sig, df, hold, enable_cost=True, moving_stop=True)
            # 同源自检：重算关版必须与基线 CSV 一致（exit 价与 R，容差 0.01 价 / 1e-3 R）
            if hold in ref:
                same_source["checked"] += 1
                if (abs(oc_off.exit_price - ref[hold]["exit"]) <= 0.01
                        and abs(oc_off.r - ref[hold]["r"]) <= 1e-3
                        and oc_off.stopped == ref[hold]["stopped"]):
                    same_source["ok"] += 1
                elif len(same_source["mismatches"]) < 10:
                    same_source["mismatches"].append(
                        f"{sig.code}@{sig.date.date()} {sig.mode} hold{hold}: "
                        f"exit {oc_off.exit_price} vs csv {ref[hold]['exit']}, "
                        f"r {oc_off.r} vs {ref[hold]['r']}")
            key = (sig.mode, hold)
            b = buckets.setdefault(key, {"off_r": [], "off_stop": [],
                                         "on_r": [], "on_stop": []})
            if oc_off.participate():
                b["off_r"].append(oc_off.r)
                b["off_stop"].append(oc_off.stopped)
            if oc_on.participate():
                b["on_r"].append(oc_on.r)
                b["on_stop"].append(oc_on.stopped)
            # 误伤：开版止损 且 关版未止损（提前请出）
            if oc_off.participate() and oc_on.participate() and oc_on.stopped and not oc_off.stopped:
                early_all["off_r"].append(oc_off.r)
                early_all["on_r"].append(oc_on.r)

    out = {"buckets": {}, "early": early_all, "n_signals": n_sig,
           "same_source": same_source}
    for key, b in buckets.items():
        out["buckets"][key] = {
            "off": _metrics(b["off_r"], b["off_stop"]),
            "on": _metrics(b["on_r"], b["on_stop"]),
        }
    return out


def _fmt(v: float) -> str:
    return f"{v:.1%}" if 0 <= v <= 1 else f"{v:.4f}"


def render_markdown(result: dict, holds: list[int], src: str, codes_n: int) -> str:
    """渲染对照报告（markdown）"""
    early = result["early"]
    early_n = len(early["off_r"])
    early_win = sum(1 for r in early["off_r"] if r > 0)          # 关版会赢（误伤）
    early_r_delta = round(sum(early["on_r"]) - sum(early["off_r"]), 4)
    ss = result["same_source"]
    lines = [
        "# C5 移动止损 开/关 对照实验（2026-08-05 老板拍板 · 方案 C5 第2波）",
        "",
        f"- 信号源：`{src}`（基线 moving_stop=关，同一信号集重跑开版）",
        f"- 信号数：{result['n_signals']} 笔　股票数：{codes_n} 只　观察窗：{'/'.join(str(h) for h in holds)}d",
        (f"- 同源自检：关版重算与基线 CSV 一致 **{ss['ok']}/{ss['checked']}**"
        f"（{'通过' if ss['ok'] == ss['checked'] else '⚠ 有差异，见附录'}）"),
        ("- 移动止损口径：持仓中每确认新结构低点（买入后新高后的回调低点，日线收盘判定）"
        "→ 止损上移 低点×0.99（须高于当前止损 且 高于进场价——六层第3层正向硬规则）"),
        "- 出处：知识库《价格行为学入门·04 突破单和移动止损篇》（线索c 2026-07-24）核心方法 + 《出场体系·六层出场》第3层",
        "",
        ("> 判定口径：胜=R>0；prebreak 仅触发者参与；盈亏比=总盈利R/|总亏损R|；"
        "最大回撤=1R等权累计R曲线；止损单占比=以止损价出场比例。"),
        "",
    ]
    for mode in ("normal", "prebreak"):
        lines += [f"## {mode}", "", "| hold | 版本 | 参与 | 胜率 | 平均R | 累计R | 盈亏比 | 最大回撤 | 止损单占比 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for hold in holds:
            b = result["buckets"].get((mode, hold))
            if b is None:
                continue
            for tag, m in (("关", b["off"]), ("开", b["on"])):
                lines.append(
                    f"| {hold}d | {tag} | {m['n']} | {_fmt(m['win_rate'])} | {m['avg_r']:.4f} | "
                    f"{m['total_r']:.4f} | {m['profit_factor']:.4f} | {m['max_dd']:.4f} | {_fmt(m['stop_rate'])} |")
        lines.append("")
    # 提前请出 / 误伤分析
    lines += [
        "## 误伤分析（移动止损提前请出的单）",
        "",
        f"- 提前请出（开版止损、关版未止损）：**{early_n}** 笔",
        f"- 其中关版本会盈利（误伤）：**{early_win}** 笔（{_fmt(early_win / early_n) if early_n else 0.0}）",
        (f"- 提前请出笔的 R 净变化（开版总R − 关版总R）：**{early_r_delta:+.4f}**"
        f"（{'负 = 提前请出净亏，误伤为主' if early_r_delta < 0 else '正 = 提前请出净赚，逃顶有效'}）"),
        "",
    ]
    # 结论（数据说话）：以最长观察窗累计 R 净变化 + 误伤率判定
    lines += _verdict_section(result, holds)
    # 附录：同源差异明细（质检 B3 修复：原实现 return 在附录之前，永不输出）
    # 保持输出顺序：结论节在前，附录在后
    if ss["ok"] != ss["checked"]:
        lines += ["## 附录：同源差异明细（前 10 条）", ""]
        lines += [f"- {m}" for m in ss["mismatches"]]
        lines.append("")
    return "\n".join(lines)


def _verdict_section(result: dict, holds: list[int]) -> list[str]:
    """结论节：以最长 hold 累计 R 净变化 + 误伤率自动判定有效/无效

    判定规则（2026-08-05 C5 口径）：
      - 累计 R 净变化（开−关）>0 且 误伤率 ≤50% → 有效，建议接入
      - 否则 → 无效/存疑：报告净变化与误伤数据，建议老师三要素精细版再议
    """
    max_hold = max(holds)
    r_off = r_on = 0.0
    for key, b in result["buckets"].items():
        if key[1] == max_hold:
            r_off += b["off"]["total_r"]
            r_on += b["on"]["total_r"]
    delta = round(r_on - r_off, 4)
    early = result["early"]
    early_n = len(early["off_r"])
    miss_rate = sum(1 for r in early["off_r"] if r > 0) / early_n if early_n else 0.0
    effective = delta > 0 and miss_rate <= 0.5
    verdict = ("✅ 有效：长窗累计 R 提升 且 误伤受控 → 建议正式接入"
               if effective else
               "❌ 无效（简化版）：长窗累计 R 下降 或 误伤严重 → 不接入正式出场，老师三要素精细版再议")
    lines = [
        "## 结论（数据说话）",
        "",
        f"- 最长观察窗（{max_hold}d）累计 R 净变化（开 − 关，normal+prebreak 合计）：**{delta:+.4f}R**",
        f"- 误伤率（提前请出中本会盈利的比例）：**{_fmt(miss_rate)}**",
        f"- **判定：{verdict}**",
        "",
        ("> 依据（C5 定案：先回测验证后上线，数据说话）：胜率上升但平均R/累计R/盈亏比下降，"
        "说明移动止损把「继续大赚」变「小赚止损」——R 分布被压扁，净效应为负。"
        "老师三要素精细版（深度回调/明显影线/调整结构过滤，见 exit_manager.check_trailing_stop 雏形）"
        "等简化版数据后另议。"),
        "",
    ]
    return lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="python 回测系统/c5_trail_compare.py",
                                     description="C5 移动止损 开/关 对照实验")
    parser.add_argument("--signals", required=True, help="基线 signals.csv（moving_stop=关）路径")
    parser.add_argument("--codes", nargs="+", default=None, help="只跑指定代码（冒烟用）")
    parser.add_argument("--holds", nargs="+", type=int, default=None, help="观察窗（默认取 CSV 已有列）")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认 output/backtest/c5_trail_compare）")
    args = parser.parse_args()

    src = Path(args.signals)
    if not src.exists():
        print(f"❌ 信号文件不存在: {src}")
        return 1

    only = set(args.codes) if args.codes else None
    signals, refs, csv_holds = load_signals(src, only)
    if not signals:
        print("❌ 无信号（--codes 过滤后为空）")
        return 1
    holds = args.holds or csv_holds
    codes_n = len({s.code for s in signals})
    print(f"[C5] 信号 {len(signals)} 笔 | 股票 {codes_n} 只 | holds {'/'.join(str(h) for h in holds)}")

    result = run_compare(signals, refs, holds, CacheDataProvider())
    markdown = render_markdown(result, holds, str(src), codes_n)

    out_dir = Path(args.output_dir) if args.output_dir else src.parent / ".." / "c5_trail_compare"
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    report_path.write_text(markdown + "\n", encoding="utf-8")

    # 终端摘要
    print("\n[C5] 对照摘要（开 vs 关）：")
    for mode in ("normal", "prebreak"):
        for hold in holds:
            b = result["buckets"].get((mode, hold))
            if b is None:
                continue
            off, on = b["off"], b["on"]
            print(f"  {mode} {hold}d: 胜率 {_fmt(off['win_rate'])}→{_fmt(on['win_rate'])} | "
                  f"平均R {off['avg_r']:.4f}→{on['avg_r']:.4f} | 回撤 {off['max_dd']:.4f}→{on['max_dd']:.4f} | "
                  f"止损占比 {_fmt(off['stop_rate'])}→{_fmt(on['stop_rate'])}")
    early = result["early"]
    print(f"  提前请出 {len(early['off_r'])} 笔（误伤 {sum(1 for r in early['off_r'] if r > 0)} 笔），"
          f"R 净变化 {sum(early['on_r']) - sum(early['off_r']):+.4f}")
    ss = result["same_source"]
    print(f"  同源自检: {ss['ok']}/{ss['checked']}"
          + (" ✅" if ss["ok"] == ss["checked"] else f" ❌ {ss['mismatches'][:3]}"))
    print(f"  report.md → {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
