"""llm 的 provider 中立消息模型——不绑定任何厂商接口的消息表示。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 消息角色（OpenAI 兼容接口的事实）：system 系统指令、user 现场内容、assistant 模型历史回复。
Role = Literal["system", "user", "assistant"]

# 视频扩展名 → MIME（video_url data URL 前缀）与支持集合——入口层（CLI / HTTP）与引擎
# 共用的单一事实源，防三处口径漂移（audit 2026-09-14）；未识别的扩展名由调用方回落 mp4。
VIDEO_MIME_BY_SUFFIX: dict[str, str] = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}
VIDEO_EXTENSIONS = frozenset(VIDEO_MIME_BY_SUFFIX)

# 图片扩展名 → MIME 与窄清单（OpenAI 兼容端点事实标准 + torchvision 训练生态；design 项 12）
# ——与视频扩展名同为媒体格式事实，集中在此作单一事实源（workdir 导入、labeling 纯素材
# 路径、素材预览端点共用，防多处各写一份清单悄悄漂移）。
# 这份映射只用于「把文件原样提供给浏览器」时定 Content-Type；送模型的图片格式判定走
# magic bytes 嗅探（images.py 的 _sniff_image_subtype）——扩展名可以撒谎、字节不会，
# 两者用途不同不可互换。
IMAGE_MIME_BY_SUFFIX: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
IMAGE_EXTENSIONS = frozenset(IMAGE_MIME_BY_SUFFIX)

# 视频字节上限（二期新增）：批量无人值守必须本地限制——GB 级视频会整读进内存、
# base64 再胀 1.33 倍。与 MAX_IMAGE_BYTES 同为「运行时读取前再验」的单一事实源
# （design「素材扫描窄清单与大小护栏」）；低于 OpenAI 官方 512 MB payload 上限，
# 为第三方兼容网关与内存峰值留余量。
MAX_VIDEO_BYTES = 100 * 1024 * 1024


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
    fps 为整型——端点（SiliconFlow）对浮点 fps 判参数非法（实测错误码 20015），
    用整型把「抽帧步进只能是整数帧/秒」钉进类型契约。
    """

    data: bytes
    mime: str = "video/mp4"
    fps: int = 2
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


@dataclass(frozen=True)
class StreamDelta:
    """流式输出的一个增量：思考（reasoning）或正文（content）的一小段文本。

    reasoning_content 是思考型模型（如 Qwen3.5 thinking）的端点扩展字段，OpenAI 标准
    增量只有 content；不支持思考的端点自然只产 content 增量。
    """

    kind: Literal["reasoning", "content"]
    text: str
