# -*- coding: utf-8 -*-
"""
阿里云 DashScope 批量转写脚本（paraformer-realtime-v2 线上 ASR）
================================================================
功能：遍历视频目录 → ffmpeg 提取 16k 音频 → DashScope 转写 → 输出 [起-止] 文本
特性：多路并行（线上 API，本地零算力）、断点续传、日志、失败重试、重名消歧
用法：python dashscope_batch.py
"""
import os
import sys
import subprocess
import time
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Windows 控制台 GBK 兼容
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============ 配置 ============
VIDEO_ROOT = Path(r"D:\BaiduNetdiskDownload\路肖南")          # 视频源
OUT_ROOT = Path(r"C:\Users\32032\Desktop\deepseek\交易部\知识库")  # 输出：交易部知识库
TMP_DIR = Path(r"C:\Users\32032\Desktop\deepseek\公共服务部\whisper-vulkan\tmp")       # 临时 wav
MODEL = "paraformer-realtime-v2"   # 可用模型（专属网关实测）
BASE_URL = "https://ws-lgykee82rbzzw694.cn-beijing.maas.aliyuncs.com"  # 专属网关
SILENCE = 300                      # 句间断静音阈值(ms)，控制句子切分粒度
WORKERS = 4                        # 并行数（线上并发，4 保守；限流则降）
RETRY = 3                          # 失败重试次数
LOG_FILE = Path(r"C:\Users\32032\Desktop\deepseek\公共服务部\whisper-vulkan\dashscope_transcribe.log")
# ==============================

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts"}

_log_lock = threading.Lock()
_progress_lock = threading.Lock()
_done = 0
_fail = 0


def get_api_key():
    """优先环境变量，兜底 Windows 用户级环境变量"""
    k = os.environ.get("DASHSCOPE_API_KEY")
    if not k:
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "[Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY','User')"],
                capture_output=True, text=True, timeout=15)
            k = r.stdout.strip()
        except Exception:
            pass
    if not k:
        raise SystemExit("未找到 DASHSCOPE_API_KEY（环境变量或用户级环境变量）")
    return k


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def find_videos(root):
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


