"""L2 系统测试的本地假模型端点：一个真的 OpenAI 兼容 HTTP 服务。

为什么不用 mock：系统测试（L2）要验证的是「整个系统真实装配后，能否通过
真实网络与外部服务对话」。mock 拦在进程内存里，socket、超时、重试、HTTP 解码
这些环节全部被跳过——那就退化成了 L1。假端点是「真 HTTP 服务 + 假数据」，
只替换外部依赖（模型）本身。

可编程面：
- set_responses([...])   逐轮脚本化回复（与 conftest 的 FakeCompleter 同一习惯）
- fail_with(status)      让下一次请求返回指定错误码（测我们的错误映射真不真）
- requests               收到的请求体列表（断言「我们真的发出了什么」）
"""

from __future__ import annotations

import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeLLMEndpoint:
    """OpenAI 兼容假端点：线程内 uvicorn + FastAPI，绑 127.0.0.1 随机空闲端口。"""

    def __init__(self) -> None:
        """装配 FastAPI 应用、探测空闲端口、创建（但不启动）uvicorn 服务。"""
        self.app = FastAPI()
        self.requests: list[dict[str, Any]] = []
        self._responses: list[dict[str, Any] | int] = []
        self._persistent_error: int | None = None
        self._lock = threading.Lock()
        self.app.post("/v1/chat/completions")(self._handle_chat)

        # 端口策略：让 uvicorn 直接绑 0（内核分配），启动后从服务对象读真实端口——
        # 「先探测再交绑」在两步之间有把端口让给别人的竞态。
        self.port: int | None = None
        self._server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=0,
                log_config=None,
                access_log=False,
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def base_url(self) -> str:
        """写进 config.json 的 base_url（openai SDK 直接可用）。"""
        port = self.port
        assert port is not None, "start() 之前拿不到端口（由内核分配）"
        return f"http://127.0.0.1:{port}/v1"

    def set_responses(self, responses: list[dict[str, Any] | int]) -> None:
        """设置逐轮脚本：dict = 正常响应体；int = 该轮返回的 HTTP 错误码。"""
        self._responses = list(responses)

    def fail_with(self, status_code: int) -> None:
        """此后所有请求都返回指定错误码（模拟端点持续故障）。

        必须是「持续」而不是「仅下一次」：openai SDK 对 5xx 会按 max_retries
        自动重试，只失败一次会被重试吞掉——这恰恰是真实系统才会有的行为。
        """
        with self._lock:
            self._persistent_error = status_code

    def wait_ready(self, timeout: float = 10.0) -> None:
        """等待服务就绪（uvicorn 的 started 标志 + 轮询），就绪后读出真实端口。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                if self.port is None:
                    # 端口由内核分配，服务启动完成后才能从服务对象上读到。
                    servers = self._server.servers
                    self.port = servers[0].sockets[0].getsockname()[1]
                return
            time.sleep(0.05)
        raise RuntimeError("假端点未能在超时内就绪")

    def start(self) -> None:
        """后台线程启动并等待就绪。"""
        self._thread.start()
        self.wait_ready()

    def stop(self) -> None:
        """优雅停止（测试结束时调用；daemon 线程兜底，不会挂住进程退出）。"""
        self._server.should_exit = True
        self._thread.join(timeout=5)

    async def _handle_chat(self, request: Request) -> JSONResponse:
        """OpenAI /v1/chat/completions 的最小实现：记录请求、按脚本响应。"""
        body: dict[str, Any] = await request.json()
        with self._lock:
            self.requests.append(body)
            if self._persistent_error is not None:
                return JSONResponse(
                    status_code=self._persistent_error,
                    content={
                        "error": {
                            "message": f"假端点持续故障（模拟 {self._persistent_error}）"
                        }
                    },
                )
            scripted: dict[str, Any] | int | None = (
                self._responses.pop(0) if self._responses else None
            )
        if isinstance(scripted, int):
            return JSONResponse(
                status_code=scripted,
                content={"error": {"message": f"假端点按脚本返回 {scripted}"}},
            )
        content = (
            scripted.get("content", "假端点回复")
            if isinstance(scripted, dict)
            else "假端点回复"
        )
        return JSONResponse(
            status_code=200,
            content={
                "id": "chatcmpl-fake-001",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "fake-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
