#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
annotate_frames.py — 批量方位标注帧图（智谱 glm-4.6v-flash 免费 API，30 并发）

用法:
  python annotate_frames.py <帧目录> [输出json路径]
  例: python annotate_frames.py frames/ frames_annotation.json

原理:
  对目录下每张 PNG 调用智谱视觉模型做"方位标注"（只报元素+坐标，不解读），
  结果保存为 JSON（{文件名: {status, 标注文本, 耗时}}），供分析者解读。

配置:
  读取 交易部门/工具链/脚本/config.local.json（zhipu_api_key / zhipu_model）
"""
import sys, os, json, base64, time, concurrent.futures
from openai import OpenAI

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "交易部", "scripts", "config.local.json")

ANNOTATE_PROMPT = """你是图像方位标注器。你的任务：客观描述图上"看到什么、在哪里"，**不解读、不判断、不推测**——只报客观可见的元素及其位置。
必输出（按此结构）：
1. 图表区域：图表类型（K线/分时/其他，按图上可见特征） + 图区坐标区间
2. 价格/数值标签：文本内容 + 位置坐标（如"69430.81 在 (728,525)"）
3. 线条/图形元素：类型（线/框/箭头/十字线）+ 起止坐标（如"线 (300,200)→(600,450)"）
4. 文字/标题：内容摘要 + 位置
5. 数据区域：如成交量柱位置区间
6. 图上不可读或不存在的内容：不编造，需要时标注"不可读"
注意：你只报告视觉事实，交易含义（平台/支撑/形态）由分析者判断，你不要输出任何交易解读。"""


def load_cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


def annotate_one(client, model, path, max_retries=5):
    t0 = time.time()
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": ANNOTATE_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}],
                temperature=0.1,
            )
            return {"status": "ok", "text": resp.choices[0].message.content,
                    "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            last_err = str(e)
            if "429" in last_err or "速率" in last_err:
                time.sleep(5 * (2 ** attempt))  # 指数退避 5/10/20/40/80s
            else:
                break
    return {"status": "error", "text": last_err, "seconds": round(time.time() - t0, 1)}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    frames_dir = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(frames_dir, "annotation.json")
    pngs = sorted(f for f in os.listdir(frames_dir) if f.lower().endswith(".png"))
    if not pngs:
        print("目录下无 PNG 帧图"); sys.exit(1)

    cfg = load_cfg()
    client = OpenAI(api_key=cfg["zhipu_api_key"], base_url="https://open.bigmodel.cn/api/paas/v4/")
    model = cfg.get("zhipu_model", "glm-4.6v-flash")
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]

    # 断点续传：跳过已有成功结果
    results = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            results = json.load(f)
        skip = [p for p, v in results.items() if v.get("status") == "ok"]
        if skip:
            print(f"断点续传：跳过 {len(skip)} 帧已成功", flush=True)

    todo = [p for p in pngs if p not in results or results[p].get("status") != "ok"]
    print(f"待标注 {len(todo)} 帧（模型 {model}，串行+限速）...", flush=True)
    done = len(pngs) - len(todo)
    for p in todo:
        results[p] = annotate_one(client, model, os.path.join(frames_dir, p))
        done += 1
        print(f"  [{done}/{len(pngs)}] {p} {results[p]['seconds']}s", flush=True)
        time.sleep(8)  # 限速：glm-4.6v-flash 仅 1 并发，慢速串行防 429

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"完成: {out_path}", flush=True)


if __name__ == "__main__":
    main()
