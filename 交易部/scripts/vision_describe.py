#!/usr/bin/env python3
"""
vision_describe.py — 我的"眼睛"：调用智谱视觉模型描述图片（坐标锚点模板）

用法:
  python vision_describe.py <图片路径>              # 标准档（默认模型）
  python vision_describe.py <图片路径> --annotate   # 方位标注档（只报元素+坐标，不解读）
  python vision_describe.py <图片路径> --model glm-4v-flash   # 指定模型

配置: scripts/config.local.json（{"zhipu_api_key": "...", "zhipu_model": "glm-4.6v-flash"}）
      或环境变量 ZHIPU_API_KEY
"""
import sys, os, json, base64
from openai import OpenAI

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.json")

PROMPT = """你是专业K线图/行情图分析师。请用坐标锚点格式描述这张图（若图不是行情图则如实说明）。
必输出：
1. 图表类型与周期（日K/分时/周K等）
2. 标的（图上可读的代码/名称）
3. 形态结构（含坐标区间，如"平台区间 x1~x2"）
4. 标注元素（线/框/文字，起止坐标 A→B）
5. 关键价位与当前价关系
6. 一句话要点"""

ANNOTATE_PROMPT = """你是图像方位标注器。你的任务：客观描述图上"看到什么、在哪里"，**不解读、不判断、不推测**——只报客观可见的元素及其位置。
必输出（按此结构）：
1. 图表区域：图表类型（K线/分时/其他，按图上可见特征） + 图区坐标区间
2. 价格/数值标签：文本内容 + 位置坐标（如"69430.81 在 (728,525)"）
3. 线条/图形元素：类型（线/框/箭头/十字线）+ 起止坐标（如"线 (300,200)→(600,450)"）
4. 文字/标题：内容摘要 + 位置
5. 数据区域：如成交量柱位置区间
6. 图上不可读或不存在的内容：不编造，需要时标注"不可读"
注意：你只报告视觉事实，交易含义（平台/支撑/形态）由分析者判断，你不要输出任何交易解读。"""


def load_config():
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    if len(sys.argv) < 2:
        print("用法: python vision_describe.py <图片路径> [--model xxx]")
        sys.exit(1)
    path = sys.argv[1]
    cfg = load_config()
    api_key = cfg.get("zhipu_api_key") or os.environ.get("ZHIPU_API_KEY", "")
    if not api_key:
        print("未配置智谱 API key：scripts/config.local.json 或环境变量 ZHIPU_API_KEY")
        sys.exit(1)
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    else:
        model = cfg.get("zhipu_model", "glm-4.6v-flash")

    prompt = ANNOTATE_PROMPT if "--annotate" in sys.argv else PROMPT

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    client = OpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4/")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        temperature=0.1,
    )
    print(resp.choices[0].message.content)


if __name__ == "__main__":
    main()
