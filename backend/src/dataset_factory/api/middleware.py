"""HTTP 请求观测中间件：给每个请求分配 request id、记录一条含耗时的访问日志。

为什么应用自己记、而不用 uvicorn 自带的 access log：uvicorn 的默认访问日志既**没有耗时**、
格式也改不动，而且它属于 INFO 级——本项目 `dsf serve` 原先把 `log_level` 设成 `warning`，
等于把请求记录整体静音（用户报「发了消息很久没回应、查不了卡在哪一层」的直接原因）。
这里接管访问日志后，`dsf serve` 会把 uvicorn 的 access log 关掉，避免同一请求打两遍。

请求 id 的落地方式见 `_obs`：中间件只负责「设置 + 还原」，库层靠日志过滤器自动带上它。
"""

from __future__ import annotations

import logging
import re
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .._obs import ms_since, new_request_id, reset_request_id, set_request_id

logger = logging.getLogger(__name__)

# 请求 id 的响应头名（沿用业界通行写法，便于与其它工具对接）。
REQUEST_ID_HEADER = "X-Request-ID"

# 外来请求 id 只在「安全的单段短 token」时才采纳：id 会进日志并回显到响应头，照单全收
# 任意客户端内容等于敞开日志伪造与响应头注入（换行、控制字符尤其危险）。
_ADOPTED_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """记录 `method / path / status / 耗时`，并把 request id 写进响应头。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """分配请求 id → 交给下游处理 → 记一条访问日志（成功与未捕获异常都记）。

        时间口径是「整个请求在处理管线里待了多久」，包含路由、业务组装、模型调用与落盘；
        更细的分层耗时由 labeling / llm 各自记录（只有它们知道自己那一段花了多久）。

        下游抛出的未捕获异常（bug 一类）在这里收口：完整堆栈进日志（ERROR 级），响应是
        带请求 id 的 500 JSON（给用户干净摘要、不甩栈）。这是入口层边界唯一一次宽捕获
        （错误分级见 design「日志与错误呈现」）——最需要对照日志的程序性 500 恰恰不能
        少了 request id。

        Args:
            request: 本次 HTTP 请求。
            call_next: 交给下游处理管线的回调。

        Returns:
            下游返回的响应（附带 request id 响应头）；未捕获异常时是带请求 id 的 500 JSON。
        """
        incoming = request.headers.get(REQUEST_ID_HEADER)
        if incoming is not None and _ADOPTED_REQUEST_ID.fullmatch(incoming):
            request_id = incoming
        else:
            request_id = new_request_id()
        token = set_request_id(request_id)
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 入口层边界的唯一宽捕获（错误分级见类 docstring）；记完整堆栈后收口成 500，
            # 不 re-raise——交给 Starlette 兜底反而丢掉响应头里的 request id。
            logger.exception(
                "HTTP %s %s 处理时抛出未捕获异常（%.0fms）",
                request.method,
                request.url.path,
                ms_since(start),
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "服务器内部错误（程序 bug 一类），不是你的输入问题；"
                    "请带着本响应的 X-Request-ID 反馈，便于在后端日志中定位。"
                },
                headers={REQUEST_ID_HEADER: request_id},
            )
        finally:
            reset_request_id(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "HTTP %s %s -> %d（%.0fms）",
            request.method,
            request.url.path,
            response.status_code,
            ms_since(start),
        )
        return response
