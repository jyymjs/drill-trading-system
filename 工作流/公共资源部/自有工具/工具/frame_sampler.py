#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frame_sampler.py — 画面变化抽帧器（公共服务部工具）

用法:
  python frame_sampler.py <视频路径> <输出目录> [--scene 0.4]

原理:
  - ffmpeg 硬件解码（AMD d3d11va，失败自动回退 CPU）
  - scene 场景检测：相邻帧画面差异超阈值才保留（画面切换才抽帧，讲课静止不抽）
  - showinfo 输出每帧精确时间戳 → 生成 frames_timestamps.csv

输出:
  - 帧图 frame_00001.png ...（按保留顺序命名）
  - frames_timestamps.csv（帧名, 秒, 时:分:秒）——供图文绑定使用
"""
import sys, os, subprocess, re, csv

def main():
    if len(sys.argv) < 3:
        print("用法: python frame_sampler.py <视频> <输出目录> [--scene 阈值(默认0.4)]")
        sys.exit(1)
    video, out = sys.argv[1], sys.argv[2]
    thr = 0.4
    if "--scene" in sys.argv:
        thr = float(sys.argv[sys.argv.index("--scene") + 1])
    os.makedirs(out, exist_ok=True)

    # 硬件解码 + scene 检测 + 帧输出 + 时间戳
    cmd = ["ffmpeg", "-y", "-hwaccel", "d3d11va", "-i", video,
           "-vf", f"select='gt(scene,{thr})',showinfo",
           "-vsync", "vfr", "-f", "image2",
           os.path.join(out, "frame_%05d.png")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    # 解析 showinfo 行: n:xx pts_time:xx.xx ...（按输出顺序）
    pts = re.findall(r"pts_time:([\d.]+)", r.stderr)
    # image2 输出文件按保留顺序命名（1-based）
    files = sorted(f for f in os.listdir(out) if f.startswith("frame_") and f.endswith(".png"))
    ts_file = os.path.join(out, "frames_timestamps.csv")
    with open(ts_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame", "time_sec", "time_hms"])
        for i, sec in enumerate(pts):
            if i < len(files):
                sec = float(sec)
                hms = f"{int(sec//3600):02d}:{int(sec%3600//60):02d}:{int(sec%60):02d}"
                w.writerow([files[i], f"{sec:.2f}", hms])

    print(f"抽帧完成: {len(pts)} 帧（scene阈值 {thr}）")
    print(f"帧目录: {out}")
    print(f"时间戳表: {ts_file}")
    if "Failed to setup" in r.stderr or "d3d11va" in r.stderr and "not supported" in r.stderr:
        print("硬件解码: 未生效（已自动回退 CPU 解码，结果不受影响）")
    else:
        print("硬件解码: 已请求 d3d11va（AMD GPU）")

if __name__ == "__main__":
    main()
