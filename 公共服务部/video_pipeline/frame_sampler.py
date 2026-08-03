#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frame_sampler.py — 画面变化才抽帧 v3（低阈值 scene + pHash 去重）

用法:
  python frame_sampler.py <视频路径> <输出目录> [scene阈值] [pHash距离]
  例: python frame_sampler.py 视频.mp4 out/ 0.05 10

原理（v3，2026-08-03 按老板意见简化）:
  ① scene 检测阈值降到 0.05：更小的画面变化（K线缓慢更新/光标/局部标注）也能捕捉
  ② pHash 去重：相邻帧感知哈希距离 < 阈值（默认 10）视为相似，只保留变化更大的一帧
  ③ 不做定时兜底——纯"变化才抽"，减少冗余

输出:
  out/frame_<秒>s.png   关键帧（文件名自带视频时间戳）
  out/timeline.json     时间点清单（index/time/file）
"""
import subprocess, sys, os, re, json

try:
    from PIL import Image
    import imagehash
except ImportError:
    print("需要依赖: pip install imagehash pillow")
    sys.exit(1)


def detect_scene(video: str, threshold: float) -> list:
    """ffmpeg scene 检测，返回画面变化时间点列表（秒）"""
    cmd = ["ffmpeg", "-i", video,
           "-vf", f"select='gt(scene,{threshold})',showinfo",
           "-vsync", "vfr", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return [float(m.group(1)) for m in re.finditer(r"pts_time:(\d+\.?\d*)", proc.stderr)]


def extract_frame(video: str, t: float, outpath: str) -> None:
    cmd = ["ffmpeg", "-ss", str(t), "-i", video, "-frames:v", "1", "-q:v", "2", outpath]
    subprocess.run(cmd, capture_output=True)


def phash_filter(frames_dir: str, min_dist: int) -> list:
    """pHash 去重：相邻帧距离 < min_dist 视为相似，保留变化更大的一帧。
    返回保留的帧文件列表（按时间顺序）。"""
    files = sorted(f for f in os.listdir(frames_dir) if f.lower().endswith(".png"))
    kept, last_hash, last_file = [], None, None
    for f in files:
        h = imagehash.phash(Image.open(os.path.join(frames_dir, f)))
        if last_hash is None or h - last_hash >= min_dist:
            kept.append(f)
            last_hash, last_file = h, f
        else:
            os.remove(os.path.join(frames_dir, f))  # 相似帧，删掉
    return kept


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    video, outdir = sys.argv[1], sys.argv[2]
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
    min_dist = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    os.makedirs(outdir, exist_ok=True)

    print(f"scene 检测（阈值 {threshold}）...", flush=True)
    times = detect_scene(video, threshold)
    print(f"捕捉 {len(times)} 个变化点", flush=True)

    timeline = []
    for i, t in enumerate(times, 1):
        name = f"frame_{t:07.2f}s.png"
        extract_frame(video, t, os.path.join(outdir, name))
        timeline.append({"index": i, "time": round(t, 2), "file": name})
        if i % 20 == 0 or i == len(times):
            print(f"  [{i}/{len(times)}] t={t:.2f}s", flush=True)

    print(f"pHash 去重（距离 < {min_dist} 视为相似）...", flush=True)
    kept = phash_filter(outdir, min_dist)
    timeline = [t for t in timeline if t["file"] in kept]

    with open(os.path.join(outdir, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    print(f"完成: {len(kept)} 帧（去重前 {len(times)}）→ {outdir}", flush=True)


if __name__ == "__main__":
    main()
