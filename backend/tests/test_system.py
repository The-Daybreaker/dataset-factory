"""L2 系统测试：整个系统真实装配 + 真实 HTTP，只把外部依赖（模型）换成本地假端点。

与 L1（TestClient / mock llm）的边界：
- 这里起的是**真的 uvicorn 服务**（真 TCP、真 socket、真中间件链），用 httpx 发真实请求；
- llm 调用走 openai SDK → 本地假端点（tests/fake_llm_endpoint.py），真 HTTP 往返；
- 数据真的落盘（临时数据根），文件系统状态参与断言。

业界经验：这一层「稳定、快、不花钱」，所以随 CI 跑；真端点测试（L3）才需要密钥。
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn

from dataset_factory.llm import EndpointConfig, SecretValue, write_config

from .fake_llm_endpoint import FakeLLMEndpoint

# 1x1 透明 PNG：系统测试不需要真图，只需要「服务端认为合法的图片字节」。
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAA"
    "AABJRU5ErkJggg=="
)


@pytest.fixture
def fake_endpoint() -> Iterator[FakeLLMEndpoint]:
    """本地假模型端点：随用随起、用完即停。"""
    endpoint = FakeLLMEndpoint()
    endpoint.start()
    yield endpoint
    endpoint.stop()


@pytest.fixture
def system_port(temp_data_root: Path) -> Iterator[int]:
    """被测系统本身：线程内起真的 uvicorn 服务（完整 HTTP 栈），返回它的端口。

    服务是模块级单例 app 的真实装配——中间件、错误处理器、静态托管全部在线。
    与生产 `dsf serve` 的唯一差别是「同一进程内的另一个线程」而不是独立进程；
    对本地单进程工具而言，这个差别不影响被验证的行为。
    """
    from dataset_factory.api import app

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_config=None, access_log=False
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("被测服务未能在超时内启动")
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def system_client(
    temp_data_root: Path, system_port: int, fake_endpoint: FakeLLMEndpoint
) -> Iterator[httpx.Client]:
    """指向真实服务的 httpx 客户端。

    temp_data_root（conftest）把 DATASET_FACTORY_HOME 指到临时目录——线程内跑的服务
    与测试同进程，env 即时生效。这里把端点配置**真的写进磁盘**，让服务走真实的
    配置读取路径（而不是测试里直接注入对象）。

    刻意**不用** httpx 的 ASGITransport：那是进程内直调 app，会绕过 TCP/socket/
    真实网络栈——那是 L1 的做法。这里不带 transport，直连 127.0.0.1 上的真端口，
    请求真正经过完整的 HTTP 协议栈（连接、编码、解码、超时）。
    """
    write_config(
        EndpointConfig(
            base_url=fake_endpoint.base_url,
            model="fake-label-model",
            api_key=SecretValue("sk-fake-for-system-test"),
        )
    )
    with httpx.Client(
        base_url=f"http://127.0.0.1:{system_port}",
        timeout=30.0,
    ) as client:
        yield client


def test_full_labeling_flow_over_real_http(
    system_client: httpx.Client,
    temp_data_root: Path,
    fake_endpoint: FakeLLMEndpoint,
) -> None:
    """全链路：建提示词 → 发图打标 → 模型经真 HTTP 返回 → 会话真的落盘。"""
    # 1. 通过 HTTP 建一条基础提示词（真实走 prompts 库的写盘路径）。
    save = system_client.put(
        "/api/prompts/sys-e2e",
        json={"description": "系统测试用", "body": "你是图片打标助手。"},
    )
    assert save.status_code == 204, save.text

    # 2. 假端点按脚本回复；发一轮带图打标。
    fake_endpoint.set_responses([{"content": "一只赛博朋克风格的猫"}])
    label = system_client.post(
        "/api/label",
        json={
            "session_id": None,
            "prompt_name": "sys-e2e",
            "skill_names": [],
            "instruction": "给这张图打个标",
            "image_base64": TINY_PNG_BASE64,
            "image_name": "cat.png",
        },
    )
    assert label.status_code == 200, label.text
    body = label.json()
    assert body["caption"] == "一只赛博朋克风格的猫"
    assert body["session_id"]

    # 3. 请求 id 贯穿（可观测性契约）：错误才有意义的前提是成功路径也有 id。
    assert label.headers.get("X-Request-ID")

    # 4. 假端点收到的请求是真的 OpenAI 形状：模型名、消息里带 data URL 图。
    #    cast 是安全的：请求体由我们自己的 llm 客户端构造，形状是已知契约。
    assert fake_endpoint.requests, "假端点没有收到任何请求"
    sent = fake_endpoint.requests[0]
    assert sent["model"] == "fake-label-model"
    image_parts: list[dict[str, Any]] = []
    for message in cast(list[dict[str, Any]], sent["messages"]):
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for part in cast(list[Any], content):
            if not isinstance(part, dict):
                continue
            entry = cast(dict[str, Any], part)
            if entry.get("type") == "image_url":
                image_parts.append(entry)
    assert image_parts, "消息里没有图片部分"
    image_url = image_parts[0]["image_url"]["url"]
    assert isinstance(image_url, str)
    assert image_url.startswith("data:image/png;base64,")

    # 5. 会话真的写进了磁盘（临时数据根下 sessions/<id>/events.jsonl）。
    session_dir = temp_data_root / "sessions" / body["session_id"]
    assert (session_dir / "events.jsonl").is_file(), "会话事件没有落盘"
    events = [
        json.loads(line)
        for line in (session_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    roles = [event["role"] for event in events if "role" in event]
    assert "user" in roles
    assert "assistant" in roles


def test_error_path_real_http(
    system_client: httpx.Client, fake_endpoint: FakeLLMEndpoint
) -> None:
    """模型端点真返回 500 时，系统把它映射成 502 且 detail 可读（错误链路真走一遍）。

    顺带验证一个只有真实系统才看得到的行为：openai SDK 对 5xx 按 max_retries=2
    自动重试——假端点会收到 3 次请求（1 次原始 + 2 次重试）。L1 的 mock 永远
    测不出这层语义。
    """
    system_client.put(
        "/api/prompts/sys-e2e",
        json={"description": "系统测试用", "body": "你是图片打标助手。"},
    )
    fake_endpoint.fail_with(500)
    response = system_client.post(
        "/api/label",
        json={
            "session_id": None,
            "prompt_name": "sys-e2e",
            "skill_names": [],
            "instruction": "打标",
            "image_base64": None,
            "image_name": "x.png",
        },
    )
    assert response.status_code == 502
    assert "模型" in response.json()["detail"]
    assert len(fake_endpoint.requests) == 3, "SDK 应该自动重试 2 次（共 3 次调用）"


def test_session_recovery_over_real_http(
    system_client: httpx.Client, fake_endpoint: FakeLLMEndpoint
) -> None:
    """发一轮 → GET /api/sessions/latest 拿到的快照与请求一致（恢复链路）。"""
    system_client.put(
        "/api/prompts/sys-e2e",
        json={"description": "系统测试用", "body": "你是图片打标助手。"},
    )
    fake_endpoint.set_responses([{"content": "第二轮回复"}])
    first = system_client.post(
        "/api/label",
        json={
            "session_id": None,
            "prompt_name": "sys-e2e",
            "skill_names": [],
            "instruction": "第一轮",
            "image_base64": TINY_PNG_BASE64,
            "image_name": "a.png",
        },
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    snapshot = system_client.get("/api/sessions/latest")
    assert snapshot.status_code == 200
    data = snapshot.json()
    assert data["session_id"] == session_id
    assert data["settings"]["prompt_name"] == "sys-e2e"
    assert data["messages"][-1] == {
        "role": "assistant",
        "text": "第二轮回复",
        "attachment": None,
    }


def test_tiny_png_is_valid_image_bytes() -> None:
    """守门测试：常量图片必须是合法 PNG（避免上面的链路测试静默失效）。"""
    raw = base64.b64decode(TINY_PNG_BASE64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
