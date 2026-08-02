"""下载引擎 — 流式下载 m4s 音视频文件，带进度显示和重试机制"""

import os
import sys
import time
from typing import Callable, Optional

import requests

from .models import StreamItem

# 下载块大小（8KB）
CHUNK_SIZE = 8 * 1024

# 最大重试次数
MAX_RETRIES = 3

# 重试等待基数（秒，指数退避）
RETRY_BASE_DELAY = 2


def download_stream(
    stream_item: StreamItem,
    filepath: str,
    headers: Optional[dict] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    session: Optional[requests.Session] = None,
) -> bool:
    """
    下载单个流（视频或音频）到文件

    参数:
        stream_item: 流信息（含URL）
        filepath:    保存路径
        headers:     额外请求头
        progress_callback: 进度回调 (已下载字节, 总字节)
        session:     可复用 Session

    返回:
        True=成功, False=所有重试均失败
    """
    req_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com",
    }
    if headers:
        req_headers.update(headers)

    close_session = False
    if session is None:
        session = requests.Session()
        session.headers.update(req_headers)
        close_session = True
    else:
        # 确保 session 有必要的 headers
        for k, v in req_headers.items():
            if k not in session.headers:
                session.headers[k] = v

    # 优先使用 base_url，失败时尝试 url
    urls = []
    if stream_item.base_url:
        urls.append(stream_item.base_url)
    if stream_item.url and stream_item.url != stream_item.base_url:
        urls.append(stream_item.url)

    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        for url in urls:
            try:
                resp = session.get(url, stream=True, timeout=30)
                resp.raise_for_status()

                total_size = int(resp.headers.get("content-length", 0))
                downloaded = 0

                os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

                with open(filepath, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total_size > 0:
                                progress_callback(downloaded, total_size)

                # 下载完成
                if total_size > 0 and downloaded < total_size:
                    raise RuntimeError(f"下载不完整: {downloaded}/{total_size}")

                if close_session:
                    session.close()
                return True

            except Exception as e:
                last_error = str(e)
                # 清理不完整的文件
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
                continue

        # 所有 URL 都失败，等待重试
        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY ** attempt
            print(f"  下载失败，{delay}秒后重试 ({attempt}/{MAX_RETRIES})...", file=sys.stderr)
            time.sleep(delay)

    if close_session:
        session.close()

    print(f"  ❌ 下载失败 (已重试{MAX_RETRIES}次): {last_error}", file=sys.stderr)
    return False


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def show_progress(downloaded: int, total: int):
    """简单的进度条显示"""
    pct = downloaded / total * 100
    bar_len = 30
    filled = int(bar_len * downloaded / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(
        f"\r  {bar} {pct:5.1f}%  {format_size(downloaded)}/{format_size(total)}",
        end="",
        flush=True,
    )
    if downloaded >= total:
        print()