def get_duration(video):
    """ffprobe 拿视频时长（秒）"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def extract_audio(video, wav_path):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(video), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
           str(wav_path)]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0


def dashscope_transcribe(wav_path, api_key):
    """调用 paraformer-realtime-v2，返回 [(begin_ms, end_ms, text), ...]"""
    import dashscope
    from dashscope.audio.asr import Recognition
    dashscope.api_key = api_key
    dashscope.base_url = BASE_URL
    rec = Recognition(model=MODEL, format="wav", sample_rate=16000,
                      callback=None, max_sentence_silence=SILENCE)
    result = rec.call(str(wav_path))
    if result.status_code != 200:
        raise RuntimeError(f"API 错误({result.status_code}): {getattr(result, 'message', '')}")
    sentences = result.get_sentence()
    if not isinstance(sentences, list):
        raise RuntimeError("API 返回格式异常: 无句子列表")
    return [(int(s.get("begin_time", 0)), int(s.get("end_time", 0)), s.get("text", ""))
            for s in sentences]


def fmt_ts(ms):
    """毫秒 → '  秒.毫秒'（两位小数秒，与知识库 raw 格式一致）"""
    return f"{ms / 1000:8.2f}"


def transcribe_long(video, api_key, seg_len=1800, max_dur=3300):
    """超长音频分段转写（每段 seg_len 秒），时间戳自动补偿偏移"""
    dur = get_duration(video)
    if dur <= max_dur:
        return None  # 不超长，走常规路径
    segs_all = []
    seg_start = 0
    while seg_start < dur:
        seg_wav = TMP_DIR / f"dash_seg_{video.stem}_{seg_start}.wav"
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", str(seg_start), "-t", str(seg_len), "-i", str(video),
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(seg_wav)],
            capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"分段音频提取失败 @{seg_start}s")
        seg = dashscope_transcribe(seg_wav, api_key)
        segs_all += [(b + seg_start * 1000, e + seg_start * 1000, t) for b, e, t in seg]
        seg_wav.unlink(missing_ok=True)
        seg_start += seg_len
    return segs_all


def process_one(cat, video, total, dup_stems, api_key):
    global _done, _fail
    rel = video.relative_to(VIDEO_ROOT)
    out_dir = OUT_ROOT / cat / "raw"
    os.makedirs(out_dir, exist_ok=True)
    # 重名消歧：子目录视频带父目录前缀
    in_subdir = len(rel.parents) > 1
    stem_new = f"{rel.parent.name}_{video.stem}" if in_subdir else video.stem
    txt_new = out_dir / f"{stem_new}.txt"
    txt_old = out_dir / f"{video.stem}.txt"

    # 断点续传（与 whisper 批次规则一致）
    if video.stem in dup_stems:
        skip = txt_new.exists() and txt_new.stat().st_size > 10
    else:
        t = txt_new if txt_new.exists() else txt_old
        skip = t.exists() and t.stat().st_size > 10
    if skip:
        with _progress_lock:
            _done += 1
        log(f"⏭ [{_done}/{total}] 已存在跳过: {rel}")
        return

    wav = TMP_DIR / f"dash_{video.stem}.wav"
    ok = False
    for attempt in range(1, RETRY + 1):
        if not extract_audio(video, wav):
            log(f"  ⚠ 音频提取失败 (第{attempt}次): {rel}")
            time.sleep(3)
            continue
        try:
            segs = transcribe_long(video, api_key)  # 超长自动分段
            if segs is None:
                segs = dashscope_transcribe(wav, api_key)
            # 输出 [起 - 止] 文本 格式（与知识库 raw 一致）
            with open(txt_new, "w", encoding="utf-8") as f:
                for b, e, t in segs:
                    f.write(f"[{fmt_ts(b)} - {fmt_ts(e)}] {t.strip()}\n")
            ok = txt_new.exists() and txt_new.stat().st_size > 10
            break
        except Exception as e:
            log(f"  ⚠ 转写失败 (第{attempt}次): {rel} — {str(e)[:80]}")
            time.sleep(5)
    wav.unlink(missing_ok=True)

    with _progress_lock:
        if ok:
            _done += 1
        else:
            _fail += 1
    if ok:
        log(f"✅ [{_done}/{total}] {rel}")
    else:
        log(f"❌ [{_done+_fail}/{total}] 重试耗尽失败: {rel}")


def main():
    global _done, _fail
    api_key = get_api_key()
    os.makedirs(TMP_DIR, exist_ok=True)
    videos = find_videos(VIDEO_ROOT)
    if not videos:
        log("未找到任何视频")
        return
    total = len(videos)
    dup_stems = {s for s, c in Counter(v.stem for _, v in videos).items() if c > 1}
    if dup_stems:
        log(f"⚠ 检测到 {len(dup_stems)} 个重名视频，将带父目录前缀输出")
    # 按时长升序排序：短视频先转，避免超长视频占满并发槽
    videos.sort(key=lambda x: get_duration(x[1]))
    log(f"🚀 开始 DashScope 批量转写：共 {total} 个视频，模型 {MODEL}，并行 {WORKERS} 路（按时长排序，超长自动分段）")
    log(f"   输出目录: {OUT_ROOT}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(process_one, cat, video, total, dup_stems, api_key)
                   for cat, video in videos]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                with _progress_lock:
                    _fail += 1
                log(f"❌ 异常: {str(e)[:100]}")

    log(f"🏁 完成：成功 {_done}，失败 {_fail}，总计 {total}")
    if _fail:
        log("⚠ 失败列表见上方 ❌ 行，可重新运行本脚本自动重试")


if __name__ == "__main__":
    main()
