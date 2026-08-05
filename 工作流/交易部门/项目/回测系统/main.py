#!/usr/bin/env python3
"""回测独立项目 CLI（时光机）——把策略 V2 放回历史行情验证胜率/盈亏比

用法:
    python 回测系统/main.py run --start 20240601 --end 20260701 --mode both
    python 回测系统/main.py verify --signals output/backtest/.../signals.csv --samples 20
"""
import argparse
import os
import sys
from dataclasses import replace

# 确保交易部根目录在路径中（2026-08-04 修复：重组后需加交易部根层级）
_HERE = os.path.dirname(os.path.abspath(__file__))   # 项目/回测系统
sys.path.insert(0, os.path.dirname(_HERE))            # 项目/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # 交易部根

from pathlib import Path

from 回测系统.engine import BacktestEngine
from 回测系统.params import BacktestParams
from 回测系统.report import write_report, write_signals_csv
from 回测系统.verify import verify_csv, verify_engine_output


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
        dl_cands=args.dl_cands,
        moving_stop=args.moving_stop,
        env_gate=not args.no_env_gate, env_drop_pct=args.env_drop_pct,
        env_mode=args.env_mode, env_index=args.env_index,
        volume_filter=not args.no_volume_filter, min_amount=args.min_amount,
        vol_window=args.vol_window,
        prbook_gate=not args.no_prbook_gate,
        sentiment_gate=not args.no_sentiment_gate, sent_threshold=args.sent_threshold,
        missing_sentiment=args.missing_sentiment,
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
          + (" | 严格逐窗重算（对照）" if params.recompute_each_window else "")
          + (" | C1 财报日避让" if params.prbook_gate else " | 无 C1 财报日避让（对照）"))

    engine = BacktestEngine(params)
    if params.dl_cands:
        cands = tuple(int(x) for x in params.dl_cands.split(","))
        engine.strategy.strategy.DL_CANDS = cands
        print(f"  DL 候选根数覆盖: S={cands[0]} A={cands[1]} B={cands[2]}（策略默认 90/70/60）")
    result = engine.run()

    # 策略信息（params.json 快照用）
    strategy_info = {"name": engine.strategy.name, "params": engine.strategy.strategy.get_params()}

    out_dir = _out_dir(params)
    out_dir.mkdir(parents=True, exist_ok=True)
    signals_path = out_dir / "signals.csv"
    report_path = out_dir / "report.md"
    params_path = out_dir / "params.json"

    write_signals_csv(signals_path, result.records, params.holds)

    # D2 2倍成本压力测试：同源重跑（佣金+印花税+滑点全 ×2，方案 D 类 2026-08-05 老板拍板）
    stress_records = None
    if params.enable_cost:
        stress_params = replace(params, cost_multiplier=2.0)
        stress_engine = BacktestEngine(stress_params, provider=engine.provider,
                                       strategy=engine.strategy, risk=engine.risk)
        print("  [D2] 2倍成本压力重跑（佣金万2.6+印花税万10+滑点翻倍万2）…")
        stress_result = stress_engine.run()
        stress_records = stress_result.records
        print(f"  [D2] 完成 | 信号 {len(stress_records)} 笔")

    write_report(report_path, result.records, _buckets(result.records, params), params,
                 meta={"processed": result.processed, "skipped": result.skipped,
                       "gate_counts": result.gate_counts},
                 stress_records=stress_records)
    params.save_snapshot(str(params_path), strategy_info)

    # 蒙特卡洛版式报告（自动生成，复用 分析决策/跟踪/monte_carlo 渲染器）
    try:
        from 分析决策.跟踪.monte_carlo import (
            load_backtest_r_series,
            load_backtest_years,
            render_terminal_report,
            simulate,
        )
        mc_years = load_backtest_years(str(signals_path))
        mc_trades = load_backtest_r_series(str(signals_path), mode="prebreak",
                                           hold="20d", sample_n=500)
        mc_res = simulate(mc_trades, n_simulations=2000, fee_per_trade_r=0.0)
        mc_text = render_terminal_report(mc_res, years=mc_years)
        mc_path = out_dir / "monte_carlo.txt"
        mc_path.write_text(mc_text + "\n", encoding="utf-8")
        print(f"  monte_carlo.txt → {mc_path}")
    except Exception as e:
        print(f"  ⚠ 蒙特卡洛报告未生成: {e}")

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
    from 回测系统.stats import group_stats
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
        prog="python 回测系统/main.py",
        description="回测独立项目（时光机）：策略 V2 历史验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python 回测系统/main.py run --start 20240601 --end 20260701 --mode both
  python 回测系统/main.py run --codes 000001 600000 --start 20250101 --end 20250601 --verify-samples 20
  python 回测系统/main.py verify --signals output/backtest/full_full/signals.csv --samples 20 --seed 42""",
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
    run_p.add_argument("--dl-cands", default=None, help="覆盖策略 DL 候选根数 S,A,B（如 120,90,70；默认 90,70,60）")
    run_p.add_argument("--codes", nargs="+", default=None, help="只跑指定代码（冒烟/验收）")
    run_p.add_argument("--max-workers", type=int, default=5, help="线程数（默认5）")
    run_p.add_argument("--output-dir", default=None, help="覆盖默认输出目录")
    run_p.add_argument("--verify-samples", type=int, default=0, help="run 后自动验收自检抽样笔数（0=关）")
    run_p.add_argument("--recompute-each-window", action="store_true",
                       help="严格逐窗重算指标（对照验证慢路径）")
    run_p.add_argument("--moving-stop", action="store_true",
                       help="C5 移动止损（2026-08-05 老板拍板）：持仓中每确认新结构低点→止损上移低点×0.99；默认关（先回测对照后上线）")
    run_p.add_argument("--no-env-gate", action="store_true",
                       help="关闭 B1 环境闸门（2026-08-05 第3波，默认开=回测验证后正式接入）")
    run_p.add_argument("--env-drop-pct", type=float, default=-2.0,
                       help="指数当日跌幅阈值（%%，默认 -2.0；建议值，回测验证）")
    run_p.add_argument("--env-mode", default="veto", choices=["veto", "downgrade"],
                       help="环境不利处理：veto=一票否决（默认）/ downgrade=降一档")
    run_p.add_argument("--env-index", default="上证指数",
                       help="主闸门指数（默认上证指数；可选 深证成指/创业板指）")
    run_p.add_argument("--no-volume-filter", action="store_true",
                       help="关闭 C3 量能硬过滤（2026-08-05 第3波，默认开=回测验证后正式接入）")
    run_p.add_argument("--no-prbook-gate", action="store_true",
                       help="关闭 C1 财报日避让（2026-08-05 老板拍板，默认开=正式接入；对照实验用）")
    run_p.add_argument("--min-amount", type=float, default=5000.0,
                       help="日均成交额阈值（万元，默认 5000；建议值，回测验证）")
    run_p.add_argument("--vol-window", type=int, default=5,
                       help="均额窗口（交易日，默认5，含信号日）")
    run_p.add_argument("--no-sentiment-gate", action="store_true",
                       help="关闭 C4 情绪闸门（涨跌家数，2026-08-05 老板拍板，默认开）")
    run_p.add_argument("--sent-threshold", type=float, default=70.0,
                       help="全市场下跌家数占比阈值（%%，默认 70；建议值，普跌日实证 2026-05-29=71.4%%）")
    run_p.add_argument("--missing-sentiment", default="pass", choices=["pass", "veto"],
                       help="涨跌家数数据缺失：pass=放行（默认）/ veto=否决")

    verify_p = sub.add_parser("verify", help="验收自检（收盘价抽查；同源重演请用 run --verify-samples）")
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
