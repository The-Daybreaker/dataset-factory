"""可观测性测试：请求 id 机制（_obs）与 HTTP 访问日志中间件。

分两层：先直接测 `_obs` 的零件（上下文、过滤器、耗时换算），再用一个最小 FastAPI 应用
验证中间件的对外行为（响应头、访问日志、异常路径、请求期间上下文可见）。

写法上有一处刻意的取舍：路由用「显式注册」而不是 `@app.get` 装饰器——装饰器写法下静态
类型检查看不到函数被调用，会报「函数未使用」；同理，过滤器注入的 `request_id` 是 logging
的动态属性、不在 `LogRecord` 的静态类型里，所以这里改成「用 formatter 渲染后断言文本」，
既不碰动态属性、又更贴近它实际被使用的样子。
"""

from __future__ import annotations

import logging
from time import perf_counter

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from dataset_factory._obs import (
    RequestIdFilter,
    current_request_id,
    ms_since,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from dataset_factory.api.middleware import REQUEST_ID_HEADER, RequestLogMiddleware

_FORMAT = "[%(request_id)s] %(message)s"


def _make_record() -> logging.LogRecord:
    """造一条日志记录，供过滤器测试使用。"""
    return logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)


@pytest.fixture
def mini_app() -> FastAPI:
    """最小应用：一个正常端点 + 一个必抛异常的端点，专供中间件行为测试。"""
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)

    def ping() -> dict[str, str]:
        """正常返回，供断言响应头与访问日志。"""
        return {"pong": "1"}

    def boom() -> None:
        """必定抛出未捕获异常，供断言异常路径的日志。"""
        raise RuntimeError("模拟未捕获异常")

    app.get("/ping")(ping)
    app.get("/boom")(boom)
    return app


def test_new_request_id_is_short_hex() -> None:
    """请求 id 是 12 位十六进制、且每次都不一样。"""
    first = new_request_id()
    assert len(first) == 12
    assert first.isalnum()
    assert new_request_id() != first


def test_context_round_trip() -> None:
    """设置后能取到、还原后回到 None——这是「不串到下一个请求」的基础。"""
    assert current_request_id() is None
    token = set_request_id("abc123")
    assert current_request_id() == "abc123"
    reset_request_id(token)
    assert current_request_id() is None


def test_filter_fills_placeholder_outside_request() -> None:
    """非请求上下文（如运行 CLI 单发命令）渲染成 `-`，保证格式化不缺字段。"""
    record = _make_record()
    RequestIdFilter().filter(record)
    assert logging.Formatter(_FORMAT).format(record) == "[-] msg"


def test_filter_fills_current_request_id() -> None:
    """请求上下文里渲染出当前请求 id。"""
    token = set_request_id("xyz789")
    try:
        record = _make_record()
        RequestIdFilter().filter(record)
        assert logging.Formatter(_FORMAT).format(record) == "[xyz789] msg"
    finally:
        reset_request_id(token)


def test_filter_keeps_explicit_value() -> None:
    """调用方已显式设置 request_id 时不覆盖。"""
    record = _make_record()
    record.__dict__["request_id"] = "explicit"
    RequestIdFilter().filter(record)
    assert logging.Formatter(_FORMAT).format(record) == "[explicit] msg"


def test_ms_since_is_non_negative() -> None:
    """耗时换算返回非负值。"""
    assert ms_since(perf_counter()) >= 0


def test_response_carries_request_id(mini_app: FastAPI) -> None:
    """每个响应都带上 request id 响应头（用户可据此把界面报错对到后端日志）。"""
    with TestClient(mini_app) as client:
        response = client.get("/ping")
    assert response.status_code == 200
    assert len(response.headers[REQUEST_ID_HEADER]) == 12


def test_incoming_request_id_is_reused(mini_app: FastAPI) -> None:
    """请求自带的 X-Request-ID 被复用（把跨工具的一条链路串起来）。"""
    with TestClient(mini_app) as client:
        response = client.get("/ping", headers={REQUEST_ID_HEADER: "given-id-001"})
    assert response.headers[REQUEST_ID_HEADER] == "given-id-001"


def test_request_id_visible_inside_request() -> None:
    """请求处理期间上下文里有 request id——库层的日志正是靠它自动带上的。"""
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    seen: list[str | None] = []

    def probe() -> dict[str, str]:
        """把当前请求 id 记下来供断言。"""
        seen.append(current_request_id())
        return {}

    app.get("/probe")(probe)
    with TestClient(app) as client:
        client.get("/probe", headers={REQUEST_ID_HEADER: "probe-001"})
    assert seen == ["probe-001"]


def test_access_log_records_method_path_status_duration(
    mini_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """访问日志里能看到方法、路径、状态码与耗时——排查「卡在哪一层」的入口。"""
    caplog.set_level(logging.INFO, logger="dataset_factory.api.middleware")
    with TestClient(mini_app) as client:
        client.get("/ping")
    assert any(
        "GET /ping -> 200" in record.message and "ms" in record.message
        for record in caplog.records
    )


def test_unhandled_exception_returns_500_with_request_id(
    mini_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """未捕获异常收口成带请求 id 的 500：堆栈进日志（ERROR），给用户干净摘要、不甩栈。"""
    caplog.set_level(logging.ERROR, logger="dataset_factory.api.middleware")
    with TestClient(mini_app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert len(response.headers[REQUEST_ID_HEADER]) == 12
    detail = response.json()["detail"]
    assert "服务器内部错误" in detail
    assert "模拟未捕获异常" not in detail
    assert any(
        record.levelno == logging.ERROR and "未捕获异常" in record.message
        for record in caplog.records
    )


def test_invalid_incoming_request_id_is_replaced(mini_app: FastAPI) -> None:
    """请求自带的 request id 不合法（控制字符 / 超长）时换新生成的，不照单回显。"""
    hostile = "bad id\nwith-newline"
    with TestClient(mini_app) as client:
        response = client.get("/ping", headers={REQUEST_ID_HEADER: hostile})
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] != hostile
    assert len(response.headers[REQUEST_ID_HEADER]) == 12
