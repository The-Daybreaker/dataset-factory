"""可观测性基础设施：请求 id 的上下文传递与日志自动注入。

为什么单独一个模块：请求 id 这件事横跨「应用层设置」与「库层记录」两侧——api 入口层在
中间件里设置它，而 labeling / llm 这些库层模块只是在用标准 logging 发记录，**不需要知道
「HTTP 请求」这个概念**。这里提供的就是这条隐式通道：库层照常 `getLogger(__name__)`，
应用层在日志配置里挂上 `RequestIdFilter`，每行记录就自动带上当前请求的 id。

放在包根（私有模块，同 `_fs`）：它不含任何业务语义，api 与 cli 都可以 import，且不需要
改动 import-linter 的分层契约（契约只约束列入的模块）。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from time import perf_counter
from uuid import uuid4

# 当前请求 id：默认 None = 不在任何请求里（如 CLI 单发命令的执行上下文）。
# 用 ContextVar 而非线程局部变量，是因为 FastAPI 的请求可能跑在事件循环的同一线程上，
# 只有 contextvars 能保证「每个请求各看各的值」。
_request_id_var: ContextVar[str | None] = ContextVar(
    "dataset_factory_request_id", default=None
)


def new_request_id() -> str:
    """生成一个新的请求 id。

    用 uuid4 前 12 位十六进制——足够短（便于在日志里阅读与复制）且碰撞概率可忽略；
    请求 id 只用于本地排查，不承担安全职责。
    """
    return uuid4().hex[:12]


def set_request_id(request_id: str) -> Token[str | None]:
    """把请求 id 放进当前上下文，返回用于还原的 token。

    必须与 `reset_request_id` 成对使用，且 reset 放在 `finally` 里——否则这个 id 会
    沿着上下文继续影响到后续请求（业界踩过的坑：漏 reset 导致后续请求的日志带上旧 id）。

    Args:
        request_id: 要设置的请求 id。

    Returns:
        还原用的 token，交由 `reset_request_id` 使用。
    """
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """还原上下文到设置之前的状态（在 `finally` 中调用）。

    Args:
        token: `set_request_id` 返回的 token。
    """
    _request_id_var.reset(token)


def current_request_id() -> str | None:
    """取当前上下文的请求 id；不在任何请求上下文里时为 None。"""
    return _request_id_var.get()


def ms_since(start: float) -> float:
    """从 start（`time.perf_counter()` 的读数）到现在的毫秒数。

    用 perf_counter 而不是 time.time()：它是**单调时钟**，不受系统时间被调整（校时、手动改表）
    的影响，量出来的耗时才可信。各层（HTTP 接入 / 编排组装 / 模型调用）共用这一个换算，
    保证日志里的耗时口径一致。

    Args:
        start: `time.perf_counter()` 的读数。

    Returns:
        经过的毫秒数。
    """
    return (perf_counter() - start) * 1000


class RequestIdFilter(logging.Filter):
    """把当前请求 id 注入每条日志记录，供 formatter 用 `%(request_id)s` 取用。

    非请求上下文（如运行 `dsf label`）取不到 id 时填 `-`——保证 formatter 不会因为缺
    字段而抛错，也让「这条日志不属于任何 HTTP 请求」这件事一眼可见。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """给记录补上 request_id 字段（调用方已显式设置时不覆盖）。

        Args:
            record: 待处理的日志记录。

        Returns:
            恒为 True——本过滤器只补字段、不过滤记录。
        """
        if not hasattr(record, "request_id"):
            record.request_id = _request_id_var.get() or "-"
        return True
