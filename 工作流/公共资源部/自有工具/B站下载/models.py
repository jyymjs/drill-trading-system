"""数据模型：视频信息、播放流、分P 等数据结构"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StreamItem:
    """单个音视频流的信息"""
    id: int               # 流质量编号
    url: str              # 下载地址
    base_url: str         # 备用下载地址
    bandwidth: int        # 带宽
    codec_id: int         # 编码ID
    codecs: str           # 编码名称 (avc1.640028 / mp4a.40.2)
    width: int = 0        # 视频宽度（音频为0）
    height: int = 0       # 视频高度（音频为0）
    frame_rate: str = ""  # 帧率（音频为空）


@dataclass
class VideoPage:
    """单个分P的信息"""
    cid: int              # 分P的cid
    title: str            # 分P标题
    page: int             # 分P序号
    duration: int         # 时长（秒）
    part: str = ""        # 分P名称


@dataclass
class VideoInfo:
    """视频基本信息"""
    bvid: str             # BV号
    title: str            # 视频标题
    owner_name: str       # UP主名称
    owner_uid: int        # UP主UID
    duration: int         # 总时长（秒）
    pages: list[VideoPage] = field(default_factory=list)  # 分P列表
    desc: str = ""        # 视频简介
    pic: str = ""         # 封面图URL


@dataclass
class PlayInfo:
    """视频播放流信息"""
    bvid: str
    cid: int
    duration: int         # 时长（毫秒）
    videos: list[StreamItem] = field(default_factory=list)   # 视频流列表（从高到低）
    audios: list[StreamItem] = field(default_factory=list)   # 音频流列表
    quality: int = 0      # 当前选择的画质
    accept_quality: list[int] = field(default_factory=list)  # 可选的画质列表
    accept_description: list[str] = field(default_factory=list)  # 画质名称列表
    support_formats: list[dict] = field(default_factory=list)    # 格式信息
    video_codecid: int = 7  # 视频编码ID
    no_merge: bool = False  # 是否不需要合并
    dash: bool = True       # 是否为DASH格式

    @property
    def best_video(self) -> Optional[StreamItem]:
        """获取最高画质的视频流"""
        return self.videos[0] if self.videos else None

    @property
    def best_audio(self) -> Optional[StreamItem]:
        """获取最高码率的音频流"""
        return self.audios[0] if self.audios else None

    @property
    def quality_name(self) -> str:
        """获取当前画质名称"""
        if self.accept_description and self.quality in self.accept_quality:
            idx = self.accept_quality.index(self.quality)
            if idx < len(self.accept_description):
                return self.accept_description[idx]
        return f"qn={self.quality}"
