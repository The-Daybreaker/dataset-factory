"""RFC 9457 problem+json 错误响应助手（二期新端点统一错误形）。

分工（design 横切约定）：一期端点保持 ``{"detail"}`` 简形不返工（前端读 detail
字段兼容两者）；二期新端点一律 problem+json——``type`` 放机器可读的 slug、
``title`` 短语、``status`` 状态码、``detail`` 中文可操作消息，后续扩展字段
（occupier / 冲突清单等）按需附加。
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

__all__ = ["PROBLEM_MEDIA_TYPE", "problem_response"]

#: RFC 9457 规定的 problem+json 媒体类型。
PROBLEM_MEDIA_TYPE = "application/problem+json"


def problem_response(
    status_code: int,
    type_slug: str,
    title: str,
    detail: str,
    extras: dict[str, Any] | None = None,
) -> JSONResponse:
    """构造一个 problem+json 错误响应。

    Args:
        status_code: HTTP 状态码（同时写入响应体 status 字段）。
        type_slug: 机器可读的错误类别短标识（如 ``task-not-found``）。
        title: 人读的短语概括（如「任务不存在」）。
        detail: 中文可操作消息——讲清下一步该做什么（PRD 验收 12）。
        extras: 扩展字段（RFC 9457 允许 extension members，如占用者信息 occupier）；
            None = 不附加。
    """
    content: dict[str, Any] = {
        "type": type_slug,
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if extras:
        content.update(extras)
    return JSONResponse(
        status_code=status_code,
        content=content,
        media_type=PROBLEM_MEDIA_TYPE,
    )
