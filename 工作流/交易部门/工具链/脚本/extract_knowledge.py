#!/usr/bin/env python3
"""
extract_knowledge.py — 将交易教学视频（MP4）转化为结构化的 Markdown 知识文档

架构：
  - SenseVoice（语音） + GLM-4V（画面），自动故障转移
  - 并行处理：音频转录和帧分析同时进行
  - 断点续传：通过 checkpoint.json 记录进度
  - 容错：指数退避重试，失败帧不阻塞整体流程

用法：
  python extract_knowledge.py --video 视频.mp4 --zhipu-api-key xxx
  python extract_knowledge.py --video 视频.mp4 --zhipu-api-key xxx --gemini-api-key xxx
  python extract_knowledge.py --help
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

# ============================================================
# 第三方库 — 延迟导入（在对应函数中 import）
#   pip install tqdm python-dotenv openai requests funasr modelscope
#   pip install openai-whisper google-generativeai
# ============================================================

# ─── 日志 ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("extract_knowledge")

# ============================================================
# 配置
# ============================================================

@dataclass
class Config:
    """所有可配置项，支持环境变量 / .env 文件 / CLI 参数覆盖。"""

    # --- 必需 ---
    zhipu_api_key: str = ""
    video_path: str = ""

    # --- 可选 ---
    gemini_api_key: str = ""
    output_dir: str = ""
    frame_interval: float = 15.0      # 截图间隔（秒）
    batch_size: int = 10              # 帧分析并行批次大小
    concurrency: int = 4              # 线程池并发数
    max_retries: int = 3              # API 调用最大重试次数
    ffmpeg_path: str = "ffmpeg"       # ffmpeg 可执行文件路径
    temp_root: str = ""               # 临时文件根目录

    # 派生路径（setup 时填充）
    temp_dir: str = ""
    frames_dir: str = ""
    audio_path: str = ""
    checkpoint_path: str = ""

    # 视频分段（超 45 分钟自动分段）
    max_segment_minutes: int = 45

    # 模型名称
    zhipu_model: str = "glm-4v-flash"
    gemini_model: str = "gemini-2.0-flash-exp"

    @classmethod
    def from_env_and_args(cls, args: argparse.Namespace | None = None) -> Config:
        """从环境变量 .env 和 CLI 参数合并构建配置，CLI 参数优先。"""
        # 尝试加载 .env
        self = cls()

        # 加载 .env 文件（如存在）
        env_paths = [
            Path.cwd() / ".env",
            Path(__file__).parent / ".env",
            Path(__file__).parent.parent / ".env",
        ]
        for p in env_paths:
            if p.exists():
                try:
                    from dotenv import load_dotenv
                    load_dotenv(p, override=False)
                    log.info("已加载 .env 文件: %s", p)
                except ImportError:
                    log.warning("python-dotenv 未安装，跳过 .env 加载，请 pip install python-dotenv")
                break

        # 从环境变量读取
        env_map = {
            "ZHIPU_API_KEY": "zhipu_api_key",
            "GEMINI_API_KEY": "gemini_api_key",
            "VIDEO_PATH": "video_path",
            "OUTPUT_DIR": "output_dir",
            "FRAME_INTERVAL": "frame_interval",
            "BATCH_SIZE": "batch_size",
            "CONCURRENCY": "concurrency",
            "MAX_RETRIES": "max_retries",
            "FFMPEG_PATH": "ffmpeg_path",
        }
        for env_name, attr in env_map.items():
            val = os.environ.get(env_name)
            if val is not None:
                # 数值类型转换
                current = getattr(self, attr)
                if isinstance(current, float):
                    setattr(self, attr, float(val))
                elif isinstance(current, int):
                    setattr(self, attr, int(val))
                else:
                    setattr(self, attr, val)

        # CLI 参数覆盖（优先级最高）
        if args is not None:
            for attr in env_map.values():
                cli_val = getattr(args, attr, None)
                if cli_val is not None:
                    setattr(self, attr, cli_val)
            # 额外 CLI 专属参数
            for extra in ["zhipu_api_key", "gemini_api_key", "video_path",
                          "output_dir", "ffmpeg_path", "zhipu_model", "gemini_model"]:
                cli_val = getattr(args, extra, None)
                if cli_val is not None:
                    setattr(self, extra, cli_val)
            if hasattr(args, "frame_interval") and args.frame_interval is not None:
                self.frame_interval = float(args.frame_interval)
            if hasattr(args, "batch_size") and args.batch_size is not None:
                self.batch_size = int(args.batch_size)
            if hasattr(args, "concurrency") and args.concurrency is not None:
                self.concurrency = int(args.concurrency)
            if hasattr(args, "max_retries") and args.max_retries is not None:
                self.max_retries = int(args.max_retries)

        self._validate()
        self._setup_paths()
        return self

    def _validate(self) -> None:
        if not self.zhipu_api_key:
            log.warning("ZHIPU_API_KEY 未设置 — GLM-4V 画面分析将不可用，仅能完成语音转录")
        if not self.video_path:
            raise ValueError("必须指定视频路径 (VIDEO_PATH / --video)")
        if not os.path.isfile(self.video_path):
            raise FileNotFoundError(f"视频文件不存在: {self.video_path}")

    def _setup_paths(self) -> None:
        video_stem = Path(self.video_path).stem
        # 清理文件名中的特殊字符
        safe_stem = re.sub(r'[\\/:*?"<>|]', "_", video_stem)

        if not self.temp_root:
            self.temp_root = str(Path(__file__).parent.parent.parent / "temp" / "extract_knowledge")
        self.temp_dir = os.path.join(self.temp_root, safe_stem)
        self.frames_dir = os.path.join(self.temp_dir, "frames")
        self.audio_path = os.path.join(self.temp_dir, "audio.wav")
        self.checkpoint_path = os.path.join(self.temp_dir, "checkpoint.json")

        if not self.output_dir:
            self.output_dir = os.path.join(Path(__file__).parent, "output")

        # 确保目录存在
        os.makedirs(self.frames_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}


# ============================================================
# 检查点管理器
# ============================================================

class CheckpointManager:
    """基于 JSON 文件的检查点，支持断点续传。"""

    def __init__(self, path: str):
        self.path = path
        self.data: dict = self._load()

    def _load(self) -> dict:
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                log.warning("检查点文件损坏，重新开始: %s", self.path)
        return {
            "video_path": "",
            "video_size": 0,
            "video_mtime": 0,
            "audio_extracted": False,
            "frames_extracted": False,
            "frame_list": [],
            "transcription_done": False,
            "transcription_result": [],
            "frame_results": {},          # {filename: {status, result, error, timestamp}}
            "processed_frame_count": 0,
            "merged": False,
            "output_path": "",
            "version": 2,
        }

    def save(self) -> None:
        try:
            # 原子写入：先写临时文件再 rename（Windows 兼容）
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
            # Windows 上 replace 是原子的（目标存在时覆盖）
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("检查点保存失败: %s", e)

    def sync_video_info(self, video_path: str) -> bool:
        """检查视频是否变更，返回 True 表示需要从头开始。"""
        st = os.stat(video_path)
        size = st.st_size
        mtime = st.st_mtime
        if (self.data["video_path"] == video_path
                and self.data["video_size"] == size
                and self.data["video_mtime"] == mtime):
            return False  # 未变更
        # 视频变更或首次运行，重置
        self.data["video_path"] = video_path
        self.data["video_size"] = size
        self.data["video_mtime"] = mtime
        self.data["audio_extracted"] = False
        self.data["frames_extracted"] = False
        self.data["frame_list"] = []
        self.data["transcription_done"] = False
        self.data["transcription_result"] = []
        self.data["frame_results"] = {}
        self.data["processed_frame_count"] = 0
        self.data["merged"] = False
        self.data["output_path"] = ""
        self.save()
        return True

    def mark_audio_extracted(self) -> None:
        self.data["audio_extracted"] = True
        self.save()

    def mark_frames_extracted(self, frame_list: list[str]) -> None:
        self.data["frames_extracted"] = True
        self.data["frame_list"] = frame_list
        # 为新帧初始化结果条目
        existing = set(self.data["frame_results"].keys())
        for f in frame_list:
            if f not in existing:
                self.data["frame_results"][f] = {
                    "status": "pending",
                    "result": None,
                    "error": None,
                    "timestamp": 0.0,
                }
        self.save()

    def mark_transcription_done(self, result: list[dict]) -> None:
        self.data["transcription_done"] = True
        self.data["transcription_result"] = result
        self.save()

    def mark_frame_analyzed(self, filename: str, result: str | None,
                            error: str | None, timestamp: float) -> None:
        if filename not in self.data["frame_results"]:
            self.data["frame_results"][filename] = {}
        self.data["frame_results"][filename] = {
            "status": "done" if error is None else "failed",
            "result": result,
            "error": error,
            "timestamp": timestamp,
        }
        # 更新已处理计数
        done = sum(1 for v in self.data["frame_results"].values()
                   if v.get("status") in ("done", "failed"))
        self.data["processed_frame_count"] = done
        # 每分析 5 帧存一次（避免频繁 IO）
        if done % 5 == 0:
            self.save()
        else:
            # 只写不频繁刷盘
            self.save()

    def mark_merged(self, output_path: str) -> None:
        self.data["merged"] = True
        self.data["output_path"] = output_path
        self.save()

    def get_pending_frames(self) -> list[str]:
        """返回尚未分析或分析失败的帧。"""
        pending = []
        for f in self.data["frame_list"]:
            info = self.data["frame_results"].get(f, {})
            if info.get("status") != "done":
                pending.append(f)
        return pending

    def needs_audio_extraction(self) -> bool:
        return not self.data.get("audio_extracted", False)

    def needs_frame_extraction(self) -> bool:
        return not self.data.get("frames_extracted", False)

    def needs_transcription(self) -> bool:
        return not self.data.get("transcription_done", False)

    def needs_frame_analysis(self) -> bool:
        return bool(self.get_pending_frames())

    def needs_merge(self) -> bool:
        return not self.data.get("merged", False)


# ============================================================
# 视频处理器 — ffmpeg
# ============================================================

class VideoProcessor:
    """封装 ffmpeg 调用：音频提取、帧提取、分段。"""

    def __init__(self, config: Config):
        self.config = config
        self.ffmpeg = config.ffmpeg_path
        self._check_ffmpeg()

    def _check_ffmpeg(self) -> None:
        """验证 ffmpeg 可用。"""
        try:
            subprocess.run(
                [self.ffmpeg, "-version"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(
                f"ffmpeg 不可用: {e}。请安装 ffmpeg 或设置 FFMPEG_PATH 环境变量。"
            ) from e

    def get_video_duration(self) -> float:
        """获取视频时长（秒）。优先使用 ffprobe，备用 ffmpeg 解析 stderr。"""
        # 优先用 ffprobe（更可靠）
        try:
            return self._get_duration_ffprobe()
        except (ValueError, subprocess.TimeoutExpired, FileNotFoundError, RuntimeError):
            pass

        # 备用：用 ffmpeg 解析 stderr
        cmd = [
            self.ffmpeg, "-i", self.config.video_path,
            "-f", "null", "-",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=False, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            # 从 stderr 提取时长（可能是 bytes）
            stderr = result.stderr
            if stderr is None:
                raise RuntimeError("ffmpeg stderr 为空")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            # 匹配格式: Duration: 01:23:45.67, start: ...
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
            raise RuntimeError(f"无法从 ffmpeg 输出解析时长: {stderr[:200]}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg 获取视频时长超时")

    def _get_duration_ffprobe(self) -> float:
        """使用 ffprobe 获取时长。"""
        ffprobe = self.ffmpeg.replace("ffmpeg", "ffprobe")
        if ffprobe == self.ffmpeg:
            ffprobe = "ffprobe"
        cmd = [
            ffprobe, "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            self.config.video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=False, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            stdout = result.stdout
            if stdout is None:
                raise ValueError("ffprobe stdout 为空")
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            return float(stdout.strip())
        except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
            log.warning("无法获取视频时长，假设为 0")
            return 0.0

    def extract_audio(self) -> str:
        """
        提取音频为 16kHz 单声道 WAV。
        对 >45 分钟视频自动分段处理。
        """
        output = self.config.audio_path
        duration = self.get_video_duration()
        log.info("视频时长: %.1f 秒 (%.1f 分钟)", duration, duration / 60)

        if duration > self.config.max_segment_minutes * 60:
            log.info("视频超过 %d 分钟，进行分段处理", self.config.max_segment_minutes)
            segments = self._split_audio_segments(duration)
            # 合并所有分段
            self._merge_audio_segments(segments, output)
        else:
            self._extract_audio_single(output)

        if os.path.isfile(output):
            size_mb = os.path.getsize(output) / (1024 * 1024)
            log.info("音频提取完成: %s (%.1f MB)", output, size_mb)
        else:
            log.error("音频提取失败: 输出文件不存在")
        return output

    def _extract_audio_single(self, output: str) -> None:
        """单次提取音频。"""
        cmd = [
            self.ffmpeg, "-i", self.config.video_path,
            "-vn",                    # 无视频
            "-acodec", "pcm_s16le",   # PCM 16-bit
            "-ar", "16000",           # 16kHz
            "-ac", "1",               # 单声道
            "-y",                     # 覆盖
            output,
        ]
        self._run_ffmpeg(cmd, desc="音频提取")

    def _split_audio_segments(self, total_duration: float) -> list[str]:
        """将长视频切分为多段音频。"""
        segment_sec = self.config.max_segment_minutes * 60
        segments = []
        start = 0
        idx = 0
        while start < total_duration:
            seg_output = os.path.join(self.config.temp_dir, f"audio_seg_{idx:03d}.wav")
            cmd = [
                self.ffmpeg, "-i", self.config.video_path,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-ss", str(start),
                "-t", str(min(segment_sec, total_duration - start)),
                "-y",
                seg_output,
            ]
            self._run_ffmpeg(cmd, desc=f"音频分段 {idx+1}")
            if os.path.isfile(seg_output) and os.path.getsize(seg_output) > 1000:
                segments.append(seg_output)
            else:
                log.warning("分段 %d 输出为空，跳过", idx)
            start += segment_sec
            idx += 1
        return segments

    def _merge_audio_segments(self, segments: list[str], output: str) -> None:
        """合并多段音频。"""
        if not segments:
            raise RuntimeError("没有可合并的音频分段")
        if len(segments) == 1:
            os.replace(segments[0], output)
            return
        # 创建文件列表
        list_file = os.path.join(self.config.temp_dir, "segments.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for seg in segments:
                # ffmpeg concat 需要转义路径
                seg.replace("\\", "\\\\").replace("'", "'\\''")
                f.write(f"file '{seg}'\n")
        cmd = [
            self.ffmpeg, "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            "-y",
            output,
        ]
        self._run_ffmpeg(cmd, desc="音频分段合并")

    def extract_frames(self, frame_interval: float = 5.0) -> list[str]:
        """
        每隔 frame_interval 秒提取一帧 PNG。
        返回帧文件路径列表（按时间排序）。
        """
        duration = self.get_video_duration()
        output_pattern = os.path.join(self.config.frames_dir, "frame_%05d.png")
        # 计算帧数量
        num_frames = int(duration / frame_interval) + 1
        log.info("预计提取 %d 帧 (间隔 %.1f 秒)", num_frames, frame_interval)

        cmd = [
            self.ffmpeg, "-i", self.config.video_path,
            "-vf", f"fps=1/{frame_interval},scale=iw:ih",
            "-sws_flags", "lanczos",        # 高精度缩放算法
            "-y",
            output_pattern,
        ]
        self._run_ffmpeg(cmd, desc="帧提取")

        # 收集生成的文件，按文件名排序（天然时间序）
        frames = sorted(
            [f for f in os.listdir(self.config.frames_dir) if f.endswith(".png")]
        )
        log.info("帧提取完成: %d 张", len(frames))

        # 写入帧元数据（间隔、时间映射关系），供后续对照
        self._write_frame_metadata(frame_interval, frames)
        return frames

    def _write_frame_metadata(self, interval: float, frames: list) -> None:
        """在帧目录写入 _metadata.json，记录间隔和每帧对应的时间"""
        import json
        metadata = {
            "frame_interval_seconds": interval,
            "total_frames": len(frames),
            "frames": {}
        }
        for fname in frames[:5]:  # 只记录前5帧作为示例
            match = re.search(r"(\d+)", fname)
            if match:
                idx = int(match.group(1)) - 1
                ts = idx * interval
                mins, secs = divmod(int(ts), 60)
                metadata["frames"][fname] = f"{mins:02d}:{secs:02d}"
        metadata["note"] = f"帧号 = (时间秒数 / {interval}) + 1，例: frame_00001 = 0秒"
        meta_path = os.path.join(self.config.frames_dir, "_metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def dedup_frames(self, threshold: float = 0.92) -> int:
        """
        对已有帧进行差异哈希去重。移除相似帧，只保留第一张。
        threshold: 相似度阈值 0-1, 越高保留越多
        返回移除的帧数量。
        """
        frames_dir = self.config.frames_dir
        pngs = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
        if len(pngs) < 2:
            return 0

        try:
            from PIL import Image
        except ImportError:
            log.warning("PIL 未安装，跳过帧去重")
            return 0

        def _dhash(img_path, hash_size=8):
            img = Image.open(img_path).convert("L").resize(
                (hash_size + 1, hash_size), Image.LANCZOS)
            diff = []
            for row in range(hash_size):
                for col in range(hash_size):
                    left = img.getpixel((col, row))
                    right = img.getpixel((col + 1, row))
                    diff.append(1 if left > right else 0)
            return sum(b << i for i, b in enumerate(diff))

        hashes = {}
        removed = 0
        hash_size = 64  # 8x8 dhash → 64位
        max_dist = int(hash_size * (1 - threshold))

        for fname in pngs:
            fpath = os.path.join(frames_dir, fname)
            try:
                h = _dhash(fpath)
            except Exception as e:
                log.warning("哈希计算失败 %s: %s", fname, e)
                continue

            is_dup = False
            for kept_hash in list(hashes.values()):
                if (h ^ kept_hash).bit_count() <= max_dist:
                    is_dup = True
                    break

            if is_dup:
                os.remove(fpath)
                removed += 1
            else:
                hashes[fname] = h

        # 保留原始编号（frame_00001.png 中的 00001 编码了时间位置）
        # 不重命名，避免丢失时间信息
        kept = sorted(hashes.keys())

        log.info("帧去重: 移除 %d 张, 保留 %d 张", removed, len(kept))
        return removed

    def _run_ffmpeg(self, cmd: list[str], desc: str = "") -> None:
        """执行 ffmpeg 命令并记录日志。"""
        log.info("ffmpeg %s: %s", desc, " ".join(cmd[:6]) + "...")
        try:
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            _stdout, stderr = process.communicate(timeout=3600)
            if process.returncode != 0:
                err_text = stderr.decode("utf-8", errors="replace")[-1000:]
                raise RuntimeError(f"ffmpeg {desc} 失败 (code {process.returncode}): {err_text}")
        except subprocess.TimeoutExpired:
            process.kill()
            raise RuntimeError(f"ffmpeg {desc} 超时")


# ============================================================
# 语音转文字
# ============================================================

class Transcriber:
    """
    语音识别引擎。
    主引擎：SenseVoice (funasr)
    备用引擎：openai-whisper
    自动降级。
    """

    def __init__(self, config: Config):
        self.config = config
        self.engine: str | None = None  # "sensevoice" or "whisper"
        self.model = None

    def transcribe(self, audio_path: str) -> list[dict[str, Any]]:
        """转录音频，返回 [{timestamp, text}, ...] 格式。"""
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        # 优先 SenseVoice
        try:
            log.info("尝试使用 SenseVoice 进行语音识别...")
            result = self._transcribe_sensevoice(audio_path)
            self.engine = "sensevoice"
            log.info("SenseVoice 转录完成: %d 条", len(result))
            return result
        except Exception as e:
            log.warning("SenseVoice 失败: %s", e)
            log.info("降级至 openai-whisper...")

        try:
            result = self._transcribe_whisper(audio_path)
            self.engine = "whisper"
            log.info("Whisper 转录完成: %d 条", len(result))
            return result
        except Exception as e:
            raise RuntimeError(f"所有语音识别引擎均失败: {e}") from e

    def _get_audio_duration(self, audio_path: str) -> float:
        """获取音频文件时长（秒）"""
        try:
            result = subprocess.run(
                [self.config.ffmpeg_path, "-i", audio_path, "-f", "null", "-"],
                capture_output=True, text=False, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            stderr = result.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr or "")
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
        except Exception:
            pass
        raise RuntimeError(f"无法获取音频时长: {audio_path}")

    def _transcribe_sensevoice(self, audio_path: str) -> list[dict[str, Any]]:
        """使用 SenseVoice 转录。"""
        try:
            from funasr import AutoModel
        except ImportError:
            raise ImportError("请安装 funasr: pip install funasr modelscope")

        if self.model is None:
            log.info("正在加载 SenseVoice 模型 (首次运行会自动下载 ~200MB)...")
            self.model = AutoModel(
                model="iic/SenseVoiceSmall",
                vad_model="fsmn-vad",
                device="cpu",
            )
            log.info("SenseVoice 模型加载完成")

        # 分段转录：把音频切成30秒一段，每段单独转写，确保有时间戳
        duration = self._get_audio_duration(audio_path)
        CHUNK = 30  # 每段30秒
        num_chunks = max(1, int(duration / CHUNK))
        log.info("音频 %.0f 秒, 分 %d 段转录", duration, num_chunks)

        _tag_pattern = re.compile(r"<\|[^|]+\|>")
        all_segments = []

        for ci in range(num_chunks):
            start = ci * CHUNK
            end = min(start + CHUNK, duration)

            # ffmpeg 截取当前段（内存管道）
            import subprocess as sp
            cmd = [self.config.ffmpeg_path, "-i", audio_path, "-ss", str(start), "-to", str(end),
                   "-f", "wav", "-ac", "1", "-ar", "16000", "-"]
            try:
                proc = sp.run(cmd, capture_output=True, timeout=120,
                    creationflags=sp.CREATE_NO_WINDOW if hasattr(sp, 'CREATE_NO_WINDOW') else 0)
                if len(proc.stdout) < 1000:
                    continue
            except Exception:
                continue

            # SenseVoice 转写当前段
            temp_path = f"{audio_path}.c{ci}.wav"
            try:
                with open(temp_path, "wb") as f:
                    f.write(proc.stdout)
                result = self.model.generate(input=temp_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            if not isinstance(result, list):
                continue
            for item in result:
                ts = 0.0
                raw = ""
                if isinstance(item, dict):
                    ts = float(item.get("timestamp", 0) or item.get("start", 0) or 0)
                    raw = item.get("text", "")
                elif hasattr(item, "timestamp") and hasattr(item, "text"):
                    ts = float(item.timestamp)
                    raw = item.text
                text = _tag_pattern.sub("", raw).strip()
                if text and len(text) >= 2 and text != "<|nospeech|>":
                    all_segments.append({
                        "timestamp": round(start + ts, 1),
                        "text": text,
                    })

        log.info("SenseVoice 转录完成: %d 条", len(all_segments))
        return all_segments

    def _transcribe_whisper(self, audio_path: str) -> list[dict[str, Any]]:
        """使用 openai-whisper 转录（备用）。"""
        try:
            import whisper
        except ImportError:
            raise ImportError("请安装 openai-whisper: pip install openai-whisper")

        if self.model is None:
            log.info("正在加载 Whisper 模型...")
            self.model = whisper.load_model("base", device="cpu")
            log.info("Whisper 模型加载完成")

        result = self.model.transcribe(audio_path, language="zh")
        segments = []
        for seg in result.get("segments", []):
            text = seg.get("text", "").strip()
            if text:
                segments.append({
                    "timestamp": seg.get("start", 0),
                    "text": text,
                })
        return segments


# ============================================================
# 帧分析器
# ============================================================

def _encode_image_base64(image_path: str) -> str:
    """将图片编码为 base64 data URL。"""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(image_path).suffix.lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        mime = "image/jpeg"
    elif ext == "png":
        mime = "image/png"
    else:
        mime = f"image/{ext}"
    return f"data:{mime};base64,{data}"


FRAME_CLASSIFY_FAST = "这张截图是否包含K线图/走势图？只回复chart或other:"
FRAME_ANALYSIS_PROMPT = """你是一个交易教学视频的截图分析专家。你的任务是用文字精确描述这张截图中的K线图，使读者能据此在纸上重建出与原图比例一致的K线图。

