#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周会视频全自动接力处理

一键启动，自动逐个处理所有2024年周会视频。
中断后重新启动会自动跳过已完成的，从断点继续。

用法:
  python autopilot_weekly.py                       # 全部（2路并行）
  python autopilot_weekly.py --parallel 1           # 单路
  python autopilot_weekly.py --dry-run              # 预览不执行
  python autopilot_weekly.py --frame-interval 10    # 自定义帧间隔
"""
import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ── 配置 ──
VIDEO_DIR = r"D:\BaiduNetdiskDownload\路肖南\周会录屏"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
ZHIPU_KEY = "f6de857c9a5f45acafd3be75cdbb7e62.IQQmrpKGiX65q70J"
SCRIPT = Path(__file__).resolve().parent / "extract_knowledge_weekly.py"
LOG_FILE = Path(__file__).resolve().parent.parent / "autopilot_weekly.log"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 视频发现 ──

def find_videos() -> list[Path]:
    """递归扫描所有视频，2024优先，其余按日期排序"""
    base = Path(VIDEO_DIR)
    all_videos = list(base.rglob("*.mp4"))

    # 分组：2024年（根目录平铺） + 历史（子目录）
    v2024 = [v for v in all_videos if v.parent == base]
    v_hist = [v for v in all_videos if v.parent != base]

    v2024.sort()
    v_hist.sort()
    return v2024 + v_hist  # 2024 先跑，再跑历史


def mark_complete(video_path: Path) -> Path:
    """获取对应的期望输出文件路径"""
    return OUTPUT_DIR / f"{video_path.stem}_knowledge.md"


def is_done(video_path: Path) -> bool:
    """检查该视频是否已处理完成"""
    mark = mark_complete(video_path)
    if not mark.exists():
        return False
    # 文件存在且非空
    return mark.stat().st_size > 500


# ── 单视频处理 ──

def process_one(video_path: Path, frame_interval: int = 15) -> dict:
    """处理单个视频（子进程调用 extract_knowledge_weekly.py）"""
    name = video_path.stem
    start = time.time()

    env = os.environ.copy()
    env["ZHIPU_API_KEY"] = ZHIPU_KEY
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")
    env["OMP_NUM_THREADS"] = "4"
    env["MKL_NUM_THREADS"] = "4"

    cmd = [
        sys.executable, str(SCRIPT),
        "--video", str(video_path),
        "--zhipu-api-key", ZHIPU_KEY,
        "--output-dir", str(OUTPUT_DIR),
        "--frame-interval", str(frame_interval),
        "--batch-size", "8",
        "--concurrency", "3",
    ]

    try:
        proc = subprocess.run(
            cmd, env=env,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=7200,  # 2小时超时
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        elapsed = time.time() - start

        ok = is_done(video_path)
        return {
            "video": name,
            "path": str(video_path),
            "status": "ok" if ok else "failed",
            "elapsed_s": elapsed,
            "output_size": mark_complete(video_path).stat().st_size if ok else 0,
            "stderr": proc.stderr[-300:] if proc.returncode != 0 else "",
        }
    except subprocess.TimeoutExpired:
        return {"video": name, "path": str(video_path),
                "status": "timeout", "elapsed_s": 7200, "output_size": 0, "stderr": "超时2h"}
    except Exception as e:
        return {"video": name, "path": str(video_path),
                "status": "error", "elapsed_s": time.time() - start,
                "output_size": 0, "stderr": str(e)}


# ── 主循环 ──

def log(msg: str):
    """同时输出到终端和日志文件"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="周会视频全自动接力处理")
    parser.add_argument("--parallel", type=int, default=2, help="并行数（默认2）")
    parser.add_argument("--frame-interval", type=int, default=15, help="截图间隔秒")
    parser.add_argument("--dry-run", action="store_true", help="预览不执行")
    parser.add_argument("--force", action="store_true", help="强制重跑已完成视频")
    args = parser.parse_args()

    videos = find_videos()
    if not videos:
        log("[FAIL] 未找到2024年视频")
        return

    # 分离待处理/已完成
    todo = []
    done = []
    for v in videos:
        if is_done(v) and not args.force:
            done.append(v)
        else:
            todo.append(v)

    log("=" * 60)
    log(f"周会视频自动接力 — 启动")
    log(f"  总数: {len(videos)} | 已完成: {len(done)} | 待处理: {len(todo)}")
    log(f"  并行: {args.parallel}路 | 帧间隔: {args.frame_interval}s")
    log("=" * 60)

    if done:
        for v in done:
            log(f"  [跳过] {v.stem} — 已有产出")

    if args.dry_run:
        for v in todo:
            log(f"  [待处理] {v.stem}")
        return

    if not todo:
        log("* 全部完成！")
        return

    # ── 启动处理 ──
    total_start = time.time()
    results = []
    completed_count = 0

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        # 逐个提交（不是一次性全部，而是顺序提交等前面的完成）
        # ThreadPoolExecutor 本身就是队列，提交 max_workers 个后自动阻塞
        futures = {}
        for v in todo:
            future = executor.submit(process_one, v, args.frame_interval)
            futures[future] = v

        for future in as_completed(futures):
            v = futures[future]
            result = future.result()
            results.append(result)
            completed_count += 1

            elapsed_min = result["elapsed_s"] / 60
            icon = "[OK]" if result["status"] == "ok" else "[FAIL]"
            log(f"{icon} [{completed_count}/{len(todo)}] {result['video']} "
                f"— {result['status']} ({elapsed_min:.0f}分) "
                f"大小={result['output_size']/1024:.0f}KB")

    # ── 汇总 ──
    total_min = (time.time() - total_start) / 60
    ok = [r for r in results if r["status"] == "ok"]
    fail = [r for r in results if r["status"] != "ok"]

    log("=" * 60)
    log(f"[DONE] 全部完成！总耗时: {total_min:.0f}分 | 成功: {len(ok)}/{len(results)}")
    if fail:
        log(f"  失败列表:")
        for f in fail:
            log(f"    {f['video']}: {f['status']} — {f['stderr'][:100]}")
    if ok:
        log(f"  产出文件:")
        for o in ok:
            log(f"    {mark_complete(Path(o['path']))}")
    log("=" * 60)


if __name__ == "__main__":
    main()
