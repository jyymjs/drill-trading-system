#!/usr/bin/env python3
"""
batch_process.py — 批量并行处理多个交易教学视频

用法:
  # 处理指定目录下所有 mp4 文件（最多 3 个并行）
  python batch_process.py --dir "D:/BaiduNetdiskDownload/路肖南/钻潜交易内训" --parallel 3

  # 处理指定文件列表
  python batch_process.py --files video1.mp4 video2.mp4 --parallel 2

  # 指定 API Key
  python batch_process.py --dir "课程目录" --zhipu-api-key xxx --parallel 3
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BATCH] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch")


def process_one_video(video_path: str, output_dir: str, zhipu_key: str,
                       gemini_key: str, frame_interval: float,
                       batch_size: int, concurrency: int) -> dict:
    """处理单个视频（在子进程中运行）"""
    video_name = Path(video_path).stem
    log.info(f"[{video_name}] 开始处理...")

    start = time.time()
    result = {
        "video": video_name,
        "path": video_path,
        "status": "unknown",
        "output": "",
        "duration_s": 0,
        "error": "",
    }

    try:
        # 构造命令
        cmd = [
            sys.executable,
            str(Path(__file__).parent / "extract_knowledge.py"),
            "--video", video_path,
            "--zhipu-api-key", zhipu_key,
            "--output-dir", output_dir,
            "--frame-interval", str(frame_interval),
            "--batch-size", str(batch_size),
            "--concurrency", str(concurrency),
        ]
        if gemini_key:
            cmd += ["--gemini-api-key", gemini_key]

        # 每个视频使用独立的临时目录（避免检查点冲突）
        env = os.environ.copy()
        unique_temp = f"产出/临时/extract_knowledge/{video_name[:20]}_{int(time.time())}"
        env["TEMP_ROOT"] = unique_temp

        log.info(f"[{video_name}] 执行命令...")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=1800,  # 30分钟超时
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        elapsed = time.time() - start
        result["duration_s"] = elapsed

        if proc.returncode == 0:
            result["status"] = "success"
            # 从输出中提取文件路径
            for line in proc.stdout.split("\n"):
                if "知识文档已生成" in line or "输出:" in line:
                    result["output"] = line.strip()
            log.info(f"[{video_name}] ✅ 完成 ({elapsed:.0f}秒)")
        else:
            result["status"] = "failed"
            result["error"] = (proc.stderr[-500:] if proc.stderr else "未知错误")
            log.warning(f"[{video_name}] ❌ 失败: {result['error'][:100]}")

    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["error"] = "超时（>30分钟）"
        log.warning(f"[{video_name}] ⏰ 超时")
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        log.error(f"[{video_name}] 🔴 异常: {e}")

    return result


def find_videos(directory: str) -> list:
    """查找目录下所有 mp4 文件"""
    videos = []
    for f in sorted(os.listdir(directory)):
        if f.lower().endswith(".mp4"):
            videos.append(os.path.join(directory, f))
    return videos


def main():
    parser = argparse.ArgumentParser(
        description="批量并行处理交易教学视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 视频来源（二选一）
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", help="视频目录，自动扫描所有 mp4")
    group.add_argument("--files", nargs="+", help="视频文件列表")

    # 配置
    parser.add_argument("--zhipu-api-key", default=os.environ.get("ZHIPU_API_KEY", ""),
                        help="智谱 AI API Key")
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY", ""),
                        help="Gemini API Key（备用）")
    parser.add_argument("--output-dir", default="工具链/脚本/output",
                        help="输出目录（默认 scripts/output）")
    parser.add_argument("--parallel", type=int, default=2,
                        help="并行处理数量（默认 2，建议 2-3）")
    parser.add_argument("--frame-interval", type=float, default=10,
                        help="截图间隔秒数（默认 10）")
    parser.add_argument("--batch-size", type=int, default=8,
                        help="帧分析批次大小（默认 8）")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="API 并发数（默认 3）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出视频，不实际处理")

    args = parser.parse_args()

    # 检查 API Key
    if not args.zhipu_api_key:
        log.error("❌ 需要设置 ZHIPU_API_KEY 环境变量或 --zhipu-api-key 参数")
        sys.exit(1)

    # 获取视频列表
    if args.dir:
        if not os.path.isdir(args.dir):
            log.error(f"❌ 目录不存在: {args.dir}")
            sys.exit(1)
        videos = find_videos(args.dir)
    else:
        videos = [os.path.abspath(f) for f in args.files if os.path.isfile(f)]
        missing = [f for f in args.files if not os.path.isfile(f)]
        if missing:
            log.warning(f"⚠️ 以下文件不存在: {missing}")

    if not videos:
        log.error("❌ 没有找到可处理的视频文件")
        sys.exit(1)

    # 确保输出目录存在
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    log.info("=" * 60)
    log.info("批量处理启动")
    log.info(f"  视频数量: {len(videos)}")
    log.info(f"  并行数: {args.parallel}")
    log.info(f"  输出目录: {output_dir}")
    log.info(f"  API Key: {'✅ 已配置' if args.zhipu_api_key else '❌ 未配置'}")
    log.info("=" * 60)

    for i, v in enumerate(videos, 1):
        log.info(f"  [{i}/{len(videos)}] {Path(v).name}")
    log.info("=" * 60)

    if args.dry_run:
        log.info("🔍 Dry-run 模式，不执行处理")
        return

    total_start = time.time()
    results = []

    # 并行处理
    with ProcessPoolExecutor(max_workers=args.parallel) as executor:
        futures = []
        for video in videos:
            future = executor.submit(
                process_one_video,
                video, output_dir, args.zhipu_api_key,
                args.gemini_api_key, args.frame_interval,
                args.batch_size, args.concurrency,
            )
            futures.append(future)

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

    # 输出汇总
    total_elapsed = time.time() - total_start
    success = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] != "success"]

    log.info("=" * 60)
    log.info("📊 批量处理完成")
    log.info(f"  总耗时: {total_elapsed:.0f} 秒 ({total_elapsed/60:.1f} 分钟)")
    log.info(f"  成功: {len(success)} / {len(results)}")
    if failed:
        log.warning(f"  失败: {len(failed)}")
        for f in failed:
            log.warning(f"    - {f['video']}: {f['error'][:80]}")

    if success:
        log.info("📄 生成的知识文档:")
        for s in success:
            log.info(f"    {s.get('output', s['video'])}")

    log.info("=" * 60)
    log.info("现在可以告诉 Claude 读取新生成的知识文档进行炼化")


if __name__ == "__main__":
    main()
