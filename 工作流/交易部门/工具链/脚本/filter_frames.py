#!/usr/bin/env python3
"""
filter_frames.py — 对视频帧进行去重和筛选

用法:
  # 对指定目录下的帧去重（保留相似帧中第一张）
  python filter_frames.py --dir 产出/临时/extract_knowledge/XXX/frames

  # 去重 + 尝试识别是否包含K线图（需要API）
  python filter_frames.py --dir frames/ --classify

  # 只保留最独特的帧（阈值0.95=只移除几乎完全相同的帧）
  python filter_frames.py --dir frames/ --threshold 0.95
"""
import argparse
import os
import shutil
import sys
from pathlib import Path


def dhash(image, hash_size=8):
    """计算图片的差异哈希 (difference hash)"""
    from PIL import Image
    img = Image.open(image).convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    diff = []
    for row in range(hash_size):
        for col in range(hash_size):
            diff.append(img.getpixel((col, row)) > img.getpixel((col + 1, row)))
    return sum(2 ** i for i, v in enumerate(diff) if v)

def hamming_distance(h1, h2):
    """两个哈希值的汉明距离"""
    return (h1 ^ h2).bit_count()

def main():
    parser = argparse.ArgumentParser(description="视频帧去重筛选")
    parser.add_argument("--dir", required=True, help="帧图片目录")
    parser.add_argument("--threshold", type=float, default=0.92,
                       help="相似度阈值 0-1, 越高保留越多 (默认0.92)")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    parser.add_argument("--yes", "-y", action="store_true", help="自动确认，跳过交互")
    args = parser.parse_args()

    frames_dir = Path(args.dir)
    if not frames_dir.exists():
        print(f"目录不存在: {frames_dir}")
        sys.exit(1)

    pngs = sorted(frames_dir.glob("*.png"))
    print(f"总帧数: {len(pngs)}")

    if not pngs:
        return

    # 计算所有帧的哈希
    import pickle
    cache_file = frames_dir / ".hash_cache"
    if cache_file.exists():
        with open(cache_file, "rb") as f:
            hashes = pickle.load(f)
        print(f"缓存加载: {len(hashes)} 个哈希")
    else:
        print("计算差异哈希 (dHash)...")
        hashes = []
        for i, p in enumerate(pngs):
            try:
                h = dhash(p)
                hashes.append((p.name, h))
            except Exception as e:
                print(f"  [{i+1}/{len(pngs)}] {p.name} 失败: {e}")
                hashes.append((p.name, None))
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(pngs)}]")

        with open(cache_file, "wb") as f:
            pickle.dump(hashes, f)
        print("哈希计算完成, 已缓存")

    # 按相似度聚类去重
    hash_size = 64  # dhash outputs hash_size*hash_size bits
    max_distance = int(hash_size * (1 - args.threshold))

    # 贪心去重：保留第一张，移除相似的后续帧
    keep = []       # 保留的帧
    remove = []     # 移除的帧
    kept_hashes = []

    for name, h in hashes:
        if h is None:
            remove.append(name)
            continue
        # 检查是否与已保留的帧相似
        is_duplicate = False
        for kh in kept_hashes:
            if hamming_distance(h, kh) <= max_distance:
                is_duplicate = True
                break
        if is_duplicate:
            remove.append(name)
        else:
            keep.append(name)
            kept_hashes.append(h)

    print("\n结果:")
    print(f"  保留: {len(keep)} 帧")
    print(f"  移除: {len(remove)} 帧")

    if args.dry_run:
        print("\n(dry-run 模式, 未实际删除)")
        return

    # 确认
    print(f"\n将删除 {len(remove)} 张重复帧, 保留 {len(keep)} 张")
    if not args.yes:
        try:
            confirm = input("确认删除? (y/n): ").strip().lower()
            if confirm != 'y':
                print("已取消")
                return
        except Exception:
            print("非交互模式, 使用 --yes 自动确认")
            return

    # 执行删除
    deleted = 0
    for name in remove:
        path = frames_dir / name
        try:
            os.remove(path)
            deleted += 1
        except Exception:
            pass

    # 重命名保留的帧为连续序号
    keep.sort()
    for i, name in enumerate(keep, 1):
        src = frames_dir / name
        dst = frames_dir / f"frame_{i:05d}.png"
        if src != dst:
            shutil.move(str(src), str(dst))

    print(f"已删除 {deleted} 帧, 剩余 {len(keep)} 帧")
    print(f"保存到: {frames_dir}")


if __name__ == "__main__":
    main()
