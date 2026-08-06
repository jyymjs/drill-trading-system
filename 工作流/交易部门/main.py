#!/usr/bin/env python3
"""交易部 - 入口

用法:
    python main.py list             查看A股列表
    python main.py kline <代码>     查看单只股票K线
    python main.py scan [--strategy <策略名>]  全市场扫描
"""
import argparse

# 确保项目根目录在路径中
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from 分析决策.分析.reporter import plot_kline, print_results, save_results
from 分析决策.分析.scanner import apply_c23_filter, scan, split_prebreak_results
from 数据基础.配置.stock_pool import get_all_stocks
from 策略.核心策略.base import BaseStrategy


def cmd_list(args):
    """查看A股列表"""
    stocks = get_all_stocks()
    # 按交易所分组
    sz = [s for s in stocks if s["code"].startswith("0") or s["code"].startswith("3")]
    sh = [s for s in stocks if s["code"].startswith("6")]
    bj = [s for s in stocks if s["code"].startswith("8") or s["code"].startswith("4")]

    print(f"\n[LIST] A股市场概览 ({datetime.now().strftime('%Y-%m-%d')})")
    print(f"{'-' * 40}")
    print(f"  深圳主板/创业板: {len(sz)} 只")
    print(f"  上海主板:        {len(sh)} 只")
    print(f"  北京交易所:      {len(bj)} 只")
    print(f"  {'-' * 20}")
    print(f"  合计:            {len(stocks)} 只")

    if args.preview:
        print(f"\n前 {args.preview} 只:")
        for s in stocks[:args.preview]:
            print(f"  {s['code']}  {s['name']}")


def cmd_kline(args):
    """查看单只股票K线"""
    symbol = args.code
    print(f"\n[KLINE] {symbol} 日K线分析")
    print(f"{'-' * 40}")

    plot_kline(symbol, name=args.name or "", save=not args.no_save)


def _load_strategy(name: str):
    """动态加载策略模块，自动发现 BaseStrategy 子类

    Args:
        name: 策略模块名 (如 "demo_strategy", "zuanqian_strategy")

    Returns:
        策略实例，或 None
    """
    import importlib
    try:
        module = importlib.import_module(f"策略.核心策略.samples.{name}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaseStrategy)
                    and attr is not BaseStrategy):
                return attr()
        return None
    except (ImportError, AttributeError):
        return None


def _scan_report_already_today() -> str | None:
    """当日是否已产出扫描报告（T-022 当日去重，2026-08-06）

    幂等依据：OUTPUT_DIR（数据基础/output）下存在当日 scan_result_*.csv。
    白天手动跑过 → 19:05 计划任务再跑时跳过，避免白跑 5 分钟 + 多份报告混淆。

    Returns:
        已产出的报告文件名（str），当日无报告返回 None
    """
    from 数据基础.配置.settings import OUTPUT_DIR
    today = datetime.now().strftime("%Y%m%d")
    for p in sorted(OUTPUT_DIR.glob(f"scan_result_{today}_*.csv"), reverse=True):
        return p.name
    return None


