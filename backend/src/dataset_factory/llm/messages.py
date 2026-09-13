"""llm 的 provider 中立消息模型——不绑定任何厂商接口的消息表示。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 消息角色（OpenAI 兼容接口的事实）：system 系统指令、user 现场内容、assistant 模型历史回复。
Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class TextPart:
    """一段文本内容块。"""

    text: str


@dataclass(frozen=True)
class ImagePart:
    """一张图片内容块：持原始字节，编码与格式 / 大小校验在发送前统一做。"""

    data: bytes


@dataclass(frozen=True)
class VideoPart:
    """一段视频内容块：持原始字节与抽帧参数（随素材可调），编码转换在发送前统一做。

    fps / max_frames 是请求侧的抽帧参数（端点服务端按 min(fps × 时长, max_frames) 抽帧）；
    帧上限只是请求值，端点能力上限由服务端把关、超限以端点报错呈现。
    """

    data: bytes
    mime: str = "video/mp4"
    fps: float = 2.0
    max_frames: int = 16


# 消息内容块类型：文本块或图片块或视频块。
ContentPart = TextPart | ImagePart | VideoPart


@dataclass(frozen=True)
class Message:
    """一条 provider 中立消息：角色 + 有序内容块。

    用内容块序列（而非裸字符串）承载内容，让一条消息能有序表达多段现场内容
    （如注入的 skill 全文 + 用户指令），也便于按 provider 逐块转换。
    """

    role: Role
    parts: tuple[ContentPart, ...]
