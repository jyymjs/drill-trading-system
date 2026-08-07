"""D2 复用路径 vs 完整重跑 逐笔对比验证（临时脚本，验证后删除）"""
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 交易部门
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))          # 项目（回测系统 包）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from 回测系统.engine import BacktestEngine, rerun_track_with_cost
from 回测系统.params import BacktestParams
from 回测系统.report import write_signals_csv

def main():
    codes = ["600519", "000001", "600000", "000858", "601318", "600036", "000333", "601899",
             "600900", "000651", "601166", "600030", "000002", "601988", "600104", "000725",
             "601888", "600276", "000568", "601012"]
    params = BacktestParams(start="20230101", end="20260807", mode="prebreak", codes=codes,
                            c23=True, phase_in=True, dn_confirm=1.5, grades=["S"], holds=[20],
                            max_workers=12, output_dir="项目/回测输出/backtest/_d2_verify")
    
    eng = BacktestEngine(params)
    res1 = eng.run()
    print(f"基线: {len(res1.records)} 信号", flush=True)
    signals_path = Path("项目/回测输出/backtest/_d2_verify/signals.csv")
    signals_path.parent.mkdir(parents=True, exist_ok=True)
    write_signals_csv(signals_path, res1.records, params.holds)
    
    p2 = dataclasses.replace(params, cost_multiplier=2.0)
    res2 = BacktestEngine(p2).run()
    print(f"旧路径(全量cost2): {len(res2.records)} 信号", flush=True)
    
    res3 = rerun_track_with_cost(signals_path, params)
    print(f"新路径(复用cost2): {len(res3)} 信号", flush=True)
    
    
    def r_map(records):
        recs = records.records if hasattr(records, "records") else records
        return {r.signal.code + "|" + str(r.signal.date)[:10]: float(r.outcomes[20].r)
                for r in recs if 20 in r.outcomes and r.outcomes[20].r is not None}
    
    
    m2, m3 = r_map(res2), r_map(res3)
    common = set(m2) & set(m3)
    diff = [(k, m2[k], m3[k]) for k in common if abs(m2[k] - m3[k]) > 0.001]
    print(f"对比 {len(common)} 笔触发：差异 >0.001R 的 {len(diff)} 笔", flush=True)
    for k, a, b in diff[:8]:
        print(f"  {k}: 旧{a:.4f} vs 新{b:.4f}", flush=True)
    print("✅ 一致" if not diff else "⚠️ 有差异需查")
    


if __name__ == "__main__":
    main()
