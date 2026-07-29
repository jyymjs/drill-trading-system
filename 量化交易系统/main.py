#!/usr/bin/env python3
"""量化交易系统 - 入口

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

    print(f"📊 数据: {len(df)} 行 (最新: {df.iloc[-1]['日期']})")
    print(f"💰 收盘: {df.iloc[-1]['收盘']:.2f}")
    print()

    # 预过滤
    pre = strategy.quick_prefilter(df)
    print(f"【预过滤】{'✅ 通过' if pre else '❌ 未通过'}")
    if not pre:
        print("  说明: 快速预过滤已排除此股票（基础条件不满足）")
        return
    print()

    # 计算指标并执行诊断
    needed = strategy.required_indicators
    df = all_indicators(df, needed_cols=needed)
    result = strategy.debug_filter(df)

    print(f"【最终结果】{'✅ 符合条件' if result['match'] else '❌ 不符合条件'}")
    print(f"{'-' * 50}")
    for step_name, detail in result.get("steps", {}).items():
        icon = "✅" if detail.get("passed") else "❌"
        print(f"  {icon} {step_name}")
        print(f"    {detail.get('reason', '')}")
    print()

    # 额外信息
    latest = df.iloc[-1]
    print(f"【附加信息】")
    print(f"  MA20: {latest.get('MA20', 'N/A'):.2f}" if "MA20" in df.columns else "")
    print(f"  量比: {latest.get('VOL_RATIO', 'N/A'):.2f}" if "VOL_RATIO" in df.columns else "")
    if "RSI" in df.columns:
        print(f"  RSI: {latest['RSI']:.1f}")


def main():
    parser = argparse.ArgumentParser(
        description="量化交易系统 - A股技术面选股",
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

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "kline":
        cmd_kline(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "diagnose":
        cmd_diagnose(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
