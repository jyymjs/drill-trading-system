"""音频提取模块 — 使用 FFmpeg 从视频中提取音频"""

import os
import shutil
import subprocess
import sys
from typing import Optional

from .merger import check_ffmpeg, get_ffmpeg_path


# 提取超时（秒），应对超长视频
EXTRACT_TIMEOUT = 1800  # 30分钟


def extract_audio(
    video_path: str,
    output_path: str,
    sample_rate: int = 16000,
    channels: int = 1,
    ffmpeg_path: Optional[str] = None,
) -> bool:
    """
    从视频文件中提取音频为 WAV 格式。

    DashScope paraformer 推荐配置:
        16kHz 采样率, 单声道, PCM 16-bit WAV

    参数:
        video_path:  视频文件路径（.mp4 / .m4s / .flv 等）
        output_path: 输出 WAV 文件路径
        sample_rate: 采样率（默认 16000Hz）
        channels:    声道数（默认 1=单声道）
        ffmpeg_path: FFmpeg 路径（None=自动查找）

    返回:
        True=成功, False=失败
    """
    if not os.path.isfile(video_path):
        print(f"  ⚠ 视频文件不存在: {video_path}", file=sys.stderr)
        return False

    if not ffmpeg_path:
        try:
            ffmpeg_path = get_ffmpeg_path()
        except RuntimeError as e:
            print(f"  ⚠ {e}", file=sys.stderr)
            return False

    if not check_ffmpeg():
        print(f"  ⚠ FFmpeg 不可用，无法提取音频", file=sys.stderr)
        return False

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    cmd = [
        ffmpeg_path,
        "-i", video_path,
        "-vn",                     # 丢弃视频流
        "-acodec", "pcm_s16le",    # PCM 16-bit 小端
        "-ar", str(sample_rate),   # 采样率
        "-ac", str(channels),      # 声道数
        "-y",                      # 覆盖输出
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=EXTRACT_TIMEOUT,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            print(f"  ⚠ 音频提取失败: {stderr[:300]}", file=sys.stderr)
            return False

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            print(f"  ⚠ 音频提取结果为空文件", file=sys.stderr)
            return False

        return True

    except subprocess.TimeoutExpired:
        print(f"  ⚠ 音频提取超时（超过30分钟）", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ⚠ 音频提取异常: {e}", file=sys.stderr)
        return False


def get_audio_duration(audio_path: str, ffmpeg_path: Optional[str] = None) -> Optional[float]:
    """
    获取音频文件的时长（秒）。

    可用于转写前的预估和进度显示。
    """
    if not os.path.isfile(audio_path):
        return None

    if not ffmpeg_path:
        try:
            ffmpeg_path = get_ffmpeg_path()
        except RuntimeError:
            return None

    # 尝试用 ffprobe
    ffprobe = _find_ffprobe(ffmpeg_path)
    if not ffprobe:
        return None

    try:
        cmd = [
            ffprobe, "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass

    return None


def _find_ffprobe(ffmpeg_path: str) -> Optional[str]:
    """查找 ffprobe 可执行文件"""
    # 方式1: 与 ffmpeg 同目录
    base = os.path.dirname(ffmpeg_path)
    if base:
        ffprobe = os.path.join(base, "ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if os.path.isfile(ffprobe):
            return ffprobe
    # 方式2: PATH 中查找
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe
    return None
