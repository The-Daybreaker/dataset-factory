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


# 消息内容块类型：文本块或图片块。
ContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    """一条 provider 中立消息：角色 + 有序内容块。

    用内容块序列（而非裸字符串）承载内容，让一条消息能有序表达多段现场内容
    （如注入的 skill 全文 + 用户指令），也便于按 provider 逐块转换。
    """

    role: Role
    parts: tuple[ContentPart, ...]
