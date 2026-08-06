"""每日盘后增量主入口（T-017 P3 · P2 报告第七节建议 1）

用法（在交易部门根目录执行）：
    python -m 数据基础.duckdb.update_daily [--db 路径] [--workers 8] [--limit N] [--only-failed]
                                             [--window 15] [--no-new] [--xdxr-check] [--recon]

流程：
  1. 打开 duckdb（写模式），确保建表
  2. 从库内已有 symbol 列表确定增量对象；mootdx 列表差集 = 新股 → 全量拉取
  3. 每只：增量窗口拉日线（默认最新 15 条）+ 全量重拉 xdxr → upsert（PK 去重幂等）
     - xdxr 每只全量重插：覆盖式修复通达信漏记/补录（300093 类问题兜底手段之一）
  4. 8 线程 + 每只重试 3 次 + 退避 2/5/10s + 尝试间切换服务器（P2 模式）
  5. 断点续传：state.json 记录每只 daily/xdxr 完成状态，重启跳过 done、续跑 failed
  6. 完成校验：无重复 + 尾部缺口报告；可接 --xdxr-check（除权完整性）与 --recon（对账）

P3 依据：老板 2026-08-05 确认执行 T-017 P3；P2 全量报告第七节。
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from 数据基础.duckdb import fetch as F
from 数据基础.duckdb import store as S
from 数据基础.duckdb.config import (
    DB_PATH,
    INCR_OFFSET,
    INTER_STOCK_SLEEP,
    RUNTIME_DIR,
    SERVERS,
    WORKERS,
)

sys.stdout.reconfigure(encoding="utf-8")

STATE_PATH = RUNTIME_DIR / "state.json"
LOG_PATH = RUNTIME_DIR / "progress.log"
SUMMARY_PATH = RUNTIME_DIR / "summary.json"

_lock = threading.Lock()          # 落库/状态写互斥
_progress_lock = threading.Lock()  # 进度日志互斥


def log(msg):
    """stdout + 进度日志（utf-8）"""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    """读取断点续传状态；跨天失效（2026-08-07 修复）

    state 记录每只 daily/xdxr 完成状态供「同一天内」续传（重启跳过 done、
    续跑 failed）。此前 done 标记永不过期 → 次日增量全部被跳过（数据停更，
    08-06/08-07 事故根源）。现在按 _meta.run_date 判定：非今天 → 视为空，
    当天重跑仍跳过 done（幂等 upsert，跨天自动全池重拉最新窗口）。
    """
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        today = time.strftime("%Y-%m-%d")
        if state.get("_meta", {}).get("run_date") != today:
            return {}
        return state
    return {}


def save_state(state):
    """原子写 state.json；Windows 上 os.replace 偶发被占用（防病毒扫描等）→ 重试"""
    tmp = STATE_PATH.with_suffix(".json.tmp")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=0)
            os.replace(tmp, STATE_PATH)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.2 * (attempt + 1))


def update_state(state, sym, **kw):
    rec = state.get(sym, {})
    rec.update(kw)
    state[sym] = rec
    save_state(state)


def main():
    ap = argparse.ArgumentParser(description="每日盘后增量：mootdx → duckdb（daily/xdxr）")
    ap.add_argument("--db", default=str(DB_PATH), help="duckdb 路径（默认数据基础/行情数据/t017_p2.duckdb）")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--window", type=int, default=INCR_OFFSET, help="增量窗口：每次拉最新 N 条日线")
    ap.add_argument("--limit", type=int, default=0, help="冒烟测试：只处理前 N 只")
    ap.add_argument("--only-failed", action="store_true", help="只重试上次 failed 的股票")
    ap.add_argument("--no-new", action="store_true", help="跳过新股检测（差集全量拉取）")
    ap.add_argument("--retry-permanent", action="store_true", help="强制重试空表永久标记的股票")
    ap.add_argument("--xdxr-check", action="store_true", help="增量完成后跑除权完整性校验")
    ap.add_argument("--recon", action="store_true", help="增量完成后跑日终对账（新浪抽样）")
    args = ap.parse_args()

    con = S.open_db(args.db)
    t0 = time.time()

    # ── 1. 确定增量对象 ──
    known = set(S.known_symbols(con))
    log(f"库内已有 {len(known)} 只")
    state = load_state()
    # 断点续传日期戳（2026-08-07 修复）：_meta.run_date = 本次运行日期，
    # load_state 据此判定跨天失效；同日重跑（失败重试）仍跳过 done。
    state["_meta"] = {"run_date": time.strftime("%Y-%m-%d")}

    if args.only_failed:
        failed = [s for s, r in state.items() if r.get("daily") == "failed" or r.get("xdxr") == "failed"]
        todo = [s for s in known if s in failed]
    else:
        todo = sorted(known)
    if args.limit > 0:
        todo = todo[:args.limit]

    # 新股检测：mootdx 全列表 − 库内 − 已确认空表 = 新股（全量拉取）
    # 空表永久跳过：通达信未收录的股票（如 P2 失败 7 只）每次重试 3 次纯浪费，
    # 标记 permanent 后跳过，待 sina_backfill 补齐入库后自然纳入增量。
    new_stocks = []
    if not args.no_new and not args.only_failed:
        try:
            cli = F.connect(SERVERS[0])
            try:
                all_codes = F.fetch_stock_list(cli)
            finally:
                cli.close()
            skipped_perm = {s for s, r in state.items()
                            if r.get("daily") == "failed" and r.get("permanent")
                            and not args.retry_permanent}
            candidates = sorted(set(all_codes) - known - skipped_perm)
            if args.limit > 0:
                candidates = candidates[:args.limit]
            new_stocks = candidates
            if new_stocks:
                log(f"发现新股 {len(new_stocks)} 只: {new_stocks[:10]}{'...' if len(new_stocks) > 10 else ''}"
                    f"{' (已跳过空表永久标记 ' + str(len(skipped_perm)) + ' 只)' if skipped_perm else ''}")
                todo += new_stocks
            elif skipped_perm:
                log(f"新股检测: 无新发现（跳过空表永久标记 {len(skipped_perm)} 只）")
        except Exception as e:  # noqa: BLE001 - 网络/列表接口异常，跳过新股检测不影响增量
            log(f"新股检测失败(跳过，不影响增量): {e}")
    elif args.only_failed:
        log("only-failed 模式跳过新股检测")

    # 待拉列表：daily 或 xdxr 未 done
    todo = [s for s in todo
            if not (state.get(s, {}).get("daily") == "done" and state.get(s, {}).get("xdxr") == "done")]
    log(f"待拉 {len(todo)} 只 workers={args.workers} window={args.window} db={args.db}")
    # 统计初始化置于分支外（质检 B1 修复）：全部 done 时 todo 为空（盘后增量
    # 第二天正常运行的常见场景），也须走完校验与 summary 写出，stats 不能缺位
    stats = {"done": 0, "failed": 0, "written_rows": 0, "written_xdxr_rows": 0}
    if not todo:
        log("无待拉任务，退出")
    else:
        # ── 2. 并发增量 ──
        server_map = {s: (SERVERS[i % len(SERVERS)], SERVERS[(i + 1) % len(SERVERS)])
                      for i, s in enumerate(todo)}

        def do_one(sym):
            server_a, server_b = server_map[sym]
            recent = sym not in new_stocks          # 新股走全量，其余走增量窗口
            daily, xdxr, err = F.fetch_one(sym, server_a, server_b, recent=recent,
                                           offset=args.window)
            if daily is None:
                with _lock:
                    # 通达信未收录（日线空表）→ 永久跳过标记，下次增量不再重试，
                    # 待 sina_backfill 补齐入库后自然恢复（--retry-permanent 可强制重试）
                    permanent = err is not None and "空表" in str(err)
                    update_state(state, sym, daily="failed", err=str(err)[:200],
                                 permanent=permanent)
                    stats["failed"] += 1
                return sym, "failed", str(err)[:100]

            n = len(daily)
            nx = 0
            with _lock:
                S.upsert_daily(con, sym, daily)
                nx = S.upsert_xdxr(con, sym, xdxr)
                stats["done"] += 1
                stats["written_rows"] += n
                stats["written_xdxr_rows"] += nx
                update_state(state, sym, daily="done", xdxr="done" if nx else "missing",
                             rows=n, xdxr_records=nx,
                             latest=str(daily["date"].max()),
                             err=None)
            time.sleep(INTER_STOCK_SLEEP)
            return sym, "done", n

        n_done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(do_one, s): s for s in todo}
            for fut in as_completed(futures):
                try:
                    _, st, _ = fut.result()
                    n_done += 1
                    if n_done % 50 == 0 or st == "failed":
                        el = time.time() - t0
                        with _progress_lock:
                            log(f"进度 {n_done}/{len(todo)} done={stats['done']} failed={stats['failed']} "
                                f"耗时{el:.0f}s")
                except Exception as e:  # noqa: BLE001 - 线程任务收集，兜底所有未知异常
                    with _lock:
                        stats["failed"] += 1
                    n_done += 1
                    log(f"线程异常 {e}")

    # ── 3. 完成校验：无重复 + 尾部缺口 ──
    dup = S.check_no_duplicate(con)
    gaps = S.check_tail_gaps(con)
    log(f"校验: 重复行 daily={dup['daily_dups']} xdxr={dup['xdxr_dups']} | "
        f"库最新 {gaps['db_latest']} 落后>5天 {gaps['behind_over_days']} 只")
    if gaps["behind_over_days"]:
        log("尾部落后明细(前20): " + ", ".join(
            f"{s}({b}天)" for s, _, b in gaps["top_behind"]))

    total = time.time() - t0
    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": args.db,
        "todo": len(todo),
        "new_stocks": new_stocks,
        "done": stats.get("done", 0),
        "failed": stats.get("failed", 0),
        "written_rows": stats.get("written_rows", 0),
        "written_xdxr_rows": stats.get("written_xdxr_rows", 0),
        "total_seconds": round(total, 1),
        "dup_check": dup,
        "tail_gap": {"db_latest": gaps["db_latest"], "behind_over_days": gaps["behind_over_days"]},
    }
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    con.close()
    log(f"完成: done={stats.get('done', 0)} failed={stats.get('failed', 0)} "
        f"写入日线{stats.get('written_rows', 0)}条(含窗口重写) 写入除权{stats.get('written_xdxr_rows', 0)}条 "
        f"总耗时{total:.0f}s")

    # ── 4. 可选后续 ──
    if args.xdxr_check:
        from 数据基础.duckdb.xdxr_check import run_check
        run_check(args.db, verbose=True)
    if args.recon:
        from 数据基础.duckdb.recon import main as recon_main
        recon_main(["--db", args.db])


if __name__ == "__main__":
    main()
