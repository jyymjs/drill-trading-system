# GPU 转写工具（whisper.cpp Vulkan 版）

> 所属：公共资源部 · 功能：**视频/音频 → 文字转写（AMD GPU 加速）**
> 用途：交易部 140 视频全流程（T-013）的核心转写引擎

## 功能
- 批量转写视频（mp4/mkv/avi/mov/flv/wmv/ts）为带时间轴的纯文本
- **AMD GPU 加速**（Vulkan 后端，RX 6750 GRE 实测可用），CPU 仅做预处理
- 断点续传：已完成文件自动跳过，可中断重跑
- 失败自动重试 3 次 + 全程日志（transcribe.log）

## 目录
| 路径 | 内容 |
|------|------|
| `bin/whisper-cli.exe` | 转写主程序（Vulkan 编译版，未启用 AVX512 保兼容） |
| `models/ggml-large-v3-turbo-q8_0.bin` | 模型（874MB，中文效果好，12GB 显存富余） |
| `batch_transcribe.py` | 批量转写脚本（断点续传+日志+重试） |
| `whisper.cpp-src/` | 源码（git clone，编译产物在 build/） |
| — | 转写输出直达 `交易部/知识库/{分类}/raw/`（知识库规范：raw=素材层） |
| `tmp/` | 临时 wav（自动清理） |

## 用法
```bash
# 批量转写全部视频
python batch_transcribe.py

# 单条转写
bin/whisper-cli.exe -m models/ggml-large-v3-turbo-q8_0.bin -f 音频.wav -l zh -otxt -of 输出前缀
```

## 编译记录（2026-08-03）
- 环境：msys2/mingw gcc 16.1.0 + CMake 4.4.2 + Ninja 1.13.2 + Vulkan SDK 1.4.357
- 命令：`cmake -B build -G Ninja -DGGML_VULKAN=ON -DGGML_AVX512=OFF -DCMAKE_BUILD_TYPE=Release`
- 需环境变量：`VULKAN_SDK=C:/VulkanSDK/1.4.357.0`
- 下载源排障：GitHub HTTPS 直连不通，走 git 通道克隆源码；非 GitHub 源（lunarg/cmake 镜像）直连通

## 备注
- 语言默认 `zh`，转写格式 `[起-止] 文本`（与交易部知识库 raw 格式一致）
- 中文长视频（30-60 分钟）GPU 转写预计 3-10 分钟/个
