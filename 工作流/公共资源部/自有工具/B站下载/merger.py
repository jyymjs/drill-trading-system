"""音视频合并模块 — 检测 FFmpeg 并通过流复制合并 m4s 为 mp4"""

import os
import shutil
import subprocess
import sys
from typing import Optional


def find_ffmpeg() -> Optional[str]:
    """查找系统中可用的 ffmpeg 路径"""
    # 方式1: PATH 环境变量
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    # 方式2: 常见安装路径（Windows）
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path

    return None


def check_ffmpeg() -> bool:
    """检查 FFmpeg 是否可用"""
    path = find_ffmpeg()
    if not path:
        return False
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_ffmpeg_path() -> str:
    """获取 FFmpeg 路径，不可用时抛出异常"""
    path = find_ffmpeg()
    if not path:
        raise RuntimeError(
            "未找到 FFmpeg！\n"
            "请安装 FFmpeg:\n"
            "  - Windows: https://ffmpeg.org/download.html\n"
            "  - 或使用包管理器: winget install FFmpeg\n"
            "  - 下载后确保 ffmpeg.exe 在 PATH 中"
        )
    return path


def merge_video_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    delete_source: bool = True,
    ffmpeg_path: Optional[str] = None,
) -> bool:
    """
    使用 FFmpeg 流复制方式合并视频和音频为 mp4

    参数:
        video_path:  视频 m4s 文件路径
        audio_path:  音频 m4s 文件路径
        output_path: 输出 mp4 文件路径
        delete_source: 合并成功后是否删除源文件
        ffmpeg_path: FFmpeg 可执行文件路径（None=自动查找）

    返回:
        True=合并成功, False=失败
    """
    if not ffmpeg_path:
        try:
            ffmpeg_path = get_ffmpeg_path()
        except RuntimeError as e:
            print(f"  ⚠ {e}", file=sys.stderr)
            return False

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    cmd = [
        ffmpeg_path,
        "-i", video_path,
        "-i", audio_path,
        "-c", "copy",       # 流复制，不重新编码
        "-y",               # 覆盖输出文件
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,    # 最长10分钟
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            print(f"  ⚠ FFmpeg 合并失败: {stderr[:200]}", file=sys.stderr)
            return False

        # 合并成功，清理源文件
        if delete_source:
            try:
                os.remove(video_path)
                os.remove(audio_path)
            except OSError:
                pass
        return True

    except subprocess.TimeoutExpired:
        print("  ⚠ FFmpeg 超时（超过10分钟）", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ⚠ FFmpeg 执行异常: {e}", file=sys.stderr)
        return False


def merge_flv_segments(
    flv_paths: list[str],
    output_path: str,
    delete_source: bool = True,
    ffmpeg_path: Optional[str] = None,
) -> bool:
    """
    合并多个 FLV 文件（旧版B站视频格式）

    参数:
        flv_paths:   FLV 文件路径列表
        output_path: 输出文件路径
        delete_source: 合并成功后是否删除源文件
        ffmpeg_path: FFmpeg 可执行文件路径

    返回:
        True=合并成功, False=失败
    """
    if not ffmpeg_path:
        try:
            ffmpeg_path = get_ffmpeg_path()
        except RuntimeError as e:
            print(f"  ⚠ {e}", file=sys.stderr)
            return False

    # 创建文件列表
    list_path = output_path + ".list.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for fp in flv_paths:
                f.write(f"file '{os.path.abspath(fp)}'\n")

        cmd = [
            ffmpeg_path,
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-y",
            output_path,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )

        if result.returncode != 0:
            print(f"  ⚠ FLV 合并失败: {result.stderr.strip()[:200]}", file=sys.stderr)
            return False

        if delete_source:
            for fp in flv_paths:
                try:
                    os.remove(fp)
                except OSError:
                    pass

        return True

    finally:
        # 清理临时列表文件
        if os.path.exists(list_path):
            try:
                os.remove(list_path)
            except OSError:
                pass
