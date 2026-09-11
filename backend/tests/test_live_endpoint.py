"""L3 真端点测试（档 2）：真模型、真密钥、真图片——整套测试里唯一会花钱的一层。

定位与纪律（与调研结论、design「测试体系」一致）：
- **不进日常 CI**：依赖外部服务 + 密钥，CI 里没有也永远不该有；本地手动触发：
    uv run pytest -m live
  没配 DSF_LIVE_API_KEY 时整文件跳过——`pytest`（不带 -m）在日常开发里零打扰。
- **断言只做弱断言**（成功 / 非空 / 迭代后输出与首轮不同），绝不断言内容——
  模型输出有随机性，内容断言必然脆、会把这层测试变成 flaky 大户。
- 覆盖风险点：密钥有效、base_url / 模型名正确、端点兼容 OpenAI 格式、
  返回可解析为 caption、**图片真的能被模型理解（视觉能力）**、多轮迭代真模型生效。

环境变量（三件套齐全才跑）：
- DSF_LIVE_BASE_URL  例：https://api.siliconflow.cn/v1
- DSF_LIVE_API_KEY   平台的密钥
- DSF_LIVE_MODEL     例：Qwen/Qwen3.5-4B
"""

from __future__ import annotations

import base64
import os
import socket
import struct
import threading
import time
import zlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from dataset_factory.api import app as system_app
from dataset_factory.llm import EndpointConfig, SecretValue, write_config

# ---------- 环境与跳过逻辑 ----------

_LIVE_BASE_URL = os.environ.get("DSF_LIVE_BASE_URL", "")
_LIVE_API_KEY = os.environ.get("DSF_LIVE_API_KEY", "")
_LIVE_MODEL = os.environ.get("DSF_LIVE_MODEL", "Qwen/Qwen3.5-4B")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (_LIVE_BASE_URL and _LIVE_API_KEY),
        reason="未配置 DSF_LIVE_BASE_URL / DSF_LIVE_API_KEY（L3 真端点测试只在本机手动跑）",
    ),
]


@pytest.fixture
def live_system_client(
    temp_data_root: Path,
) -> Iterator[tuple[httpx.Client, Path]]:
    """真实端点配置 + 线程内真服务的组合（复用 T17 的服务装配方式）。"""
    write_config(
        EndpointConfig(
            base_url=_LIVE_BASE_URL,
            model=_LIVE_MODEL,
            api_key=SecretValue(_LIVE_API_KEY),
        )
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(
            system_app, host="127.0.0.1", port=port, log_config=None, access_log=False
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("被测服务未能在超时内启动")
        time.sleep(0.05)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120.0)
    try:
        yield client, temp_data_root
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=5)


# ---------- 真实图片：测试内动态生成 64x64 纯红 PNG（零依赖） ----------


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """PNG 数据块 = 长度 + 类型 + 数据 + CRC。"""
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def tiny_real_png(width: int = 64, height: int = 64) -> str:
    """生成一张 64x64 纯红 PNG 并返回 base64（data URL 形式）。

    为什么不用 1x1 占位图：L3 的验证目标之一是「图片真的能被模型理解」——
    64x64 的纯色图是一张真实可解读的图（模型应描述出纯红色），1x1 只是字节占位。
    """
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit RGB
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ---------- 档 2 用例（3~5 条，带真实图片，含一轮迭代） ----------


def test_live_text_round(live_system_client: tuple[httpx.Client, Path]) -> None:
    """通道冒烟：纯文本指令真发到模型端点并拿到非空 caption。"""
    client, _ = live_system_client
    save = client.put(
        "/api/prompts/live-e2e",
        json={"description": "L3 用", "body": "你是图片打标助手，用一句中文描述图片。"},
    )
    assert save.status_code == 204

    response = client.post(
        "/api/label",
        json={
            "session_id": None,
            "prompt_name": "live-e2e",
            "skill_names": [],
            "instruction": "你好，请回复任意一句话验证通道。",
            "image_base64": None,
            "image_name": "x.png",
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["caption"]) > 0


def test_live_image_round(live_system_client: tuple[httpx.Client, Path]) -> None:
    """视觉通道：真图片进、caption 出——图片能被模型理解是打标主干。"""
    client, _ = live_system_client
    client.put(
        "/api/prompts/live-e2e",
        json={"description": "L3 用", "body": "你是图片打标助手，用一句中文描述图片。"},
    )
    response = client.post(
        "/api/label",
        json={
            "session_id": None,
            "prompt_name": "live-e2e",
            "skill_names": [],
            "instruction": "给这张图打个标",
            "image_base64": tiny_real_png(),
            "image_name": "red.png",
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["caption"]) > 0


def test_live_iteration_changes_output(
    live_system_client: tuple[httpx.Client, Path],
) -> None:
    """多轮迭代：第二轮基于同一会话改写，输出应与首轮不同（弱断言：只比「变了没有」）。"""
    client, _ = live_system_client
    client.put(
        "/api/prompts/live-e2e",
        json={"description": "L3 用", "body": "你是图片打标助手，用一句中文描述图片。"},
    )

    first = client.post(
        "/api/label",
        json={
            "session_id": None,
            "prompt_name": "live-e2e",
            "skill_names": [],
            "instruction": "给这张图打个标",
            "image_base64": tiny_real_png(),
            "image_name": "red.png",
        },
    )
    assert first.status_code == 200, first.text
    first_caption = first.json()["caption"]
    session_id = first.json()["session_id"]

    second = client.post(
        "/api/label",
        json={
            "session_id": session_id,
            "prompt_name": None,  # 续接会话：沿用会话设置
            "skill_names": None,
            "instruction": "改成两句话，描述更详细一些",
            "image_base64": None,
            "image_name": "red.png",
        },
    )
    assert second.status_code == 200, second.text
    second_caption = second.json()["caption"]

    assert len(second_caption) > 0
    assert second_caption != first_caption, (
        "迭代轮输出与首轮完全相同——多轮上下文可能没生效"
    )
    assert second.json()["session_id"] == session_id, "迭代轮应续接同一会话"
