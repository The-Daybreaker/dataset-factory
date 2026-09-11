"""E2E 专用的一体化被测服务：真实系统 + 假模型端点，一个进程一个端口。

为什么这么做：Playwright 的 webServer 选项要求「起一个 URL 可等待的服务」。
把被测系统（dataset_factory 的 FastAPI app）和假 LLM 端点 mount 到同一个
app 上，E2E 测试只需要一个端口；打标请求经真实浏览器 → 真 HTTP → 打标引擎
→ openai SDK → 假端点（同源另一个路径）→ 落盘 → 响应，链路里的每一跳都是真的，
只有「模型本身」是假的——这正是系统测试的边界选择在 E2E 层的复用。

运行方式由 playwright.config.ts 的 webServer 负责（uv run --project ../backend），
本脚本只管装配与启动，Ctrl+C 退出。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# dataset_factory 源码在 backend/src（e2e 工程没有自己的 Python 环境）。
BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

# 数据根隔离：E2E 绝不碰真实的 ~/.dataset_factory。
data_home = tempfile.mkdtemp(prefix="dsf-e2e-")
os.environ["DATASET_FACTORY_HOME"] = data_home

from dataset_factory.api import app as system_app
from dataset_factory.llm import EndpointConfig, SecretValue, write_config
from fastapi import FastAPI
from fastapi.responses import JSONResponse

PORT = 8765


def build_fake_llm_app() -> FastAPI:
    """OpenAI 兼容假端点（E2E 版）：固定回复，够冒烟用。

    与 backend/tests 的 FakeLLMEndpoint 同一思路，但这里不需要可编程性
    （浏览器流程只跑通链路），所以实现收窄成一个固定响应。
    """
    app = FastAPI()

    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict) -> JSONResponse:
        return JSONResponse(
            {
                "id": "chatcmpl-e2e-001",
                "object": "chat.completion",
                "created": 0,
                "model": payload.get("model", "fake-e2e-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "E2E 假模型的打标结果",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )

    return app


def main() -> None:
    # 端点配置预先写进临时数据根：打标请求将指向同源 /fake-llm/v1。
    write_config(
        EndpointConfig(
            base_url=f"http://127.0.0.1:{PORT}/fake-llm/v1",
            model="fake-e2e-model",
            api_key=SecretValue("sk-e2e-not-a-real-key"),
        )
    )
    # 假端点必须**插在路由表最前**：system_app 已经有一个 mount("/", StaticFiles)
    # （前端托管），Starlette 按注册顺序匹配，/ 前缀会吞掉后面所有路径——
    # 后置的 mount("/fake-llm") 永远轮不到，POST 还会被 StaticFiles 回 405。
    from starlette.routing import Mount

    system_app.routes.insert(0, Mount("/fake-llm", app=build_fake_llm_app()))

    import uvicorn

    uvicorn.run(
        system_app, host="127.0.0.1", port=PORT, log_config=None, access_log=False
    )


if __name__ == "__main__":
    main()
