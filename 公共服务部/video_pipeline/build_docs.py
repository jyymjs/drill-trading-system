#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_docs.py — 图文绑定 + 《图文知识文档》生成

用法:
  python build_docs.py <转写txt> <帧目录> <timeline.json> <标注json> <输出md> <课程标题>

输入格式:
  转写txt: 每行 [起秒 - 止秒] 文字
  timeline.json: [{index, time, file}]
  标注json: {文件名: {status, text}}

逻辑:
  逐段转写 → 找该时间段内（或时间最近）的帧图 → 图文对照输出
  产出: markdown 图文知识文档（供培训部学习/交易部调用）
"""
import sys, os, re, json


def parse_transcript(path):
    """解析 [s - e] text 行"""
    segs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\[\s*([\d.]+)\s*-\s*([\d.]+)\]\s*(.*)", line.strip())
            if m:
                segs.append({"start": float(m.group(1)), "end": float(m.group(2)),
                             "text": m.group(3)})
    return segs


def fmt_time(t):
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def match_frame(seg, timeline):
    """找覆盖该时间段内或时间最近的帧"""
    best, best_dist = None, None
    for fr in timeline:
        t = fr["time"]
        if seg["start"] <= t <= seg["end"]:
            return fr  # 时间窗内优先
        d = min(abs(t - seg["start"]), abs(t - seg["end"]))
        if best_dist is None or d < best_dist:
            best, best_dist = fr, d
    return best if best_dist is not None and best_dist <= 90 else None  # 超过90秒不配图


def main():
    if len(sys.argv) < 7:
        print(__doc__)
        sys.exit(1)
    trans_path, frames_dir, tl_path, ann_path, out_path, title = sys.argv[1:7]

    segs = parse_transcript(trans_path)
    with open(tl_path, encoding="utf-8") as f:
        timeline = json.load(f)
    with open(ann_path, encoding="utf-8") as f:
        annotation = json.load(f)

    # 合并转写为段落：相邻段间隔 < 15 秒且段落时长 ≤ 90 秒才合并
    merged = []
    for s in segs:
        if (merged and s["start"] - merged[-1]["end"] < 15
                and merged[-1]["end"] - merged[-1]["start"] < 90):
            merged[-1]["end"] = s["end"]
            merged[-1]["text"] += s["text"]
        else:
            merged.append(dict(s))

    doc = [f"# {title}（图文版）", "",
           f"> 转写 {len(segs)} 段 → 合并 {len(merged)} 段 | 帧图 {len(timeline)} 张 | 标注 {len(annotation)} 张", ""]

    used_frames = set()
    for i, seg in enumerate(merged, 1):
        doc.append(f"## [{fmt_time(seg['start'])} ~ {fmt_time(seg['end'])}]")
        doc.append("")
        doc.append(f"🎙 **老师原话**：{seg['text']}")
        doc.append("")
        fr = match_frame(seg, timeline)
        if fr and fr["file"] in annotation and annotation[fr["file"]].get("status") == "ok":
            used_frames.add(fr["file"])
            doc.append(f"🖼 **画面** @{fmt_time(fr['time'])}（{fr['file']}）")
            doc.append("")
            doc.append("```")
            doc.append(annotation[fr["file"]]["text"])
            doc.append("```")
            doc.append("")
        elif fr:
            doc.append(f"（画面 {fr['file']} 标注未成功，略）")
            doc.append("")

    skipped = len(timeline) - len(used_frames)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(doc))
    print(f"完成: {out_path} | 段落 {len(merged)} | 配图 {len(used_frames)} | 未用帧 {skipped}")


if __name__ == "__main__":
    main()
