#!/usr/bin/env python3
"""回测独立项目 CLI（时光机）——把策略 V2 放回历史行情验证胜率/盈亏比

用法:
    python backtest/main.py run --start 20240601 --end 20260701 --mode both
    python backtest/main.py verify --signals output/backtest/.../signals.csv --samples 20
"""
import argparse
import os
import sys

# 确保交易部根目录在路径中（沿用 main.py:13-15 模式）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from backtest.engine import BacktestEngine
from backtest.params import BacktestParams
from backtest.report import write_report, write_signals_csv
from backtest.verify import verify_csv, verify_engine_output


def _out_dir(params: BacktestParams) -> Path:
    """输出目录：output/backtest/<start>_<end>/（None 段记为 full）"""
    if params.output_dir:
        return Path(params.output_dir)
    root = Path(__file__).resolve().parent.parent
    seg = f"{params.start or 'full'}_{params.end or 'full'}"
    return root / "output" / "backtest" / seg


def cmd_run(args) -> int:
    holds = [int(h) for h in args.hold]
    grades = list(args.grade)
    params = BacktestParams(
        start=args.start, end=args.end, strategy=args.strategy, mode=args.mode,
        interval=args.interval, holds=holds, grades=grades,
        codes=list(args.codes) if args.codes else None,
        max_workers=args.max_workers, output_dir=args.output_dir,
        verify_samples=args.verify_samples,
        recompute_each_window=args.recompute_each_window,
    )
    try:
        params.validate()
    except ValueError as e:
        print(f"❌ 参数错误: {e}")
        return 1

    print(f"\n[BACKTEST] 回测启动 | 区间 {params.start or '缓存起点'}~{params.end or '缓存终点'} "
          f"| 模式 {params.mode} | 步长 {params.interval} | hold {'/'.join(str(h) for h in holds)}d "
          f"| 评级 {'/'.join(params.grades)}")
    print(f"  {params.codes or '全市场股票池'} | 线程 {params.max_workers}"
          + (" | 严格逐窗重算（对照）" if params.recompute_each_window else ""))

    engine = BacktestEngine(params)
    result = engine.run()

    # 策略信息（params.json 快照用）
    strategy_info = {"name": engine.strategy.name, "params": engine.strategy.strategy.get_params()}

    out_dir = _out_dir(params)
    out_dir.mkdir(parents=True, exist_ok=True)
    signals_path = out_dir / "signals.csv"
    report_path = out_dir / "report.md"
    params_path = out_dir / "params.json"

    write_signals_csv(signals_path, result.records, params.holds)
    write_report(report_path, result.records, _buckets(result.records, params), params,
                 meta={"processed": result.processed, "skipped": result.skipped})
    params.save_snapshot(str(params_path), strategy_info)

    print(f"\n[BACKTEST] 完成 | 股票 {result.processed} 只（跳过 {result.skipped}）| 信号 {len(result.records)} 笔")
    if result.failed_codes:
        print(f"  ⚠ 失败: {result.failed_codes[:5]}")
    print(f"  signals.csv → {signals_path}")
    print(f"  report.md   → {report_path}")
    print(f"  params.json → {params_path}")

    # 自动验收自检
    if params.verify_samples and result.records:
        v = verify_engine_output(result.records, samples=params.verify_samples, seed=42)
        print(f"\n[VERIFY] 自检 {v['samples_checked']} 笔 | 同源一致: {'✅' if v['same_source_ok'] else '❌'} | "
              f"价格原值一致: {'✅' if v['price_ok'] else '❌'}")
        if not v["same_source_ok"] or not v["price_ok"]:
            for m in v["mismatches"][:5]:
                print(f"  - {m}")
            for m in v["price_issues"][:5]:
                print(f"  - {m}")
    return 0


def _buckets(records, params):
    """统计分桶（report 需要）"""
    from backtest.stats import group_stats
    return group_stats(records, params.holds)


def cmd_verify(args) -> int:
    path = Path(args.signals)
    if not path.exists():
        print(f"❌ 信号文件不存在: {path}")
        return 1
    v = verify_csv(path, samples=args.samples, seed=args.seed)
    print(f"\n[VERIFY] {path}")
    print(f"  抽查 {v['checked']} 行 | 收盘价原值一致: {'✅' if v['ok'] else '❌'}")
    for issue in v["issues"]:
        print(f"  - {issue}")
    return 0 if v["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python backtest/main.py",
        description="回测独立项目（时光机）：策略 V2 历史验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python backtest/main.py run --start 20240601 --end 20260701 --mode both
  python backtest/main.py run --codes 000001 600000 --start 20250101 --end 20250601 --verify-samples 20
  python backtest/main.py verify --signals output/backtest/full_full/signals.csv --samples 20 --seed 42""",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="执行回测")
    run_p.add_argument("--start", default=None, help="信号日期下限 YYYYMMDD（只过滤记录）")
    run_p.add_argument("--end", default=None, help="信号日期上限 YYYYMMDD（只过滤记录）")
    run_p.add_argument("--strategy", default="zuanqian_strategy", help="策略注册名（默认 zuanqian_strategy）")
    run_p.add_argument("--mode", default="normal", choices=["normal", "prebreak", "both"],
                       help="normal=6条件已突破 / prebreak=5条件预突破 / both=同窗双评级对比")
    run_p.add_argument("--interval", type=int, default=5, help="信号日步长（交易日，默认5）")
    run_p.add_argument("--hold", nargs="+", default=["5", "10", "20"], help="观察窗多值（默认 5 10 20）")
    run_p.add_argument("--grade", nargs="+", default=["S", "A", "B"], help="记录哪些评级（默认 S A B）")
    run_p.add_argument("--codes", nargs="+", default=None, help="只跑指定代码（冒烟/验收）")
    run_p.add_argument("--max-workers", type=int, default=5, help="线程数（默认5）")
    run_p.add_argument("--output-dir", default=None, help="覆盖默认输出目录")
    run_p.add_argument("--verify-samples", type=int, default=0, help="run 后自动验收自检抽样笔数（0=关）")
    run_p.add_argument("--recompute-each-window", action="store_true",
                       help="严格逐窗重算指标（对照验证慢路径）")

    verify_p = sub.add_parser("verify", help="验收自检（同源/收盘价抽查）")
    verify_p.add_argument("--signals", required=True, help="signals.csv 路径")
    verify_p.add_argument("--samples", type=int, default=20, help="抽查行数（默认20）")
    verify_p.add_argument("--seed", type=int, default=42, help="随机种子（默认42）")
    return parser


def main() -> int:
    # Windows GBK 终端编码保护（沿用 main.py 模式）
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_parser().parse_args()
    if args.command == "run":
        return cmd_run(args)
    if args.command == "verify":
        return cmd_verify(args)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
