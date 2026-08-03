"""语音转写引擎 — 抽象基类 + DashScope 实现 + 引擎注册表"""

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Optional

from requests.exceptions import RequestException


# ── 异常体系 ──────────────────────────────────────────

class TranscriberError(Exception):
    """转写引擎通用异常基类"""
    pass


class TranscriberConfigError(TranscriberError):
    """配置错误（如 API Key 未设置）"""
    pass


class TranscriberAPIError(TranscriberError):
    """API 调用错误（网络超时、HTTP 4xx/5xx 等）"""
    pass


# ── 数据结构 ──────────────────────────────────────────

@dataclass
class TranscriptionResult:
    """转写结果"""
    text: str = ""                           # 完整文本
    language: str = "zh"                     # 语种
    duration_seconds: float = 0.0            # 音频时长
    segments: list[dict] = field(default_factory=list)  # 分段详情
    engine: str = ""                         # 引擎名称


# ── 抽象基类 ──────────────────────────────────────────

class BaseTranscriber(ABC):
    """转写引擎抽象基类 — 所有引擎必须实现此接口"""

    @abstractmethod
    def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        """
        转写音频文件。

        参数:
            audio_path: 本地音频文件路径（推荐 16kHz 单声道 WAV）
            **kwargs:   引擎特定参数

        返回:
            TranscriptionResult 对象

        异常:
            TranscriberConfigError: API Key / 配置无效
            TranscriberAPIError:    远程 API 调用失败
            TranscriberError:       其他错误
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎显示名称"""
        ...

    @abstractmethod
    def check_config(self) -> tuple[bool, str]:
        """
        检查配置是否就绪。

        返回:
            (True, "") 或 (False, "错误描述")
        """
        ...


# ── DashScope 实现 ────────────────────────────────────

class DashScopeTranscriber(BaseTranscriber):
    """
    阿里云 DashScope Paraformer 转写引擎。

    使用 dashscope.audio.asr.Recognition.call() 同步转写本地文件。
    推荐模型: paraformer-realtime-v2（16kHz 采样率，文件大小无限制）。
    """

    VALID_MODELS = {
        "paraformer-realtime-v2",    # 推荐，最高精度
        "paraformer-realtime-v1",    # 上一代
        "paraformer-8k-v1",          # 8kHz 电话语音
    }
    DEFAULT_MODEL = "paraformer-realtime-v2"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        sample_rate: int = 16000,
    ):
        if model not in self.VALID_MODELS:
            raise TranscriberConfigError(
                f"不支持的模型: {model}，可选: {', '.join(sorted(self.VALID_MODELS))}"
            )
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate

    @property
    def name(self) -> str:
        return f"DashScope({self._model})"

    def _resolve_api_key(self) -> str:
        """按优先级: 构造函数参数 > 环境变量"""
        key = self._api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            raise TranscriberConfigError(
                "未配置 DashScope API Key。\n"
                "请通过 --dashscope-api-key 参数传入，\n"
                "或设置环境变量 DASHSCOPE_API_KEY=your_key"
            )
        return key

    def check_config(self) -> tuple[bool, str]:
        try:
            self._resolve_api_key()
            return True, ""
        except TranscriberConfigError as e:
            return False, str(e)

    def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not os.path.isfile(audio_path):
            raise TranscriberError(f"音频文件不存在: {audio_path}")

        file_size = os.path.getsize(audio_path)
        if file_size == 0:
            raise TranscriberError(f"音频文件为空: {audio_path}")

        api_key = self._resolve_api_key()

        # 动态导入 dashscope（让未安装时错误提示更清晰）
        try:
            import dashscope
            from dashscope.audio.asr import Recognition
        except ImportError:
            raise TranscriberConfigError(
                "未安装 dashscope SDK。\n"
                "请运行: pip install dashscope>=1.0.0"
            )

        dashscope.api_key = api_key

        recognition = Recognition(
            model=self._model,
            format="wav",
            sample_rate=self._sample_rate,
            callback=None,  # None = 同步模式
        )

        # API 调用（带简单重试）
        max_retries = kwargs.get("max_retries", 3)
        last_error = ""

        for attempt in range(1, max_retries + 1):
            try:
                result = recognition.call(audio_path)
            except (RequestException, ConnectionError) as e:
                last_error = f"网络错误: {e}"
                if attempt < max_retries:
                    delay = 2 ** attempt
                    print(f"    [重试 {attempt}/{max_retries}] {delay}秒后重试...")
                    time.sleep(delay)
                continue
            except Exception as e:
                raise TranscriberAPIError(f"DashScope API 调用失败: {e}")

            if result.status_code != HTTPStatus.OK:
                err_msg = getattr(result, "message", "未知错误")
                last_error = f"DashScope 返回错误 (code={result.status_code}): {err_msg}"
                if attempt < max_retries:
                    delay = 2 ** attempt
                    print(f"    [重试 {attempt}/{max_retries}] {delay}秒后重试...")
                    time.sleep(delay)
                continue

            # 成功
            break
        else:
            raise TranscriberAPIError(
                f"DashScope API 调用失败 (已重试 {max_retries}次): {last_error}"
            )

        # 解析结果
        sentences = result.get_sentence()
        full_text = ""
        if isinstance(sentences, list):
            full_text = "".join(s.get("text", "") for s in sentences)
        elif isinstance(sentences, dict):
            full_text = sentences.get("text", "")
        elif sentences:
            full_text = str(sentences)

        return TranscriptionResult(
            text=full_text.strip(),
            segments=sentences if isinstance(sentences, list) else [],
            engine=self.name,
        )


# ── 引擎注册表 ──────────────────────────────────────────

_TRANSCRIBER_REGISTRY: dict[str, type[BaseTranscriber]] = {}


def register_transcriber(name: str, cls: type[BaseTranscriber]):
    """注册转写引擎"""
    _TRANSCRIBER_REGISTRY[name] = cls


def get_transcriber(name: str, **kwargs) -> BaseTranscriber:
    """根据名称获取转写引擎实例"""
    if name not in _TRANSCRIBER_REGISTRY:
        raise TranscriberConfigError(
            f"不支持的转写引擎: {name}，可用: {', '.join(_TRANSCRIBER_REGISTRY)}"
        )
    return _TRANSCRIBER_REGISTRY[name](**kwargs)


def list_transcribers() -> list[str]:
    """列出所有已注册的转写引擎"""
    return list(_TRANSCRIBER_REGISTRY.keys())


# 注册内置引擎
register_transcriber("dashscope", DashScopeTranscriber)
