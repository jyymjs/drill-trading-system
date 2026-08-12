"""R-080 G15 数据截止出场量化（2026-08-13）

统计两段信号集：① 该股数据提前于回测窗口末截止的只数 ② 出场日 = 该股数据末
（数据截止出场）的笔数。当前池（存活股全量覆盖）应均为 0——验证 G15 场景
不存在，无需修改定版；G2 修正（退市股入池）时配套启用保守规则。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "t017_p2.duckdb")


def main() -> int:
    con = duckdb.connect(DB, read_only=True)
    for name, path, win_end in [
            ("定参段", "产出/输出/数据/backtest_calib_2020-2022/signals.csv", "2022-12-31"),
            ("验证段", "产出/输出/数据/backtest_final_20260806/signals.csv", "2026-07-31")]:
        sig = pd.read_csv(os.path.join(os.path.dirname(__file__), "..", "..", path),
                          encoding="utf-8-sig", dtype={"code": str})
        trig = sig[sig["triggered_20d"] == 1]
        at_end = early_end = n_early = 0
        for code, g in trig.groupby("code"):
            md = con.execute("SELECT MAX(date) FROM daily WHERE symbol=?",
                             [code]).fetchone()[0]
            if md is None:
                continue
            md = str(md)[:10]
            early = md < win_end
            if early:
                n_early += 1
            for _, r in g.iterrows():
                ed = r["exit_date_20d"]
                if isinstance(ed, str) and ed[:10] == md:
                    at_end += 1
                    if early:
                        early_end += 1
        print(f"{name}: 触发 {len(trig)} | 数据提前截止股 {n_early} 只 | "
              f"出场日=数据末 {at_end} 笔（其中提前截止 {early_end}）", flush=True)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
