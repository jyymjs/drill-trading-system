"""命令行入口 — 参数解析 + 主流程编排"""

import argparse
import os
import re
import sys

from .api import (
    extract_bvid,
    get_play_info,
    get_quality_name,
    get_video_info,
)
from .downloader import download_stream, show_progress, format_size
from .merger import check_ffmpeg, merge_video_audio, merge_flv_segments
from .audio import extract_audio
from .transcriber import (
    TranscriberError,
    TranscriberConfigError,
    TranscriberAPIError,
    DashScopeTranscriber,
)

from . import __version__


def parse_pages(pages_str: str, total_pages: int) -> list[int]:
    """
    解析分P选择字符串

    支持格式:
        "all"      → 全部分P
        "1"        → 第一P
        "1,3,5"    → 第1/3/5P
        "1-5"      → 第1~5P
        "1-5,7,9"  → 混合
    """
    if pages_str.lower() == "all":
        return list(range(1, total_pages + 1))

    pages = set()
    parts = re.split(r"[,\s]+", pages_str.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(\d+)-(\d+)$", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            pages.update(range(start, end + 1))
        elif re.match(r"^\d+$", part):
            pages.add(int(part))
        else:
            print(f"  ⚠ 无法识别的分P格式: {part}")

    result = sorted(p for p in pages if 1 <= p <= total_pages)
    if not result:
        print(f"  ⚠ 没有有效的分P选择，默认使用第1P")
        result = [1]
    return result


def parse_batch_file(filepath: str) -> list[str]:
    """读取批量文件，每行一个BV号或链接"""
    bvs = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            bv = extract_bvid(line)
            if bv:
                bvs.append(bv)
            else:
                print(f"  ⚠ 无法识别的行（跳过）: {line}", file=sys.stderr)
    return bvs


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """清理文件名，去掉非法字符"""
    # Windows 文件名非法字符
    invalid = r'[<>:"/\\|?*\x00-\x1f]'
    name = re.sub(invalid, "", name)
    name = name.strip().rstrip(". ")
    if not name:
        name = "untitled"
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name


def download_single_video(
    bvid: str,
    output_dir: str,
    qn: int,
    pages: list[int],
    cookie: str,
    no_merge: bool,
) -> bool:
    """
    下载单个视频（含多P处理）

    返回: True=全部成功, False=部分失败
    """
    print(f"\n📺 正在获取视频信息: {bvid}")

    try:
        info = get_video_info(bvid, cookie=cookie)
    except RuntimeError as e:
        print(f"  ❌ {e}")
        return False

    print(f"  标题: {info.title}")
    print(f"  UP主: {info.owner_name}")
    print(f"  分P数: {len(info.pages)}")

    if not info.pages:
        print(f"  ⚠ 该视频没有分P，跳过")
        return False

    # 确定要下载的P
    download_pages = pages
    if not download_pages or max(download_pages) > len(info.pages):
        download_pages = [1]
        print(f"  默认下载第1P")

    selected = [p for p in info.pages if p.page in download_pages]
    print(f"  下载分P: {', '.join(str(p.page) for p in selected)}")

    # 检查 FFmpeg
    has_ffmpeg = check_ffmpeg()
    if not no_merge and not has_ffmpeg:
        print(
            "  ⚠ 未检测到 FFmpeg，下载后不会自动合并！\n"
            "    需要手动用 FFmpeg 合并 video.m4s + audio.m4s\n"
            "    或安装 FFmpeg: https://ffmpeg.org/download.html"
        )
    elif not no_merge and has_ffmpeg:
        print("  ✅ 已检测到 FFmpeg")

    all_success = True

    for page in selected:
        page_dir = os.path.join(output_dir, sanitize_filename(info.title))
        os.makedirs(page_dir, exist_ok=True)

        # 文件名：标题_P页码_分P标题.mp4（有分P时）或 标题.mp4（单P）
        if len(selected) > 1:
            page_suffix = f"_P{page.page}_{sanitize_filename(page.part)}"
        else:
            page_suffix = ""
        base_name = sanitize_filename(info.title) + page_suffix
        mp4_path = os.path.join(page_dir, base_name + ".mp4")
        video_path = os.path.join(page_dir, base_name + "_video.m4s")
        audio_path = os.path.join(page_dir, base_name + "_audio.m4s")

        # 如果最终文件已存在，跳过
        if os.path.isfile(mp4_path):
            size = format_size(os.path.getsize(mp4_path))
            print(f"\n  ⏭ P{page.page} 已存在: {base_name}.mp4 ({size})")
            continue

        print(f"\n  📥 P{page.page}: {page.part or page.title}")

        # 获取播放流
        try:
            play = get_play_info(bvid, page.cid, qn=qn, cookie=cookie)
        except RuntimeError as e:
            print(f"    ❌ 获取播放流失败: {e}")
            all_success = False
            continue

        if not play.dash and play.videos:
            # 旧版 FLV 格式
            flv_paths = []
            for idx, stream in enumerate(play.videos):
                flv_path = os.path.join(page_dir, f"{base_name}_seg{idx}.flv")
                print(f"    ⬇ 下载 FLV 分段 {idx+1}/{len(play.videos)}...")
                ok = download_stream(
                    stream, flv_path,
                    progress_callback=show_progress,
                )
                if ok:
                    flv_paths.append(flv_path)
                else:
                    all_success = False
                    break

            if flv_paths and not no_merge and has_ffmpeg:
                print(f"    🔗 合并 FLV...")
                if merge_flv_segments(flv_paths, mp4_path):
                    print(f"    ✅ 合并完成: {base_name}.mp4")
                else:
                    print(f"    ⚠ 保留 FLV 分段文件")
                    all_success = False

        elif play.dash:
            # 现代 DASH 格式
            best_video = play.best_video
            best_audio = play.best_audio

            if not best_video:
                print(f"    ⚠ 无可用的视频流")
                all_success = False
                continue

            # 下载视频流
            print(f"    ⬇ 下载视频 ({get_quality_name(play.quality)})...")
            v_ok = download_stream(
                best_video, video_path,
                progress_callback=show_progress,
            )

            # 下载音频流
            a_ok = False
            if best_audio:
                print(f"    ⬇ 下载音频...")
                a_ok = download_stream(
                    best_audio, audio_path,
                    progress_callback=show_progress,
                )

            if v_ok and a_ok:
                if not no_merge and has_ffmpeg:
                    print(f"    🔗 合并音视频...")
                    if merge_video_audio(video_path, audio_path, mp4_path):
                        print(f"    ✅ 完成: {base_name}.mp4")
                    else:
                        print(f"    ⚠ 合并失败，保留 m4s 文件")
                        all_success = False
                elif no_merge:
                    print(f"    ✅ 下载完成（未合并）")
                else:
                    print(f"    ✅ 下载完成（未检测到 FFmpeg）")
            elif v_ok and not a_ok:
                print(f"    ⚠ 视频下载成功，音频下载失败")
                all_success = False
            else:
                print(f"    ❌ 视频下载失败")
                all_success = False
        else:
            print(f"    ⚠ 无可用播放流")
            all_success = False

    return all_success


def initialize_transcriber(args) -> "DashScopeTranscriber":
    """根据 CLI 参数初始化转写引擎"""
    engine = getattr(args, "transcribe_engine", "dashscope")
    api_key = getattr(args, "dashscope_api_key", None)

    if engine == "dashscope":
        return DashScopeTranscriber(api_key=api_key)
    else:
        # 从注册表查找（预留扩展）
        from .transcriber import get_transcriber
        return get_transcriber(engine, api_key=api_key)


def handle_transcribe_only(args):
    """
    --transcribe-only 模式的完整流程:
    1. 检查输入文件存在
    2. 如果是视频文件，提取音频
    3. 初始化转写引擎
    4. 调用转写
    5. 保存结果
    """
    filepath = args.transcribe_only
    if not os.path.isfile(filepath):
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    # 确定输出路径
    if args.output and args.output != "./download":
        output_txt = args.output
    else:
        stem, _ = os.path.splitext(filepath)
        output_txt = stem + ".txt"

    # 检查 FFmpeg
    if not check_ffmpeg():
        print("⚠ 未检测到 FFmpeg，无法从视频提取音频！")
        sys.exit(1)

    # 初始化转写引擎
    try:
        transcriber = initialize_transcriber(args)
    except TranscriberConfigError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # 如果是视频文件，提取音频
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".wav":
        wav_path = filepath
        print(f"  ✅ 直接使用 WAV 文件")
    else:
        wav_path = filepath + "_temp.wav"
        print(f"  🔉 正在从视频提取音频（16kHz 单声道 WAV）...")
        if not extract_audio(filepath, wav_path):
            print(f"  ❌ 音频提取失败，请检查 FFmpeg 是否安装")
            sys.exit(1)
        input_size = os.path.getsize(wav_path)
        print(f"     提取完成 ({format_size(input_size)})")

    # 检查配置
    ok, msg = transcriber.check_config()
    if not ok:
        print(f"  ❌ {msg}")
        if wav_path != filepath:
            os.remove(wav_path)
        sys.exit(1)

    # 执行转写
    print(f"\n  🎤 正在转写音频（使用 {transcriber.name}）...")
    print(f"     这可能需要几分钟，请耐心等待...")
    try:
        result = transcriber.transcribe(wav_path)
    except TranscriberError as e:
        print(f"  ❌ 转写失败: {e}")
        if wav_path != filepath:
            os.remove(wav_path)
        sys.exit(1)

    # 保存结果
    os.makedirs(os.path.dirname(output_txt) or ".", exist_ok=True)
    with open(output_txt, "w", encoding="utf-8") as f:
        f.write(result.text)

    # 显示结果预览
    preview = result.text[:200] + ("..." if len(result.text) > 200 else "")
    print(f"\n  ✅ 转写完成！结果已保存: {output_txt}")
    if preview:
        print(f"     ┌─ 预览 ─────────────────────")
        for line in preview.split("\n"):
            print(f"     │ {line}")
        print(f"     └────────────────────────────")
    print(f"     共 {len(result.text)} 字")

    # 清理临时 WAV
    if wav_path != filepath:
        os.remove(wav_path)


def handle_post_download_transcribe(args, output_dir: str):
    """
    下载完成后对输出的 mp4 文件执行转写。
    遍历输出目录中的 mp4，逐一提取音频并转写。
    """
    if not os.path.isdir(output_dir):
        print(f"  ⚠ 输出目录不存在: {output_dir}")
        return

    # 查找该目录下的 mp4 文件
    mp4_files = []
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".mp4"):
                # 跳过已有关联 txt 的文件
                txt_path = os.path.join(root, os.path.splitext(f)[0] + ".txt")
                if os.path.isfile(txt_path):
                    continue
                mp4_files.append(os.path.join(root, f))

    if not mp4_files:
        print(f"  ⚠ 没有找到需要转写的 mp4 文件")
        return

    # 初始化转写引擎
    try:
        transcriber = initialize_transcriber(args)
    except TranscriberConfigError as e:
        print(f"  ❌ {e}")
        return

    ok, msg = transcriber.check_config()
    if not ok:
        print(f"  ❌ {msg}")
        return

    success = 0
    for mp4_path in mp4_files:
        rel_name = os.path.relpath(mp4_path, output_dir)
        print(f"\n  🎤 正在转写: {rel_name}")

        # 提取音频到临时 WAV
        wav_path = mp4_path + "_temp.wav"
        if not extract_audio(mp4_path, wav_path):
            print(f"    ⚠ 音频提取失败，跳过转写")
            continue

        try:
            result = transcriber.transcribe(wav_path)
            txt_path = os.path.splitext(mp4_path)[0] + ".txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(result.text)
            print(f"    ✅ 转写完成: {os.path.basename(txt_path)} ({len(result.text)}字)")
            success += 1
        except TranscriberError as e:
            print(f"    ❌ 转写失败: {e}")
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

    print(f"\n  📊 转写汇总: ✅ {success} 成功, ⏭ {len(mp4_files) - success} 跳过/失败")