def cmd_scan(args):
    """全市场扫描"""
    # T-022 当日去重：已产出当日扫描报告 → 跳过重复扫描（Windows 计划任务 19:05 重复触发场景）
    existing = _scan_report_already_today()
    if existing:
        print(f"当日扫描报告已产出（{existing}），跳过重复扫描（T-022 当日去重）")
        print("如需强制重扫：删除当日 scan_result 文件后再运行")
        return
    strategy = _load_strategy(args.strategy)
    if strategy is None:
        print(f"❌ 未找到策略: '{args.strategy}'")
        print("可用策略: demo_strategy, zuanqian_strategy (钻潜标准模式, 钻潜VCP模式)")
        sys.exit(1)

    print(f"\n[SCAN] 全市场扫描 | 策略: {strategy.name}")
    print(f"{'-' * 50}")
    print(f"  说明: {strategy.description}")
    print(f"  参数: {strategy.get_params()}")
    print()

    # 执行扫描
    results = scan(strategy, mode=getattr(args, "mode", "normal"))

    # 价格过滤（R-009：小资金整手约束——高价股买不起一手）
    max_price = getattr(args, "max_price", None)
    if max_price:
        before = len(results)
        results = [r for r in results if r.get("price", 0) and r["price"] <= max_price]
        print(f"  价格过滤（≤{max_price}元）: {before} → {len(results)} 只")
        print()

    # 输出结果
    mode = getattr(args, "mode", "normal")
    broken = []  # prebreak 模式下已突破（现价≥触发价）的研究列表
    c23_rejected = []  # prebreak 模式下 C23 不达标的研究列表
    if mode == "prebreak":
        # 2026-08-06 实战发现 + 老板拍板：已突破的挂条件单会立即成交=追高，
        # 主表只留未突破候选；已突破行标注保留（供研究，不参与挂单候选）
        results, broken = split_prebreak_results(results)

        # 2026-08-06 C23 替换进策略（老板拍板：S 级 + dn_confirm 1.5 + 动量≤10% +
        # 止损距离 0.5~3 元；现方案封存见 策略/核心策略/策略版本存档.md）——
        # 主表只留 C23 达标候选；不达标行标注保留（供研究，不参与挂单候选）
        results, c23_rejected = apply_c23_filter(results)
        if c23_rejected:
            print(f"\n=== C23 过滤（动量>10% 或 止损<0.5/3.0元，不参与挂单候选，供研究）: "
                  f"{len(c23_rejected)} 只 ===")
            print_results(c23_rejected, mode=mode)
            save_results(c23_rejected, suffix="_c23")

    if results:
        print_results(results, mode=mode)
        save_results(results)
        if broken:
            print(f"\n=== 已突破（现价≥触发价，不参与挂单候选，供研究）: {len(broken)} 只 ===")
            print_results(broken, mode=mode)
            save_results(broken, suffix="_broken")
    else:
        print("\n当前没有符合条件的股票")

    # 执行卡（2026-08-06 老板确认四连包①）：挂单指引卡（1R/0.5R 双路径，按当日
    # 环境档）+ 分步建仓持仓卡（在持 0.5R 试探仓的收线确认动作指令）。
    # prebreak 主表（C23 达标未突破候选）为挂单对象；normal 模式仅输出持仓卡。
    try:
        from 分析决策.跟踪.execution_card import order_card, position_card
        print()
        print(order_card(results if mode == "prebreak" else []))
        print(position_card())
    except ImportError:
        pass

    # 纪律报告（每次scan自动输出）
    try:
        from 分析决策.风控.trade_guardian import discipline_report
        print()
        print(discipline_report())
    except ImportError:
        pass


def cmd_diagnose(args):
    """单只股票诊断：逐步检测策略条件"""
    from 分析决策.分析.indicators import all_indicators
    from 数据基础.数据.fetcher import get_daily_kline

    strategy = _load_strategy(args.strategy)
    if strategy is None:
        print(f"❌ 未找到策略: '{args.strategy}'")
        sys.exit(1)

    code = args.code
    print(f"\n[DIAGNOSE] {code} → {strategy.name}")
    print(f"{'=' * 50}")

    df = get_daily_kline(code, use_cache=True)
    if df.empty:
        print(f"❌ 未获取到 {code} 的数据")
        return

    print(f"  数据: {len(df)} 行 (最新: {df.iloc[-1]['日期']})")
    print(f"  收盘: {df.iloc[-1]['收盘']:.2f}")
    print()

    # 预过滤
    pre = strategy.quick_prefilter(df)
    print(f"【预过滤】{'通过' if pre else '未通过'}")
    if not pre:
        print("  说明: 快速预过滤已排除此股票（基础条件不满足）")
        return
    print()

    # 计算指标并执行诊断
    needed = strategy.required_indicators
    df = all_indicators(df, needed_cols=needed)
    result = strategy.debug_filter(df)

    print(f"【最终结果】{'符合条件' if result['match'] else '不符合条件'}")
    print(f"{'-' * 50}")
    for step_name, detail in result.get("steps", {}).items():
        icon = "+" if detail.get("passed") else "-"
        print(f"  [{icon}] {step_name}")
        print(f"    {detail.get('reason', '')}")
    print()

    # 额外信息
    latest = df.iloc[-1]
    print("【附加信息】")
    print(f"  MA20: {latest.get('MA20', 'N/A'):.2f}" if "MA20" in df.columns else "")
    print(f"  量比: {latest.get('VOL_RATIO', 'N/A'):.2f}" if "VOL_RATIO" in df.columns else "")
    if "RSI" in df.columns:
        print(f"  RSI: {latest['RSI']:.1f}")

    # 止损价（层面1）
    if result.get("match") and hasattr(strategy, 'prebreak_grade'):
        pr = strategy.prebreak_grade(df)
        if pr.get("match"):
            print(f"  TY区间: {pr.get('ty_high',0):.2f}~{pr.get('ty_low',0):.2f}")
            print(f"  建议止损: {pr.get('stop_loss',0):.2f}")
            print(f"  每股风险: {pr.get('risk_per_share',0):.2f}")
            from 分析决策.风控.capital import calc_lots as _cl
            print(f"  建议手数: {_cl(pr.get('risk_per_share',1))}")


