# -*- coding: utf-8 -*-
"""第三十节视频转写（faster-whisper small, CPU）"""
import os, sys, time
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["HF_HUB_DISABLE_XET"] = "1"  # 禁用 xet 存储协议（hf-mirror 不支持，会 401）
from faster_whisper import WhisperModel

t0 = time.time()
print("加载模型 small...", flush=True)
model = WhisperModel("small", device="cpu", compute_type="int8")
print(f"模型加载完成 {time.time()-t0:.0f}s", flush=True)

segments, info = model.transcribe(
    r"C:\Users\32032\Desktop\deepseek\量化交易系统\temp\videos\lesson30.wav",
    language="zh",
    vad_filter=True,
)
out = r"C:\Users\32032\Desktop\deepseek\量化交易系统\temp\videos\lesson30_transcript.txt"
n = 0
with open(out, "w", encoding="utf-8") as f:
    for seg in segments:
        line = f"[{seg.start:8.2f} - {seg.end:8.2f}] {seg.text.strip()}"
        print(line, flush=True)
        f.write(line + "\n")
        n += 1
print(f"\n完成: {n} 段, 耗时 {time.time()-t0:.0f}s, 输出: {out}", flush=True)
