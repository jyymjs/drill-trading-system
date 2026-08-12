"""R-080 G2 幸存者偏差量化——退市股 K 线拉取（2026-08-13）

用途：从 baostock 拉取回测窗口（2019-07 后退市）股票型退市股的日 K 线，
入独立 duckdb（delisted 库，不动主库）→ 供信号层重跑量化幸存者偏差。
断点续拉：进度写 delisted_progress.json；可重跑续拉。
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "数据基础", "行情数据", "delisted.duckdb")
PROGRESS = os.path.join(os.path.dirname(__file__), "delisted_progress.json")
START = "2019-07-01"
FIELDS = "date,open,high,low,close,volume,amount"


def main(limit: int | None = None):
    import baostock as bs
    lg = bs.login()
    assert lg.error_code == "0", f"登录失败: {lg.error_msg}"

    # 1. 证券列表 → 回测窗口内退市股票
    rs = bs.query_stock_basic()
    delisted = []
    while rs.next():
        r = rs.get_row_data()  # code, name, ipo, out, type, status
        if r[4] == "1" and r[5] == "0" and r[3] >= "2019-07-01":
            delisted.append({"code": r[0], "name": r[1], "out": r[3]})
    print(f"回测窗口内退市股票: {len(delisted)} 只", flush=True)

    # 2. 断点续拉
    done = set()
    if os.path.exists(PROGRESS):
        done = set(json.load(open(PROGRESS, encoding="utf-8")))
    todo = [d for d in delisted if d["code"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"待拉: {len(todo)} 只（已完成 {len(done)}）", flush=True)

    con = duckdb.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS delisted_daily (
        code VARCHAR, name VARCHAR, out_date VARCHAR,
        date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume DOUBLE, amount DOUBLE)""")

    t0 = time.time()
    ok = fail = 0
    for i, d in enumerate(todo):
        try:
            rs2 = bs.query_history_k_data_plus(
                d["code"], FIELDS, start_date=START, end_date=d["out"],
                frequency="d", adjustflag="2")
            rows = []
            while rs2.next():
                rows.append(rs2.get_row_data())
            if rows:
                # 清洗：退市股停牌日 volume/amount 可能为空串 → 转 '0'
                clean = []
                for r in rows:
                    cr = [v if v not in ("", " ") else "0" for v in r]
                    clean.append((d["code"], d["name"], d["out"], *cr))
                con.executemany(
                    "INSERT INTO delisted_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                    clean)
            done.add(d["code"])
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}] {d['code']} 失败: {e}", flush=True)
        if (i + 1) % 20 == 0:
            json.dump(sorted(done), open(PROGRESS, "w", encoding="utf-8"))
            el = time.time() - t0
            print(f"  进度 {i+1}/{len(todo)} 成功{ok} 失败{fail} 耗时{el:.0f}s "
                  f"预计剩{(len(todo)-i-1)*el/max(i+1,1)/60:.0f}min", flush=True)
    json.dump(sorted(done), open(PROGRESS, "w", encoding="utf-8"))
    n = con.execute("SELECT COUNT(*) FROM delisted_daily").fetchone()[0]
    print(f"完成: 成功{ok} 失败{fail} 总行数 {n}", flush=True)
    bs.logout()
    con.close()


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(lim)
