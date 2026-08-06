# -*- coding: utf-8 -*-
"""
GPU 批量转写脚本（whisper.cpp Vulkan 版 · AMD RX 6750 GRE）
=============================================================
功能：遍历视频目录 → ffmpeg 提取 16k 音频 → whisper-cli GPU 转写 → 输出 txt
特性：多路并行（3 个转写进程共享显卡）、断点续传、日志、失败重试
用法：python batch_transcribe.py
"""
import os
import sys
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 避免 emoji/中文输出崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============ 配置 ============
VIDEO_ROOT = Path(r"D:\BaiduNetdiskDownload\路肖南")          # 视频源
OUT_ROOT = Path(r"C:\Users\32032\Desktop\deepseek\工作流\交易部门\策略\知识库")  # 转写输出：交易部知识库（按分类建主题）
TMP_DIR = Path(r"C:\Users\32032\Desktop\deepseek\工作流\公共资源部\第三方引擎\whisper-vulkan\tmp")       # 临时 wav
WHISPER_CLI = Path(r"C:\Users\32032\Desktop\deepseek\工作流\公共资源部\第三方引擎\whisper-vulkan\bin\whisper-cli.exe")
MODEL = Path(r"C:\Users\32032\Desktop\deepseek\工作流\公共资源部\第三方引擎\whisper-vulkan\models\ggml-large-v3-turbo-q8_0.bin")
LANG = "zh"          # 语言：中文
THREADS = 2          # 每个转写进程的 CPU 线程（GPU 主算，2 线程足够，5 路不超卖 CPU）
WORKERS = 5          # 并行转写进程数（显存 12GB / 每路~2GB，5 路安全；调大可能引发驱动崩溃）
RETRY = 3            # 失败重试次数
LOG_FILE = Path(r"C:\Users\32032\Desktop\deepseek\公共服务部\whisper-vulkan\transcribe.log")
# ==============================

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts"}

_log_lock = threading.Lock()
_progress_lock = threading.Lock()
_done = 0
_fail = 0


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def find_videos(root):
    """返回 (分类, 视频绝对路径) 列表，按分类分组"""
    result = []
    if not root.exists():
        log(f"❌ 视频目录不存在: {root}")
        return result
    for cat_dir in sorted(root.iterdir()):
        if not cat_dir.is_dir():
            continue
        for v in sorted(cat_dir.rglob("*")):
            if v.is_file() and v.suffix.lower() in VIDEO_EXTS:
                result.append((cat_dir.name, v))
    return result


def extract_audio(video, wav_path):
    """ffmpeg 提取 16k 单声道 wav"""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0


def transcribe(wav_path, out_prefix):
    """whisper-cli GPU 转写，返回 (成功, 输出txt路径)"""
    cmd = [
        str(WHISPER_CLI),
        "-m", str(MODEL),
        "-f", str(wav_path),
        "-l", LANG,
        "-t", str(THREADS),
        "-otxt",
        "-of", out_prefix,
    ]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    txt = Path(str(out_prefix) + ".txt")
    return (r.returncode == 0 and txt.exists()), txt


def process_one(cat, video, total, dup_stems):
    """处理单个视频（提取音频 + 转写，含重试），供并行 worker 调用"""
    global _done, _fail
    rel = video.relative_to(VIDEO_ROOT)
    # 输出到 交易部/知识库/{分类}/raw/（知识库规范：raw=素材保真层）
    out_dir = OUT_ROOT / cat / "raw"
    os.makedirs(out_dir, exist_ok=True)
    # 消歧：子目录里的视频文件名加父目录前缀，避免不同子目录同名视频互相覆盖
    in_subdir = len(rel.parents) > 1
    stem_new = f"{rel.parent.name}_{video.stem}" if in_subdir else video.stem
    out_prefix = out_dir / stem_new
    txt_new = Path(str(out_prefix) + ".txt")
    txt_old = out_dir / f"{video.stem}.txt"

    # 断点续传：唯一名视频认新名或旧名；重名对必须按新名完成
    if video.stem in dup_stems:
        skip = txt_new.exists() and txt_new.stat().st_size > 10
    else:
        skip = ((txt_new.exists() or txt_old.exists())
                and Path(str(txt_new if txt_new.exists() else txt_old)).stat().st_size > 10)
    if skip:
        with _progress_lock:
            _done += 1
        log(f"⏭ [{_done}/{total}] 已存在跳过: {rel}")
        return

    wav = TMP_DIR / f"tmp_{video.stem}.wav"
    ok = False
    for attempt in range(1, RETRY + 1):
        if not extract_audio(video, wav):
            log(f"  ⚠ 音频提取失败 (第{attempt}次): {rel}")
            time.sleep(3)
            continue
        ok, txt = transcribe(wav, out_prefix)
        if ok:
            break
        log(f"  ⚠ 转写失败 (第{attempt}次): {rel}")
        time.sleep(5)
    wav.unlink(missing_ok=True)

    with _progress_lock:
        if ok:
            _done += 1
        else:
            _fail += 1
    if ok:
        log(f"✅ [{_done}/{total}] {rel} → {out_prefix.name}.txt")
    else:
        log(f"❌ [{_done+_fail}/{total}] 重试耗尽失败: {rel}")


def main():
    global _done, _fail
    os.makedirs(TMP_DIR, exist_ok=True)
    videos = find_videos(VIDEO_ROOT)
    if not videos:
        log("未找到任何视频")
        return
    total = len(videos)
    # 重名检测：跨子目录同名视频需消歧（否则输出互相覆盖）
    from collections import Counter
    dup_stems = {s for s, c in Counter(v.stem for _, v in videos).items() if c > 1}
    if dup_stems:
        log(f"⚠ 检测到 {len(dup_stems)} 个重名视频，将带父目录前缀输出: {sorted(dup_stems)}")
    log(f"🚀 开始批量转写：共 {total} 个视频，模型 {MODEL.name}，并行 {WORKERS} 路")
    log(f"   输出目录: {OUT_ROOT}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(process_one, cat, video, total, dup_stems) for cat, video in videos]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                with _progress_lock:
                    _fail += 1
                log(f"❌ 异常: {e}")

    log(f"🏁 完成：成功 {_done}，失败 {_fail}，总计 {total}")
    if _fail:
        log("⚠ 失败列表见上方 ❌ 行，可重新运行本脚本自动重试")


if __name__ == "__main__":
    main()