def cmd_track(args):
    """交易记录管理"""
    import numpy as np

    from 分析决策.跟踪.equity_curve import plot_equity_curve
    from 分析决策.跟踪.monte_carlo import plot_simulation, simulate
    from 分析决策.跟踪.trade_journal import format_stats, get_all_trades, trade_stats

    if args.action == "list":
        trades = get_all_trades()
        if not trades:
            print("\n暂无交易记录")
            return
        stats = trade_stats(trades)
        print(f"\n=== 交易记录 ({len(trades)} 笔) ===\n")
        for t in trades:
            print(f"  {t.get('trade_id','?')} | {t.get('symbol','')} {t.get('name','')} "
                  f"| {t.get('direction','')} | R={t.get('r_multiple','?')} | {t.get('exit_reason','')}")
        print(f"\n{format_stats(stats)}")

    elif args.action == "add":
        # 交互式录入
        print("\n=== 录入交易记录 ===\n")
        import uuid
        from datetime import datetime

        from 分析决策.风控.position import TradeRecord

        trade_id = str(uuid.uuid4())[:8]
        symbol = input("代码: ").strip()
        name = input("名称: ").strip()
        direction = input("方向(long/short): ").strip() or "long"
        entry_price = float(input("进场价: ").strip())
        exit_price = float(input("离场价: ").strip())
        volume = int(input("股数: ").strip())
        stop_loss = float(input("止损价: ").strip())
        grade = input("进场评级(S/A/B/C): ").strip() or "?"
        exit_reason = input("离场原因: ").strip() or "手动"

        # 价格合理性校验（拉取缓存中的最近收盘价对比）
        try:
            from 数据基础.数据.fetcher import get_daily_kline as _gdk
            _ref_df = _gdk(symbol, use_cache=True)
            if not _ref_df.empty:
                _ref_close = _ref_df["收盘"].iloc[-1]
                for _name, _price in [("进场价", entry_price), ("离场价", exit_price), ("止损价", stop_loss)]:
                    if _price > _ref_close * 1.5 or _price < _ref_close * 0.5:
                        print(f"  [警告] {_name} {_price} 偏离最近收盘价{_ref_close:.2f}超过50%，请确认")
                        if input("  继续?(y/n): ").strip().lower() != 'y':
                            print("  已取消")
                            return
        except ImportError:
            pass

        risk_per = abs(entry_price - stop_loss)
        (exit_price - entry_price) / risk_per if risk_per > 0 else 0

        # 计算交易成本
        from 分析决策.风控.capital import calc_trade_fee
        is_etf = symbol.startswith(("51", "15", "16"))
        fee_entry = calc_trade_fee(entry_price * volume, is_etf)
        fee_exit = calc_trade_fee(exit_price * volume, is_etf)
        total_fee = fee_entry + fee_exit
        pnl_real = (exit_price - entry_price) * volume - total_fee
        r_mul_real = pnl_real / risk_per / volume if risk_per > 0 else 0

        today = datetime.now().strftime("%Y-%m-%d")
        trade = TradeRecord(
            trade_id=trade_id, symbol=symbol, name=name,
            direction=direction, entry_date=today, exit_date=today,
            entry_price=entry_price, exit_price=exit_price,
            volume=volume, stop_loss=stop_loss,
            r_multiple=round(r_mul_real, 2),
            pnl=round(pnl_real, 2),
            grade_at_entry=grade, exit_reason=exit_reason,
        )
        from 分析决策.跟踪.trade_journal import add_trade as _add
        _add(trade)
        print(f"\n已记录: {symbol} {round(r_mul_real,2)}R (含手续费¥{total_fee:.1f})")

    elif args.action == "equity":
        path = plot_equity_curve(save=True)
        if path:
            trades = get_all_trades()
            stats = trade_stats(trades)
            print("\n=== 资金曲线 ===\n")
            print(f"  图片已保存: {path}")
            print(f"\n{format_stats(stats)}")
        else:
            print("\n暂无交易记录")

    elif args.action == "monte-carlo":
        from 分析决策.跟踪.monte_carlo import (
            load_backtest_r_series,
            load_backtest_years,
            render_terminal_report,
            simulate,
        )
        from 分析决策.跟踪.trade_journal import get_all_trades

        if args.source == "backtest":
            trades = load_backtest_r_series(args.signals, mode=args.mode, hold=args.hold,
                                            sample_n=args.samples)
            src_desc = (f"回测数据源 {Path(args.signals).parent.name} "
                        f"({args.mode}/{args.hold} 触发信号抽样 {len(trades)} 笔)")
            years = args.years or load_backtest_years(args.signals)
        else:
            trades = get_all_trades()
            src_desc = f"实盘 journal {len(trades)} 笔"
            if not trades:
                # 实盘无记录：自动回退回测数据（回测 R 已含成本，不再重复扣费）
                print("  实盘 journal 无交易记录，自动回退回测数据源（--source backtest 可显式指定）")
                trades = load_backtest_r_series(args.signals, mode=args.mode, hold=args.hold,
                                                sample_n=args.samples)
                src_desc = (f"回测数据源 {Path(args.signals).parent.name} "
                            f"({args.mode}/{args.hold} 触发信号抽样 {len(trades)} 笔)")
                years = args.years or load_backtest_years(args.signals)
            else:
                years = args.years or 3.0

        fee = 0.0 if args.source == "backtest" or src_desc.startswith("回测") else 0.02
        result = simulate(trades, n_simulations=args.simulations, fee_per_trade_r=fee)
        if "error" in result:
            print(f"\n{result['error']}")
            return
        # 终端版式报告（复刻级纯文本风格）
        text = render_terminal_report(result, initial_capital=args.capital,
                                      risk_per_trade=args.risk_pct,
                                      display_range=args.display_range,
                                      years=years)
        print("\n" + text)
        # 版式文本存档（供查看/归档）
        report_path = Path("产出/输出/monte_carlo_report.txt")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
        path = plot_simulation(result, save=True)
        print(f"  数据: {src_desc}")
        print(f"  置信区间: {np.percentile(result['final_equities'], 2.5):.1f}R ~ "
              f"{np.percentile(result['final_equities'], 97.5):.1f}R")
        print(f"  图片已保存: {path}")

    elif args.action in ("sim-open", "sim-check", "sim-stats"):
        # R-009 模块3：模拟交易流水线（模拟/小仓验证阶段）
        from 分析决策.跟踪.sim_trading import sim_check, sim_open, sim_stats
        if args.action == "sim-open":
            if not args.code or args.price <= 0 or args.stop <= 0:
                print("用法: track sim-open --code 600777 --price 8.5 --stop 8.0 [--grade B] [--name xxx]")
                return
            print("\n" + sim_open(args.code, args.price, args.stop,
                                  grade=args.grade, name=args.name))
        elif args.action == "sim-check":
            print("\n" + sim_check())
        else:
            print("\n" + sim_stats())