def main():
    parser = argparse.ArgumentParser(
        description="B站视频下载工具 bilibili-dl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 下载单个视频（默认1080P）
  bilibili-dl BV1xx411c7mD

  # 指定画质和输出目录
  bilibili-dl BV1xx411c7mD -q 112 -o ./videos

  # 下载指定分P
  bilibili-dl BV1xx411c7mD -p 1,3,5

  # 下载全部分P
  bilibili-dl BV1xx411c7mD -p all

  # 批量下载
  bilibili-dl -b list.txt -q 80 -o ./output

  # 使用Cookie下载高清视频
  bilibili-dl BV1xx411c7mD -c "SESSDATA=xxx"
        """,
    )

    parser.add_argument(
        "bvid", nargs="?",
        help="BV号或B站视频链接",
    )
    parser.add_argument(
        "-o", "--output", default="./download",
        help="输出目录（默认: ./download）",
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=80,
        help="画质 (6=240P, 16=360P, 32=480P, 64=720P, 80=1080P, "
             "112=1080P+, 120=1080P60, 125=4K, 默认: 80)",
    )
    parser.add_argument(
        "-p", "--pages", default="1",
        help="分P选择 (all=全部, 1,3,5=指定, 1-5=范围, 默认: 1)",
    )
    parser.add_argument(
        "-b", "--batch",
        help="批量下载文件（每行一个BV号或链接）",
    )
    parser.add_argument(
        "-c", "--cookie",
        help="Cookie 字符串（如: SESSDATA=xxx）",
    )
    parser.add_argument(
        "--no-merge", action="store_true",
        help="只下载不合并音视频",
    )
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"bilibili-dl v{__version__}",
    )

    # ── 转写功能参数 ──
    parser.add_argument(
        "--transcribe", action="store_true",
        help="下载完成后自动转写音频为文字",
    )
    parser.add_argument(
        "-t", "--transcribe-only",
        metavar="FILE",
        help="转写模式：直接转写已有的音频/视频文件，不触发下载（简写 -t）",
    )
    parser.add_argument(
        "--transcribe-engine", default="dashscope",
        choices=["dashscope"],
        help="转写引擎（默认: dashscope，预留扩展接口）",
    )
    parser.add_argument(
        "--dashscope-api-key",
        help="阿里云 DashScope API Key（也可通过 DASHSCOPE_API_KEY 环境变量设置）",
    )

    args = parser.parse_args()

    # ── 转写模式：仅转写，不下载 ──
    if args.transcribe_only:
        handle_transcribe_only(args)
        sys.exit(0)

    # ── 参数校验 ──
    if not args.bvid and not args.batch:
        parser.print_help()
        print("\n❌ 请提供 BV 号/链接（-b 批量文件）")
        sys.exit(1)

    if args.quality not in QUALITY_RANGE:
        print(f"⚠ 画质 {args.quality} 可能无效，支持的画质: 6,16,32,64,80,112,116,120,125")
        # 只警告，不退出

    # ── 校验 FFmpeg ──
    if not args.no_merge and not check_ffmpeg():
        print(
            "⚠ 未检测到 FFmpeg！\n"
            "  下载后将保留 m4s 文件，需手动合并。\n"
            "  推荐安装 FFmpeg: https://ffmpeg.org/download.html\n"
            "  安装后确保 ffmpeg.exe 在 PATH 中。\n"
        )

    # ── 批量模式 ──
    if args.batch:
        if not os.path.isfile(args.batch):
            print(f"❌ 批量文件不存在: {args.batch}")
            sys.exit(1)

        bvs = parse_batch_file(args.batch)
        if not bvs:
            print("❌ 批量文件中没有有效的BV号")
            sys.exit(1)

        print(f"📋 批量下载 {len(bvs)} 个视频")
        success = 0
        fail = 0
        for i, bv in enumerate(bvs, 1):
            print(f"\n{'='*50}")
            print(f"[{i}/{len(bvs)}] 处理: {bv}")
            print(f"{'='*50}")
            if download_single_video(
                bvid=bv,
                output_dir=args.output,
                qn=args.quality,
                pages=parse_pages(args.pages, 999),
                cookie=args.cookie or "",
                no_merge=args.no_merge,
            ):
                success += 1
            else:
                fail += 1

        # 批量下载完成后可选转写
        if args.transcribe:
            print(f"\n  🎯 --transcribe 已启用，开始转写下载的视频...")
            handle_post_download_transcribe(args, args.output)

        print(f"\n{'='*50}")
        print(f"📊 批量下载完成: ✅ {success} 成功, ❌ {fail} 失败")
        sys.exit(0 if fail == 0 else 1)

    # ── 单视频模式 ──
    bvid = extract_bvid(args.bvid)
    if not bvid:
        print(f"❌ 无法识别 BV 号: {args.bvid}")
        print("  支持的格式: BV1xx411c7mD 或 https://www.bilibili.com/video/BV1xx411c7mD")
        sys.exit(1)

    # 获取视频信息来确定分P总数
    try:
        info = get_video_info(bvid, cookie=args.cookie or "")
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    total_pages = len(info.pages)

    ok = download_single_video(
        bvid=bvid,
        output_dir=args.output,
        qn=args.quality,
        pages=parse_pages(args.pages, total_pages),
        cookie=args.cookie or "",
        no_merge=args.no_merge,
    )

    # 下载完成后可选转写
    if ok and args.transcribe:
        print(f"\n  🎯 --transcribe 已启用，开始转写下载的视频...")
        handle_post_download_transcribe(args, args.output)

    sys.exit(0 if ok else 1)


QUALITY_RANGE = {6, 16, 32, 64, 74, 80, 112, 116, 120, 125, 126, 127}


if __name__ == "__main__":
    main()
