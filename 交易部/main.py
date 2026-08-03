#!/usr/bin/env python3
"""交易部 - 入口

用法:
    python main.py list             查看A股列表
    python main.py kline <代码>     查看单只股票K线
    python main.py scan [--strategy <策略名>]  全市场扫描
"""
import sys
import argparse
from datetime import datetime

# 确保项目根目录在路径中
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import OUTPUT_DIR, KLINE_YEARS
from config.stock_pool import get_all_stocks
from analysis.scanner import scan
from analysis.reporter import print_results, save_results, plot_kline
from strategy.base import BaseStrategy


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
        module = importlib.import_module(f"strategy.samples.{name}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaseStrategy)
                    and attr is not BaseStrategy):
                return attr()
        return None
    except (ImportError, AttributeError):
        return None


def cmd_scan(args):
    """全市场扫描"""
    strategy = _load_strategy(args.strategy)
    if strategy is None:
        print(f"❌ 未找到策略: '{args.strategy}'")
        print(f"可用策略: demo_strategy, zuanqian_strategy (钻潜标准模式, 钻潜VCP模式)")
        sys.exit(1)

    print(f"\n[SCAN] 全市场扫描 | 策略: {strategy.name}")
    print(f"{'-' * 50}")
    print(f"  说明: {strategy.description}")
    print(f"  参数: {strategy.get_params()}")
    print()

    # 执行扫描
    results = scan(strategy, mode=getattr(args, "mode", "normal"))

    # 输出结果
    if results:
        print_results(results, mode=getattr(args, "mode", "normal"))
        save_results(results)
    else:
        print("\n当前没有符合条件的股票")

    # 纪律报告（每次scan自动输出）
    try:
        from risk.trade_guardian import discipline_report
        print()
        print(discipline_report())
    except ImportError:
        pass


def cmd_diagnose(args):
    """单只股票诊断：逐步检测策略条件"""
    from data.fetcher import get_daily_kline
    from analysis.indicators import all_indicators

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
    print(f"【附加信息】")
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
            from risk.capital import calc_lots as _cl
            print(f"  建议手数: {_cl(pr.get('risk_per_share',1))}")


def cmd_track(args):
    """交易记录管理"""
    from tracker.trade_journal import get_all_trades, trade_stats, format_stats
    from tracker.equity_curve import plot_equity_curve
    from tracker.monte_carlo import simulate, plot_simulation

    import numpy as np

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
        from risk.position import TradeRecord

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
            from data.fetcher import get_daily_kline as _gdk
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
        r_mul = (exit_price - entry_price) / risk_per if risk_per > 0 else 0

        # 计算交易成本
        from risk.capital import calc_trade_fee
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
        from tracker.trade_journal import add_trade as _add
        _add(trade)
        print(f"\n已记录: {symbol} {round(r_mul_real,2)}R (含手续费¥{total_fee:.1f})")

    elif args.action == "equity":
        path = plot_equity_curve(save=True)
        if path:
            trades = get_all_trades()
            stats = trade_stats(trades)
            print(f"\n=== 资金曲线 ===\n")
            print(f"  图片已保存: {path}")
            print(f"\n{format_stats(stats)}")
        else:
            print("\n暂无交易记录")

    elif args.action == "monte-carlo":
        from tracker.trade_journal import get_all_trades
        trades = get_all_trades()
        result = simulate(trades, n_simulations=args.simulations)
        if "error" in result:
            print(f"\n{result['error']}")
            return
        path = plot_simulation(result, save=True)
        print(f"\n=== 蒙特卡洛模拟 ===\n")
        print(f"  模拟次数: {result['n_simulations']}")
        print(f"  基于 {result['n_trades']} 笔历史交易")
        print(f"  平均R: {result['avg_r']:.3f}")
        print(f"  R标准差: {result['std_r']:.2f}")
        print(f"  盈利概率: {result['prob_profit']:.1%}")
        fin = result['final_equities']
        print(f"  95%置信区间: {np.percentile(fin, 2.5):.1f}R ~ {np.percentile(fin, 97.5):.1f}R")
        print(f"  图片已保存: {path}")


def cmd_capital(args):
    """资金管理"""
    from risk.capital import get_capital, set_capital, max_risk_per_trade
    if args.action == "show":
        cap = get_capital()
        risk = max_risk_per_trade()
        print(f"\n=== 资金状况 ===\n")
        print(f"  总资金: ¥{cap:.0f}")
        print(f"  单笔风险比例: 1.5%")
        print(f"  单笔最大风险: ¥{risk:.0f}")
        print(f"  建议修改: python main.py capital set <金额>")
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

    # diagnose
    diag_parser = subparsers.add_parser("diagnose", help="诊断单只股票各策略条件")
    diag_parser.add_argument("code", type=str, help="股票代码 (如 000001)")
    diag_parser.add_argument("--strategy", type=str, default="zuanqian_strategy",
                           help="策略模块名 (默认: zuanqian_strategy)")

    # track
    track_parser = subparsers.add_parser("track", help="交易记录管理")
    track_parser.add_argument("action", type=str, nargs="?",
                            choices=["list", "add", "equity", "monte-carlo"],
                            default="list", help="操作")
    track_parser.add_argument("--simulations", type=int, default=10000,
                            help="蒙特卡洛模拟次数")

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
