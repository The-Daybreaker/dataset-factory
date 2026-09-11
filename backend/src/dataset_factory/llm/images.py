"""图片编码与校验：把原始图片字节编码成 OpenAI 兼容的 data URL，发送前校验格式与大小。

只接受常见 web 图片格式（png / jpeg / webp / gif），靠 magic bytes 识别、不信调用方声明；
超过大小上限直接拒（fail-fast），免得白跑一趟 API 才拿到难懂的错误。
"""

from __future__ import annotations

import base64

from .errors import ImageTooLargeError, UnsupportedImageError

# 单图字节上限，对齐常见 OpenAI 兼容端点；可调。
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_SUPPORTED_FORMATS = "png / jpeg / webp / gif"


def encode_image_data_url(data: bytes) -> str:
    """把图片字节编码成 data:image/<type>;base64,<data>，编码前先校验格式与大小。

    Args:
        data: 图片原始字节。

    Returns:
        OpenAI 兼容 image_url 用的 data URL 字符串。

    Raises:
        UnsupportedImageError: magic bytes 识别不出支持的格式（含空数据）。
        ImageTooLargeError: 字节数超过上限。
    """
    subtype = _sniff_image_subtype(data)
    if len(data) > _MAX_IMAGE_BYTES:
        limit_mib = _MAX_IMAGE_BYTES // (1024 * 1024)
        actual_mib = len(data) / (1024 * 1024)
        raise ImageTooLargeError(
            f"图片过大（{actual_mib:.1f} MiB，上限 {limit_mib} MiB）；请压缩后重试。"
        )
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/{subtype};base64,{encoded}"


def _sniff_image_subtype(data: bytes) -> str:
    """从 magic bytes 识别图片格式，返回 MIME 子类型。

    Raises:
        UnsupportedImageError: 前缀不匹配任何支持格式。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise UnsupportedImageError(
        f"无法识别图片格式（仅支持 {_SUPPORTED_FORMATS}）；请确认文件是有效图片。"
    )
