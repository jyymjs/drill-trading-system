# B站字幕抓取（bilibili_subtitle）

抓取 B站视频的 **AI 字幕全文**（输入链接/BV 号 → 输出字幕文本），供精炼、要点提取、文字化存档使用。

## 功能

- 输入 B站视频链接或 BV 号 → 输出该视频 AI 字幕全文
- 支持多P视频选 P（`--p N`）
- 字幕打印到终端或保存到文件（`--out`）
- 使用 wbi 签名 + SESSDATA 登录态，未登录/过期时给出明确指引，不产出空文件

## 用法

```bash
# 打印字幕到终端
python 公共资源部/自有工具/bilibili_subtitle/get_subtitle.py --url "https://www.bilibili.com/video/BVxxxxxxxxxx/"

# 保存到文件（供后续精炼/转写）
python .../get_subtitle.py --url "https://www.bilibili.com/video/BVxxxxxxxxxx/" --out 字幕.txt

# 多P视频取第 2 P
python .../get_subtitle.py --url "BVxxxxxxxxxx" --p 2
```

`--url` 与位置参数等价：`get_subtitle.py BV1GcGX6iEUG` 与 `get_subtitle.py --url https://www.bilibili.com/video/BV1GcGX6iEUG/` 效果相同。

## 依赖

- Python 3.8+
- `requests`（`pip install requests`）

## 配置（必须）

B站 AI 字幕接口要求登录态，需配置 SESSDATA：

1. `copy config.local.example.json config.local.json`（config.local.json 已 gitignore，不入库）
2. 浏览器登录 bilibili.com → F12 → Application → Cookies → `https://www.bilibili.com` → 复制 `SESSDATA` 的值（形如 `xxx%2Cxxx%2Cxxx%2Axxx`）填入 `sessdata`

**过期处理**：SESSDATA 过期后接口返回错误或字幕列表为空，脚本会明确提示更新，不会静默输出空文件。

## 说明

- 只抓 AI 字幕（lan=ai-zh）；视频无 AI 字幕（如部分非教学视频）时会报错说明
- 字幕内容版权归 UP 主，仅限个人学习使用
