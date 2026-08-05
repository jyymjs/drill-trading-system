#!/usr/bin/env python3
"""
安全版帧分类：先分类所有帧并记录结果，最后再删除非图表帧。
防止中途中断导致数据丢失。
"""
import base64
import json
import os
import sys
import time

from openai import OpenAI

FRAMES_DIR = sys.argv[1]
API_KEY = os.environ.get("ZHIPU_API_KEY", "")
if not API_KEY: print("Need ZHIPU_API_KEY"); sys.exit(1)

client = OpenAI(api_key=API_KEY, base_url="https://open.bigmodel.cn/api/paas/v4/")
pngs = sorted([f for f in os.listdir(FRAMES_DIR) if f.endswith(".png")])
RESULT_FILE = os.path.join(FRAMES_DIR, "_classify_result.json")

# 如果已有部分结果，从中断处恢复
results = {}
if os.path.exists(RESULT_FILE):
    with open(RESULT_FILE) as f:
        existing = json.load(f)
    results = existing.get("results", {})
    print(f"恢复已有结果: {len(results)} 帧已分类")

PROMPT = "图片类型：chart=含K线图/走势图/技术指标, other=其他"

to_classify = [f for f in pngs if f not in results]
print(f"待分类: {len(to_classify)} 帧 (总共 {len(pngs)} 帧)")

for i, fname in enumerate(to_classify, 1):
    fpath = os.path.join(FRAMES_DIR, fname)
    try:
        with open(fpath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        resp = client.chat.completions.create(
            model="glm-4v-flash",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            max_tokens=5, temperature=0.1, timeout=20,
        )
        ans = resp.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"[{i}/{len(to_classify)}] {fname} err: {str(e)[:30]}")
        results[fname] = "error"
        time.sleep(2)
        continue

    label = "chart" if "chart" in ans else "other"
    results[fname] = label
    print(f"[{i}/{len(to_classify)}] {fname} -> {label}")

    # 每分类10帧保存一次结果
    if i % 10 == 0:
        with open(RESULT_FILE, "w") as f:
            json.dump({"total": len(pngs), "results": results}, f, ensure_ascii=False)
    time.sleep(0.3)

# 保存最终结果
with open(RESULT_FILE, "w") as f:
    json.dump({"total": len(pngs), "results": results}, f, ensure_ascii=False)

# 统计
chart_count = sum(1 for v in results.values() if v == "chart")
other_count = sum(1 for v in results.values() if v == "other")
error_count = sum(1 for v in results.values() if v == "error")
print("\n=== 统计 ===")
print(f"  K线图(chart): {chart_count}")
print(f"  非图表(other): {other_count}")
print(f"  错误: {error_count}")

# 最后统一删除非图表帧
if other_count > 0 and not os.environ.get("SKIP_DELETE"):
    print(f"\n删除 {other_count} 张非图表帧...")
    for fname, label in results.items():
        if label == "other":
            fpath = os.path.join(FRAMES_DIR, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
    print("已删除")

remaining = len([f for f in os.listdir(FRAMES_DIR) if f.endswith(".png")])
print(f"最终保留: {remaining} 帧 (仅K线图)")
with open(os.path.join(FRAMES_DIR, "_filter_done.txt"), "w") as f:
    f.write(f"chart={chart_count} other={other_count} error={error_count} final={remaining}")
