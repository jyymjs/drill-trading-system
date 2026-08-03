# B站视频下载工具 bilibili-dl 🎬

轻量级 Python 命令行工具，下载 B 站视频（支持单视频和批量处理）。

## 功能

- ✅ 输入 BV 号或 B 站链接自动下载
- ✅ 画质选择（240P ~ 4K）
- ✅ 多分P视频支持（单P / 指定P / 全部）
- ✅ 批量下载（从文本文件读取 BV 列表）
- ✅ 自动合并音视频（需 FFmpeg）
- ✅ 断点续传（自动重试）
- ✅ Cookie 支持（高清/会员视频）
- ✅ **音频转文字**（语音识别，需阿里云 DashScope API Key）

## 安装

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 FFmpeg（推荐，用于自动合并音视频）

**Windows:**
```bash
winget install FFmpeg
```
或从 https://ffmpeg.org/download.html 下载并添加到 PATH。

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt install ffmpeg   # Ubuntu/Debian
sudo yum install ffmpeg   # CentOS
```

不安装 FFmpeg 也能下载，但需要手动合并音视频。

## 使用

### 下载单视频（默认 1080P）

```bash
python -m bilibili_dl.cli BV1xx411c7mD
```

### 指定画质和输出目录

```bash
python -m bilibili_dl.cli BV1xx411c7mD -q 112 -o ./videos
```

画质参数：
| qn 值 | 画质     |
|-------|----------|
| 6     | 240P     |
| 16    | 360P     |
| 32    | 480P     |
| 64    | 720P     |
| 80    | 1080P    |
| 112   | 1080P+   |
| 120   | 1080P60  |
| 125   | 4K       |

### 下载指定分P

```bash
# 指定单页
python -m bilibili_dl.cli BV1xx411c7mD -p 3

# 指定多页
python -m bilibili_dl.cli BV1xx411c7mD -p 1,3,5

# 指定范围
python -m bilibili_dl.cli BV1xx411c7mD -p 1-5

# 下载全部
python -m bilibili_dl.cli BV1xx411c7mD -p all
```

### 批量下载

准备 `list.txt`（每行一个 BV 号或链接）：
```
BV1xx411c7mD
https://www.bilibili.com/video/BV1yy411c7mE
BV1zz411c7mF
```

```bash
python -m bilibili_dl.cli -b list.txt -q 80 -o ./output
```

### 下载高清/会员视频

需要登录后从浏览器获取 Cookie：
```bash
python -m bilibili_dl.cli BV1xx411c7mD -c "SESSDATA=你的SESSDATA值"
```

### 仅下载不合并

```bash
python -m bilibili_dl.cli BV1xx411c7mD --no-merge
```

### 音频转文字（语音识别）

需要先安装 dashscope SDK 并获取阿里云 API Key：
```bash
pip install dashscope
```

**方式1：下载完成后自动转写**
```bash
python -m bilibili_dl.cli BV1xx411c7mD --transcribe --dashscope-api-key sk-xxx
```

也可将 API Key 设为环境变量，避免每次输入：
```bash
set DASHSCOPE_API_KEY=sk-xxx
python -m bilibili_dl.cli BV1xx411c7mD --transcribe
```

**方式2：单独转写已有视频/音频文件**
```bash
# 简洁模式（-t）
python -m bilibili_dl.cli -t input.mp4 -o output.txt

# 完整模式
python -m bilibili_dl.cli --transcribe-only input.mp4 -o output.txt
```

支持的输入格式：mp4、mp3、wav、m4a、flv 等（会自动提取音频）。

## 输出结构

```
download/
└── 视频标题/
    ├── 视频标题.mp4                 # 单P视频
    ├── 视频标题_P1_part1.mp4        # 多P视频
    ├── 视频标题_P2_part2.mp4
    └── ...
```

## 常见问题

**Q: 提示"未检测到 FFmpeg"**
A: 安装 FFmpeg 或将 ffmpeg.exe 所在目录加入 PATH。不安装也能下载，但不会自动合并。

**Q: 下载速度慢**
A: B站 CDN 限速属于正常现象，可尝试更换网络环境。

**Q: 某些视频下载失败**
A: 可能原因：1) 视频未审核通过  2) 需要登录（加 -c 参数）  3) 地区限制

**Q: WBI 签名失败**
A: B站 API 可能已更新签名算法。请更新本工具至最新版。

**Q: 转写提示"未配置 DashScope API Key"**
A: 需要先获取阿里云 DashScope API Key：登录阿里云 → 模型服务灵积 → API-KEY 管理 → 创建 API Key。然后通过 `--dashscope-api-key` 参数或 `DASHSCOPE_API_KEY` 环境变量传入。

**Q: 转写速度慢 / 准确率低**
A: 阿里云 Paraformer 的转写质量取决于音频质量和视频内容。B站视频通常音质良好，中文识别准确率很高。长视频可能需要几分钟处理时间，属于正常现象。