重要：图中没有精确数值，请用**相对比例**描述。**描述每一根可见K线，不要遗漏**。

输出格式：

画面类型：【图表】或【非图表】

如果是【图表】，按以下模板详细描述：

【整体布局】画面分区，主图位置，指标图

【K线图参数-逐根详细】
- 可见K线总数量：约___根
- 逐根描述（从左到右第1根、第2根...第N根，全部列出）：
  每根的：阴阳/实体比例/影线长度/高低点相对位置/与前一根的关系/特殊形态

【走势形态】
- 整体趋势方向及角度
- 关键转折点（波峰/波谷位置）
- 技术形态识别（头肩顶/底、双顶/底、旗形、三角、楔形、通道等），标注各部位位置
- 趋势线/通道/支撑阻力线的位置和角度

【画线标注】老师画的线、箭头、圈、文字标注

【技术指标】均线排列/MACD位置/RSI范围/成交量逐根对比

注意：不做投资建议，只做客观视觉描述。如果画面不是K线图，简单说明画面内容即可。"""


class FrameAnalyzer:
    """
    画面分析引擎。
    主引擎：GLM-4V (Zhipu API, OpenAI 兼容协议)
    备用引擎：Gemini 2.0 Flash API
    支持批量并行分析。
    """

    def __init__(self, config: Config):
        self.config = config
        self.engine: str | None = None
        self._zhipu_client = None
        self._gemini_client = None

    def analyze_batch(self, frames: list[tuple[str, str]],
                      checkpoint: CheckpointManager) -> list[tuple[str, str, str | None]]:
        """
        批量分析帧。
        frames: [(filename, full_path), ...]
        returns: [(filename, analysis_text_or_None, error_or_None), ...]
        """
        if not frames:
            return []

        results: list[tuple[str, str, str | None]] = []

        # 先尝试 GLM-4V
        if self.config.zhipu_api_key:
            try:
                log.info("使用 GLM-4V 分析 %d 帧...", len(frames))
                results = self._analyze_with_zhipu(frames, checkpoint)
                if results:
                    self.engine = "zhipu-glm4v"
                    return results
            except Exception as e:
                log.warning("GLM-4V 分析失败: %s", e)

        # 降级到 Gemini
        if self.config.gemini_api_key:
            try:
                log.info("使用 Gemini 分析 %d 帧...", len(frames))
                results = self._analyze_with_gemini(frames, checkpoint)
                if results:
                    self.engine = "gemini"
                    return results
            except Exception as e:
                log.warning("Gemini 分析失败: %s", e)

        if not results:
            raise RuntimeError("所有画面分析引擎均不可用 — 请检查 API Key 设置")

        return results

    # ---- Zhipu GLM-4V (OpenAI 兼容协议) ----

    def _get_zhipu_client(self):
        if self._zhipu_client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError("请安装 openai: pip install openai")
            self._zhipu_client = OpenAI(
                api_key=self.config.zhipu_api_key,
                base_url="https://open.bigmodel.cn/api/paas/v4/",
            )
        return self._zhipu_client

    def _analyze_with_zhipu(self, frames: list[tuple[str, str]],
                             checkpoint: CheckpointManager) -> list[tuple[str, str, str | None]]:
        client = self._get_zhipu_client()
        results = []

        # 并行处理帧
        with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
            future_map = {}
            for filename, full_path in frames:
                future = executor.submit(
                    self._analyze_single_zhipu, client, full_path,
                    self.config.max_retries,
                )
                future_map[future] = filename

            for future in as_completed(future_map):
                filename = future_map[future]
                try:
                    analysis = future.result()
                    results.append((filename, analysis, None))
                    # 从文件名解析时间戳（frame_00001.png → 00001*interval）
                    try:
                        idx = int(re.search(r"(\d+)", filename).group(1)) - 1
                        ts = idx * self.config.frame_interval
                    except (AttributeError, ValueError):
                        ts = 0.0
                    checkpoint.mark_frame_analyzed(filename, analysis, None, ts)
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    results.append((filename, None, err_msg))
                    checkpoint.mark_frame_analyzed(filename, None, err_msg, 0.0)

        return results

    def _analyze_single_zhipu(self, client, image_path: str, retries: int) -> str:
        """单张帧的 GLM-4V 分析（含重试）。"""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                b64_image = _encode_image_base64(image_path)
                response = client.chat.completions.create(
                    model=self.config.zhipu_model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": FRAME_ANALYSIS_PROMPT},
                                {"type": "image_url", "image_url": {"url": b64_image}},
                            ],
                        }
                    ],
                    max_tokens=1024,
                    temperature=0.1,
                    timeout=60,
                )
                text = response.choices[0].message.content.strip()
                if text:
                    return text
                raise ValueError("返回内容为空")
            except Exception as e:
                last_error = e
                if attempt < retries:
                    wait = min(2 ** attempt * 2, 30)  # 指数退避
                    log.warning("GLM-4V 调用失败 (尝试 %d/%d): %s — %d 秒后重试",
                                attempt, retries, e, wait)
                    time.sleep(wait)
                else:
                    log.error("GLM-4V 调用失败 (已达最大重试次数 %d): %s", retries, e)
        raise RuntimeError(f"GLM-4V 分析失败: {last_error}")

    # ---- Gemini 备用 ----

    def _get_gemini_client(self):
        if self._gemini_client is None:
            try:
                import google.generativeai as genai
            except ImportError:
                raise ImportError("请安装 google-generativeai: pip install google-generativeai")
            genai.configure(api_key=self.config.gemini_api_key)
            self._gemini_client = genai
        return self._gemini_client

    def _analyze_with_gemini(self, frames: list[tuple[str, str]],
                              checkpoint: CheckpointManager) -> list[tuple[str, str, str | None]]:
        genai = self._get_gemini_client()
        model = genai.GenerativeModel(self.config.gemini_model)
        results = []

        with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
            future_map = {}
            for filename, full_path in frames:
                future = executor.submit(
                    self._analyze_single_gemini, model, full_path,
                    self.config.max_retries,
                )
                future_map[future] = filename

            for future in as_completed(future_map):
                filename = future_map[future]
                try:
                    analysis = future.result()
                    results.append((filename, analysis, None))
                    try:
                        idx = int(re.search(r"(\d+)", filename).group(1)) - 1
                        ts = idx * self.config.frame_interval
                    except (AttributeError, ValueError):
                        ts = 0.0
                    checkpoint.mark_frame_analyzed(filename, analysis, None, ts)
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    results.append((filename, None, err_msg))
                    checkpoint.mark_frame_analyzed(filename, None, err_msg, 0.0)

        return results

    def _analyze_single_gemini(self, model, image_path: str, retries: int) -> str:
        """单张帧的 Gemini 分析（含重试）。"""
        import google.generativeai as genai

        last_error = None
        for attempt in range(1, retries + 1):
            try:
                image_file = genai.upload_file(image_path)
                response = model.generate_content(
                    [FRAME_ANALYSIS_PROMPT, image_file],
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=1024,
                        temperature=0.1,
                    ),
                    request_options={"timeout": 60},
                )
                text = response.text.strip()
                if text:
                    return text
                raise ValueError("返回内容为空")
            except Exception as e:
                last_error = e
                if attempt < retries:
                    wait = min(2 ** attempt * 2, 30)
                    log.warning("Gemini 调用失败 (尝试 %d/%d): %s — %d 秒后重试",
                                attempt, retries, e, wait)
                    time.sleep(wait)
                else:
                    log.error("Gemini 调用失败 (已达最大重试次数): %s", e)
        raise RuntimeError(f"Gemini 分析失败: {last_error}")


# ============================================================
# 知识合并器
# ============================================================

class KnowledgeMerger:
    """
    按时间轴对齐语音和画面描述，生成结构化 Markdown 知识文档。
    """

    def __init__(self, config: Config):
        self.config = config

    def merge(self,
              video_name: str,
              transcription: list[dict[str, Any]],
              frame_results: dict[str, dict],
              frame_interval: float) -> str:
        """
        合并语音 + 画面，生成 Markdown 内容并写出文件。
        返回输出文件路径。
        """
        sections = self._build_sections(transcription, frame_results, frame_interval)
        markdown = self._render_markdown(video_name, sections)
        output_path = self._write_output(video_name, markdown)
        return output_path

    def _build_sections(
        self,
        transcription: list[dict[str, Any]],
        frame_results: dict[str, dict],
        frame_interval: float,
    ) -> list[dict[str, Any]]:
        """
        构建对齐后的知识章节。
        策略：
        - 将音频分段为逻辑章节（按时间窗口分组，每组约 30-60 秒）
        - 每组关联该时段内的画面描述
        """
        if not transcription:
            # 没有语音时，按帧时间分段
            return self._build_from_frames_only(frame_results, frame_interval)

        # 按时间排序的语音段
        sorted_audio = sorted(transcription, key=lambda x: x["timestamp"])

        # 将帧结果按时间建立索引
        frame_by_time: dict[int, dict[str, Any]] = {}
        for fname, finfo in frame_results.items():
            try:
                idx = int(re.search(r"(\d+)", fname).group(1)) - 1
                ts = idx * frame_interval
                frame_by_time[int(ts)] = finfo
            except (AttributeError, ValueError, KeyError):
                pass

        # 将语音分组为章节（每段最长 60 秒）
        sections = []
        current_section = {"start": 0.0, "end": 0.0, "texts": [], "frame_indices": set()}
        section_max_duration = 60.0

        for seg in sorted_audio:
            ts = seg["timestamp"]

            # 如果当前章节为空，初始化
            if current_section["start"] == 0.0 and not current_section["texts"]:
                current_section["start"] = ts

            # 如果超过章节最大时长，或者时间跳跃太大（> 15 秒无内容），切分
            if (current_section["texts"]
                    and (ts - current_section["start"] > section_max_duration
                         or ts - current_section["end"] > 15.0)):
                sections.append(current_section)
                current_section = {"start": ts, "end": ts, "texts": [], "frame_indices": set()}

            current_section["texts"].append(seg["text"])
            current_section["end"] = ts

            # 关联该时间附近的帧
            frame_ts = int(ts / frame_interval) * int(frame_interval)
            for offset in range(-2, 3):  # ±2 帧窗口
                candidate = frame_ts + offset * int(frame_interval)
                if candidate in frame_by_time:
                    current_section["frame_indices"].add(candidate)

        # 最后一段
        if current_section["texts"]:
            sections.append(current_section)

        # 转换为最终格式
        result = []
        for sec in sections:
            frame_texts = []
            for ft in sorted(sec["frame_indices"]):
                finfo = frame_by_time.get(ft, {})
                analysis = finfo.get("result")
                if analysis:
                    frame_texts.append(analysis)

            result.append({
                "time_range": self._format_time_range(sec["start"], sec["end"]),
                "audio_texts": sec["texts"],
                "frame_analyses": frame_texts,
            })

        return result

    def _build_from_frames_only(self, frame_results: dict[str, dict],
                                 frame_interval: float) -> list[dict[str, Any]]:
        """无语音时，仅用帧分析构建章节。"""
        sections = []
        window_size = 5  # 每 5 帧一组
        sorted_frames = sorted(
            [(fname, finfo) for fname, finfo in frame_results.items()
             if finfo.get("status") == "done" and finfo.get("result")],
            key=lambda x: x[0],
        )

        for i in range(0, len(sorted_frames), window_size):
            group = sorted_frames[i:i + window_size]
            start_ts = i * frame_interval
            end_ts = (i + len(group)) * frame_interval
            analyses = [finfo["result"] for _, finfo in group if finfo.get("result")]
            if analyses:
                sections.append({
                    "time_range": self._format_time_range(start_ts, end_ts),
                    "audio_texts": [],
                    "frame_analyses": analyses,
                })

        return sections

    def _format_time_range(self, start_sec: float, end_sec: float) -> str:
        """将秒数转为 HH:MM:SS 格式。"""
        start = str(timedelta(seconds=int(start_sec)))
        end = str(timedelta(seconds=int(end_sec)))
        # 保证格式一致
        if start.startswith("0:"):
            start = "0" + start
        if end.startswith("0:"):
            end = "0" + end
        return f"{start} ~ {end}"

    def _render_markdown(self, video_name: str,
                          sections: list[dict[str, Any]]) -> str:
        """渲染 Markdown 内容。"""
        lines = [
            f"# 课程知识：{video_name}",
            "",
            "> 本文件由 extract_knowledge.py 自动生成，供 Claude 读取炼化。",
            f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
        ]

        for i, sec in enumerate(sections, 1):
            # 主题从首句提炼
            title = sec.get("time_range", f"第 {i} 段")
            first_text = sec["audio_texts"][0] if sec["audio_texts"] else ""
            topic = first_text[:40] + ("..." if len(first_text) > 40 else "") if first_text else f"章节 {i}"
            lines.append(f"## [{title}] {topic}")
            lines.append("")

            # 语音
            if sec["audio_texts"]:
                lines.append("### 🎙️ 老师说")
                lines.append("")
                for t in sec["audio_texts"]:
                    lines.append(t)
                    lines.append("")
            else:
                lines.append("### 🎙️ 老师说")
                lines.append("")
                lines.append("*（此段无声频内容）*")
                lines.append("")

            # 画面
            if sec["frame_analyses"]:
                lines.append("### 📊 画面显示")
                lines.append("")
                for j, analysis in enumerate(sec["frame_analyses"], 1):
                    lines.append(f"**截图 {j}：**")
                    lines.append("")
                    lines.append(analysis)
                    lines.append("")
            else:
                lines.append("### 📊 画面显示")
                lines.append("")
                lines.append("*（此段无画面内容）*")
                lines.append("")

            # 知识点占位（由 LLM 读取后自行提炼，或在此由视觉分析提取）
            lines.append("### 💡 知识点")
            lines.append("")
            # 尝试从语音和画面提取规则关键词
            knowledge_items = self._extract_knowledge_hints(sec["audio_texts"], sec["frame_analyses"])
            if knowledge_items:
                for item in knowledge_items:
                    lines.append(f"- {item}")
            else:
                lines.append("*（请 Claude 阅读后自行提炼本段知识点）*")
            lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _extract_knowledge_hints(self, audio_texts: list[str],
                                  frame_analyses: list[str]) -> list[str]:
        """从语音和画面中提取可能的知识点提示（简单规则匹配）。"""
        hints = set()
        all_text = " ".join(audio_texts) + " " + " ".join(frame_analyses)

        # 常见交易规则关键词
        patterns = {
            "入场条件": ["买入", "做多", "入场", "开仓", "进场", "买点", "做空"],
            "出场条件": ["卖出", "平仓", "止盈", "止损", "出场", "离场", "卖点"],
            "趋势判断": ["趋势", "上涨", "下跌", "震荡", "盘整", "突破", "反弹", "回调"],
            "风险控制": ["止损", "仓位", "风控", "风险", "资金管理"],
            "技术形态": ["头肩", "双顶", "双底", "旗形", "三角形", "楔形", "通道"],
            "指标用法": ["均线", "MACD", "RSI", "KDJ", "布林", "成交量", "量能"],
        }

        for category, keywords in patterns.items():
            for kw in keywords:
                if kw in all_text:
                    hints.add(f"{category}（检测到关键词「{kw}」）")
                    break

        return sorted(hints)

    def _write_output(self, video_name: str, markdown: str) -> str:
        """写出 Markdown 文件。"""
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", video_name)
        output_path = os.path.join(self.config.output_dir, f"{safe_name}_knowledge.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        log.info("知识文档已生成: %s (%d 字符)", output_path, len(markdown))
        return output_path


# ============================================================
# 失败帧重试器
# ============================================================

class FailedFrameRetrier:
    """对分析失败的帧进行重试。"""

    @staticmethod
    def collect_failed(checkpoint: CheckpointManager) -> list[tuple[str, str]]:
        """收集失败的帧。"""
        failed = []
        frames_dir = os.path.dirname(checkpoint.path) + "/frames"
        for fname, finfo in checkpoint.data["frame_results"].items():
            if finfo.get("status") == "failed":
                fpath = os.path.join(frames_dir, fname)
                if os.path.isfile(fpath):
                    failed.append((fname, fpath))
        return failed


# ============================================================
# 主流程编排
# ============================================================

class KnowledgeExtractor:
    """将视频转化为知识文档的主控制器。"""

    def __init__(self, config: Config):
        self.config = config
        self.checkpoint = CheckpointManager(config.checkpoint_path)
        self.video_processor = VideoProcessor(config)
        self.transcriber = Transcriber(config)
        self.frame_analyzer = FrameAnalyzer(config)
        self.merger = KnowledgeMerger(config)

        # 检查视频是否变更
        needs_reset = self.checkpoint.sync_video_info(config.video_path)
        if needs_reset:
            log.info("视频文件变更或首次运行，从头开始")

    def run(self) -> str:
        """
        执行完整的提取流程。
        返回输出 Markdown 文件路径。
        """
        video_name = Path(self.config.video_path).stem
        total_start = time.time()

        # ─── 第 1 阶段：预处理（音频 + 帧，并行） ──────────
        log.info("=" * 60)
        log.info("阶段 1/4：预处理（音频 + 帧）")
        log.info("=" * 60)

        # 音频提取和帧提取可以并行
        audio_future: Future | None = None
        frames: list[str] = []

        with ThreadPoolExecutor(max_workers=2) as executor:
            # 提交音频提取
            if self.checkpoint.needs_audio_extraction():
                audio_future = executor.submit(
                    self._extract_audio_safe,
                )
            else:
                log.info("[跳过] 音频已提取")

            # 提交帧提取
            if self.checkpoint.needs_frame_extraction():
                frames = self._extract_frames_safe()
                # 帧去重：移除相似帧
                removed = self.video_processor.dedup_frames(threshold=0.92)
                if removed > 0:
                    # 重新收集去重后的帧列表
                    frames = sorted(
                        f for f in os.listdir(self.config.frames_dir)
                        if f.endswith(".png")
                    )
                self.checkpoint.mark_frames_extracted(frames)
            else:
                frames = self.checkpoint.data.get("frame_list", [])
                log.info("[跳过] 帧已提取 (%d 张)", len(frames))

            # 等待音频完成
            if audio_future is not None:
                try:
                    audio_future.result()
                    self.checkpoint.mark_audio_extracted()
                except Exception as e:
                    log.error("音频提取失败: %s", e)
                    raise

        # ─── 第 2 阶段：语音转文字 ──────────────────────────
        log.info("=" * 60)
        log.info("阶段 2/4：语音转文字")
        log.info("=" * 60)

        transcription: list[dict[str, Any]] = []
        if self.checkpoint.needs_transcription():
            if os.path.isfile(self.config.audio_path):
                transcription = self.transcriber.transcribe(self.config.audio_path)
                self.checkpoint.mark_transcription_done(transcription)
                log.info("语音识别完成: %d 条", len(transcription))
            else:
                log.warning("音频文件不存在，跳过语音识别: %s", self.config.audio_path)
                self.checkpoint.mark_transcription_done([])
        else:
            transcription = self.checkpoint.data.get("transcription_result", [])
            log.info("[跳过] 语音识别已完成 (%d 条)", len(transcription))

        # ─── 第 3 阶段：画面分析 ────────────────────────────
        log.info("=" * 60)
        log.info("阶段 3/4：画面分析")
        log.info("=" * 60)

        if self.checkpoint.needs_frame_analysis() and frames:
            pending = self.checkpoint.get_pending_frames()
            log.info("需要分析的帧: %d (总帧: %d)", len(pending), len(frames))

            frames_dir = self.config.frames_dir
            pending_with_path = [
                (f, os.path.join(frames_dir, f))
                for f in pending
                if os.path.isfile(os.path.join(frames_dir, f))
            ]

            if pending_with_path:
                # 分批处理
                batch_size = max(1, self.config.batch_size)
                for batch_start in range(0, len(pending_with_path), batch_size):
                    batch = pending_with_path[batch_start:batch_start + batch_size]
                    log.info("分析批次 %d/%d (%d 帧)...",
                             batch_start // batch_size + 1,
                             (len(pending_with_path) + batch_size - 1) // batch_size,
                             len(batch))
                    self.frame_analyzer.analyze_batch(batch, self.checkpoint)

                # 重试失败的帧
                failed = FailedFrameRetrier.collect_failed(self.checkpoint)
                if failed:
                    log.info("重试 %d 张失败帧...", len(failed))
                    self.frame_analyzer.analyze_batch(failed, self.checkpoint)

            # 统计
            results = self.checkpoint.data["frame_results"]
            done = sum(1 for v in results.values() if v.get("status") == "done")
            failed = sum(1 for v in results.values() if v.get("status") == "failed")
            log.info("帧分析完成: 成功 %d, 失败 %d / 共 %d", done, failed, len(results))
        else:
            log.info("[跳过] 帧分析已完成或无需分析")

        # ─── 第 4 阶段：合并输出 ────────────────────────────
        log.info("=" * 60)
        log.info("阶段 4/4：合并输出")
        log.info("=" * 60)

        if self.checkpoint.needs_merge():
            frame_results = self.checkpoint.data.get("frame_results", {})
            output_path = self.merger.merge(
                video_name=video_name,
                transcription=transcription,
                frame_results=frame_results,
                frame_interval=self.config.frame_interval,
            )
            self.checkpoint.mark_merged(output_path)
        else:
            output_path = self.checkpoint.data.get("output_path", "")
            log.info("[跳过] 合并已完成: %s", output_path)

        elapsed = time.time() - total_start
        log.info("=" * 60)
        log.info("全部完成！耗时: %d 分 %d 秒", int(elapsed // 60), int(elapsed % 60))
        log.info("输出: %s", output_path)
        log.info("=" * 60)

        return output_path

    def _extract_audio_safe(self) -> str:
        """安全的音频提取包装。"""
        try:
            return self.video_processor.extract_audio()
        except Exception as e:
            log.error("音频提取失败: %s", e)
            raise

    def _extract_frames_safe(self) -> list[str]:
        """安全的帧提取包装。"""
        try:
            return self.video_processor.extract_frames(self.config.frame_interval)
        except Exception as e:
            log.error("帧提取失败: %s", e)
            raise


# ============================================================
# 命令行接口
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将交易教学视频（MP4）转化为结构化 Markdown 知识文档",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 基本用法（仅语音转录）
  python extract_knowledge.py --video 课程.mp4 --zhipu-api-key xxx

  # 完整功能（语音 + 画面分析）
  python extract_knowledge.py --video 课程.mp4 --zhipu-api-key xxx --gemini-api-key yyy

  # 指定输出目录和截图间隔
  python extract_knowledge.py --video 课程.mp4 --zhipu-api-key xxx --output-dir ./my_output --frame-interval 3

  # 使用 .env 文件配置（在脚本目录或当前目录放 .env 文件）
  # .env 内容:
  #   ZHIPU_API_KEY=xxx
  #   GEMINI_API_KEY=xxx
  #   VIDEO_PATH=课程.mp4

支持的环境变量（可通过 .env 文件设置）:
  ZHIPU_API_KEY    智谱 AI API Key（必需）
  GEMINI_API_KEY   Google Gemini API Key（可选，备用）
  VIDEO_PATH       视频文件路径
  OUTPUT_DIR       输出目录（默认 scripts/output）
  FRAME_INTERVAL   截图间隔秒数（默认 5）
  BATCH_SIZE       帧分析并行批次大小（默认 10）
  CONCURRENCY      线程池并发数（默认 4）
  MAX_RETRIES      API 调用最大重试次数（默认 3）
  FFMPEG_PATH      ffmpeg 可执行文件路径（默认从 PATH 找）
        """,
    )

    parser.add_argument("--video", dest="video_path",
                        help="输入 MP4 视频文件路径")
    parser.add_argument("--zhipu-api-key",
                        help="智谱 AI API Key（GLM-4V 画面分析）")
    parser.add_argument("--gemini-api-key",
                        help="Google Gemini API Key（备用画面分析）")
    parser.add_argument("--output-dir", dest="output_dir",
                        help="输出目录（默认: scripts/output）")
    parser.add_argument("--frame-interval", dest="frame_interval", type=float,
                        help="截图间隔（秒，默认 5）")
    parser.add_argument("--batch-size", dest="batch_size", type=int,
                        help="帧分析并行批次大小（默认 10）")
    parser.add_argument("--concurrency", type=int,
                        help="线程池并发数（默认 4）")
    parser.add_argument("--max-retries", type=int,
                        help="API 调用最大重试次数（默认 3）")
    parser.add_argument("--ffmpeg-path", dest="ffmpeg_path",
                        help="ffmpeg 可执行文件路径")
    parser.add_argument("--zhipu-model",
                        help="GLM-4V 模型名称（默认 glm-4v-flash）")
    parser.add_argument("--gemini-model",
                        help="Gemini 模型名称（默认 gemini-2.0-flash-exp）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="输出详细日志")
    parser.add_argument("--version", action="version",
                        version="extract_knowledge.py 1.0.0")

    return parser


def main() -> int:
    """入口函数。"""
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        log.debug("详细日志模式已开启")

    try:
        # 从环境变量 + CLI 参数构建配置
        config = Config.from_env_and_args(args)

        log.info("配置概要:")
        log.info("  视频: %s", config.video_path)
        log.info("  输出目录: %s", config.output_dir)
        log.info("  截图间隔: %.1f 秒", config.frame_interval)
        log.info("  并行批次: %d", config.batch_size)
        log.info("  并发数: %d", config.concurrency)
        log.info("  GLM-4V: %s", "已启用" if config.zhipu_api_key else "未配置")
        log.info("  Gemini: %s", "已启用" if config.gemini_api_key else "未配置")
        log.info("  临时目录: %s", config.temp_dir)

        extractor = KnowledgeExtractor(config)
        output_path = extractor.run()
        log.info("全部完成! 输出文件: %s", output_path)
        # 避免 Windows GBK 终端无法打印 emoji
        safe_msg = f"\n知识文档已生成: {output_path}"
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
        print(safe_msg)
        return 0

    except (FileNotFoundError, ValueError, RuntimeError) as e:
        log.error("❌ %s", e)
        return 1
    except KeyboardInterrupt:
        log.info("\n⏹ 用户中断，进度已保存至检查点文件，下次运行可继续")
        return 130
    except Exception:
        log.exception("❌ 未预期的错误")
        return 2


if __name__ == "__main__":
    sys.exit(main())
