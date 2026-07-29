#!/usr/bin/env python3
"""对帧目录做K线图/非图表分类，只保留K线图帧"""
import os, base64, time, sys
from openai import OpenAI

FRAMES_DIR = sys.argv[1] if len(sys.argv) > 1 else ""
if not FRAMES_DIR or not os.path.isdir(FRAMES_DIR):
    print(f"用法: python classify_frames.py <帧目录>")
    sys.exit(1)

API_KEY = os.environ.get("ZHIPU_API_KEY", "")
if not API_KEY:
    print("需要设置 ZHIPU_API_KEY 环境变量")
    sys.exit(1)

client = OpenAI(api_key=API_KEY, base_url="https://open.bigmodel.cn/api/paas/v4/")
pngs = sorted([f for f in os.listdir(FRAMES_DIR) if f.endswith(".png")])
print(f"待分类: {len(pngs)} 帧")

PROMPT = "判断画面类型，chart=包含K线图/走势图/技术图表, other=其他(标题/人脸/白板/纯文字)"
chart = []

for i, fname in enumerate(pngs, 1):
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
            max_tokens=10, temperature=0.1, timeout=30,
        )
        ans = resp.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"[{i}/{len(pngs)}] {fname} err: {str(e)[:40]}")
        continue

    if ans.startswith("chart"):
        chart.append(fname)
        print(f"[{i}/{len(pngs)}] {fname} -> chart")
    else:
        print(f"[{i}/{len(pngs)}] {fname} -> other (删除)")
        os.remove(fpath)
    time.sleep(0.3)

print(f"\n完成! 保留 {len(chart)} 帧(K线图), 删除 {len(pngs)-len(chart)} 帧(非图表)")