def cmd_capital(args):
    """资金管理"""
    from 分析决策.风控.capital import get_capital, max_risk_per_trade, set_capital
    if args.action == "show":
        cap = get_capital()
        risk = max_risk_per_trade()
        print("\n=== 资金状况 ===\n")
        print(f"  总资金: ¥{cap:.0f}")
        print("  单笔风险比例: 1.5%")
        print(f"  单笔最大风险: ¥{risk:.0f}")
        print("  建议修改: python main.py capital set <金额>")
    elif args.action == "set" and args.amount:
        set_capital(args.amount)
        risk = max_risk_per_trade()
        print(f"\n资金已更新为 ¥{args.amount:.0f}")
        print(f"  单笔最大风险: ¥{risk:.0f}")


def main():
    parser = argparse.ArgumentParser(
        description="交易部 - A股技术面选股",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py list                 查看全部A股
  python main.py list --preview 10     预览前10只
  python main.py kline 000001         查看平安银行K线
  python main.py kline 000001 --name "平安银行"
  python main.py scan                  全市场扫描（默认示例策略）
  python main.py scan --strategy demo  使用示例策略
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # list
    list_parser = subparsers.add_parser("list", help="查看A股列表")
    list_parser.add_argument("--preview", type=int, default=10,
                           help="预览前N只 (默认10)")

    # kline
    kline_parser = subparsers.add_parser("kline", help="查看单只股票K线")
    kline_parser.add_argument("code", type=str, help="股票代码 (如 000001)")
    kline_parser.add_argument("--name", type=str, default="", help="股票名称")
    kline_parser.add_argument("--no-save", action="store_true",
                            help="不保存图片，仅显示")

    # scan
    scan_parser = subparsers.add_parser("scan", help="全市场扫描选股")
    scan_parser.add_argument("--strategy", type=str, default="demo",
                           help="策略名称 (默认: demo)")
    scan_parser.add_argument("--mode", type=str, default="normal",
                           choices=["normal", "prebreak"],
                           help="扫描模式: normal=标准6条件, prebreak=预突破5条件(挂条件单用)")
    scan_parser.add_argument("--max-price", type=float, default=None,
                           help="价格上限过滤（元，R-009 小资金整手约束；如 50 只选 ≤50 元）")

    # diagnose
    diag_parser = subparsers.add_parser("diagnose", help="诊断单只股票各策略条件")
    diag_parser.add_argument("code", type=str, help="股票代码 (如 000001)")
    diag_parser.add_argument("--strategy", type=str, default="zuanqian_strategy",
                           help="策略模块名 (默认: zuanqian_strategy)")

    # market-review（R-008 市场环境复盘）
    subparsers.add_parser("market-review", help="市场环境复盘（指数一致性/周期/仓位建议）")

    # rcurve（R 值曲线，2026-08-06 老板拍板：R = 盈亏÷单笔风险，累计R曲线跟踪策略）
    rcurve_parser = subparsers.add_parser("rcurve", help="R 值曲线（每笔交易 R 跟踪与统计）")
    rcurve_parser.add_argument("args", nargs=argparse.REMAINDER,
                               help="转发给 r_curve 子命令（record/record-r/list/stats/plot/delete）")

    # track
    track_parser = subparsers.add_parser("track", help="交易记录管理")
    track_parser.add_argument("action", type=str, nargs="?",
                            choices=["list", "add", "equity", "monte-carlo",
                                     "sim-open", "sim-check", "sim-stats"],
                            default="list", help="操作")
    track_parser.add_argument("--code", type=str, default="", help="sim-open: 股票代码")
    track_parser.add_argument("--price", type=float, default=0, help="sim-open: 进场价")
    track_parser.add_argument("--stop", type=float, default=0, help="sim-open: 止损价")
    track_parser.add_argument("--grade", type=str, default="", help="sim-open: 进场评级")
    track_parser.add_argument("--name", type=str, default="", help="sim-open: 股票名称")
    track_parser.add_argument("--simulations", type=int, default=10000,
                            help="蒙特卡洛模拟次数")
    track_parser.add_argument("--capital", type=float, default=100000.0,
                            help="初始资金（版式报告口径，默认 100000）")
    track_parser.add_argument("--risk-pct", type=float, default=0.01,
                            help="每笔风险比例（版式报告口径，默认 1%）")
    track_parser.add_argument("--display-range", type=float, default=100.0,
                            help="显示范围（中间 X%，默认 100.0）")
    track_parser.add_argument("--source", type=str, choices=["journal", "backtest"],
                            default="journal",
                            help="蒙特卡洛数据源（默认 journal，无记录自动回退回测）")
    track_parser.add_argument("--mode", type=str, choices=["normal", "prebreak"],
                            default="prebreak", help="回测数据源模式（默认 prebreak）")
    track_parser.add_argument("--hold", type=str, choices=["5d", "10d", "20d"],
                            default="20d", help="回测数据源观察窗（默认 20d）")
    track_parser.add_argument("--samples", type=int, default=500,
                            help="回测数据源抽样笔数（默认 500）")
    track_parser.add_argument("--signals", type=str,
                            default="项目/output/backtest/20230701_20260804/signals.csv",
                            help="回测数据源 signals.csv 路径")
    track_parser.add_argument("--years", type=float, default=None,
                            help="年化收益率年数（默认回测信号跨度自动计算）")

    # capital
    cap_parser = subparsers.add_parser("capital", help="资金管理")
    cap_parser.add_argument("action", type=str, nargs="?",
                          choices=["show", "set"], default="show", help="操作")
    cap_parser.add_argument("amount", type=float, nargs="?", default=0,
                          help="设置资金金额")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "kline":
        cmd_kline(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "diagnose":
        cmd_diagnose(args)
    elif args.command == "track":
        cmd_track(args)
    elif args.command == "capital":
        cmd_capital(args)
    elif args.command == "market-review":
        from 分析决策.市场环境.market_review import main as market_review_main
        raise SystemExit(market_review_main())
    elif args.command == "rcurve":
        from 分析决策.跟踪.r_curve import main as r_curve_main
        raise SystemExit(r_curve_main(sys.argv[2:]))
    else:
        parser.print_help()


if __name__ == "__main__":
    # Windows GBK 终端编码保护
    import sys as _sys
    if hasattr(_sys.stdout, 'reconfigure'):
        try:
            _sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    main()
