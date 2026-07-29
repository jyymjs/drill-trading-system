#!/usr/bin/env python3
"""
autopilot.py — 自动监控并接力处理视频
检测到有视频完成后，自动启动下一个优先视频
"""
import subprocess, sys, os, time, glob, re
from pathlib import Path

VIDEO_DIR = "D:/BaiduNetdiskDownload/路肖南/钻潜交易内训"
OUTPUT_DIR = "C:/Users/32032/Desktop/deepseek/量化交易系统/scripts/output"
LOG_DIR = "C:/Users/32032/Desktop/deepseek/量化交易系统"
SCRIPT_DIR = "C:/Users/32032/Desktop/deepseek/量化交易系统"

MAX_PARALLEL = 2
CHECK_INTERVAL = 60  # 秒

# 优先级排序（越靠前越先处理）
PRIORITY = [
    "第五节：如何判断市场动能",
    "第八节：market profile 技术细节",
    "第十节：趋势技术细节：判断趋势的健康程度",
    "第十一节：空间技术细节：支撑和阻力的形成原理",
    "第十二节：空间技术细节：4种关键空间位置的规范",
    "第十七节：成交量判断细节：成交量分布图",
    "第二节：全市场交易规则和品种属性",
    "第三节：专业交易者应具备的一系列认知",
    "第四节：钻潜交易整体印象",
    "第二十节：高效率盯盘细节",
    "第二十四节：战胜人性的弱点：连续止损",
    "第二十六节：资金管理方案：多个方案",
    "第二十八节：交易心态管理：交易者心态问题",
]

def get_completed() -> set:
    """获取已完成的视频文件名（不含后缀）"""
    completed = set()
    pattern = os.path.join(OUTPUT_DIR, "*_knowledge.md")
    for f in glob.glob(pattern):
        name = os.path.basename(f).replace("_knowledge.md", "")
        completed.add(name)
    return completed

def get_running_pids() -> set:
    """获取当前正在运行的处理进程PID"""
    running = set()
    for f in glob.glob(os.path.join(LOG_DIR, "pid_*.txt")):
        try:
            with open(f) as pf:
                lines = pf.read().strip().split("\n")
                pid = int(lines[-1])
            # 检查进程是否存在
            try:
                os.kill(pid, 0)
                running.add(pid)
            except OSError:
                pass  # 进程已结束
        except (ValueError, IOError):
            pass
    return running

def get_videos_by_priority() -> list:
    """按优先级返回未处理的视频列表"""
    completed = get_completed()
    all_videos = []
    for f in sorted(os.listdir(VIDEO_DIR)):
        if f.lower().endswith(".mp4"):
            all_videos.append(f)

    # 按优先级排序
    def sort_key(name):
        for i, pref in enumerate(PRIORITY):
            if pref in name:
                return i
        return 999  # 不在优先级列表中的排最后

    all_videos.sort(key=sort_key)

    # 过滤已完成的
    pending = []
    for v in all_videos:
        base = v.replace(".mp4", "")
        if base not in completed:
            pending.append(v)
    return pending

def launch_video(video_name: str, suffix: str) -> bool:
    """启动一个视频处理"""
    video_path = os.path.join(VIDEO_DIR, video_name)
    if not os.path.exists(video_path):
        return False

    script = os.path.join(SCRIPT_DIR, "run_video.py")
    cmd = ["python", script, video_path, suffix]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        print(f"[AUTOPILOT] 启动: {video_name[:30]}... PID={proc.pid}")
        return True
    except Exception as e:
        print(f"[AUTOPILOT] 启动失败: {e}")
        return False

def main():
    print(f"[AUTOPILOT] 启动自动接力，最大并行: {MAX_PARALLEL}")
    print(f"[AUTOPILOT] 检查间隔: {CHECK_INTERVAL}秒")

    suffix_counter = [int(time.time())]

    while True:
        try:
            running = get_running_pids()
            running_count = len(running)
            pending = get_videos_by_priority()
            completed_count = len(get_completed())

            print(f"[AUTOPILOT] 完成: {completed_count} | 运行中: {running_count} | 待处理: {len(pending)}")

            # 如果有空位，启动新视频
            while running_count < MAX_PARALLEL and pending:
                next_video = pending.pop(0)
                suffix_counter[0] += 1
                if launch_video(next_video, str(suffix_counter[0])):
                    running_count += 1
                time.sleep(5)  # 启动间隔

            if len(pending) == 0 and running_count == 0:
                print("[AUTOPILOT] 🎉 全部完成！")
                break

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n[AUTOPILOT] 手动停止")
            break
        except Exception as e:
            print(f"[AUTOPILOT] 异常: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
