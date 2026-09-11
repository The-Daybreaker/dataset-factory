"""单元测试：llm 的图片编码与校验（magic bytes 识别格式 / 大小上限 / data URL）。

不依赖真实图片文件——只用带正确 magic 前缀的字节即可覆盖识别逻辑；大小上限用 monkeypatch
调小，避免真造 20MiB 数据。
"""

from __future__ import annotations

import base64

import pytest

from dataset_factory.llm import ImageTooLargeError, UnsupportedImageError, images

_PNG = b"\x89PNG\r\n\x1a\nrest"
_JPEG = b"\xff\xd8\xffrest"
_GIF87 = b"GIF87arest"
_GIF89 = b"GIF89arest"
_WEBP = b"RIFF\x00\x00\x00\x00WEBPrest"


@pytest.mark.parametrize(
    ("data", "subtype"),
    [
        (_PNG, "png"),
        (_JPEG, "jpeg"),
        (_GIF87, "gif"),
        (_GIF89, "gif"),
        (_WEBP, "webp"),
    ],
)
def test_encode_recognizes_supported_formats(data: bytes, subtype: str) -> None:
    """各支持格式按 magic bytes 认出来，编成对应 MIME 的 data URL、且 base64 可还原。"""
    url = images.encode_image_data_url(data)

    assert url.startswith(f"data:image/{subtype};base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == data


def test_encode_rejects_unsupported_format() -> None:
    """认不出的 magic bytes（如纯文本）→ UnsupportedImageError。"""
    with pytest.raises(UnsupportedImageError, match="格式"):
        images.encode_image_data_url(b"not an image at all")


def test_encode_rejects_empty_bytes() -> None:
    """空字节 → UnsupportedImageError（fail loud，不编出空 data URL）。"""
    with pytest.raises(UnsupportedImageError):
        images.encode_image_data_url(b"")


def test_encode_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过大小上限 → ImageTooLargeError；调小上限避免真造大图。"""
    monkeypatch.setattr(images, "_MAX_IMAGE_BYTES", 8)

    with pytest.raises(ImageTooLargeError, match="过大"):
        images.encode_image_data_url(_PNG)
