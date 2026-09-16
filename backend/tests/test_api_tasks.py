"""接口测试：tasks 横切端点（GET /tasks/{id} 轮询 + POST cancel + 404 problem+json）。

404 语义用 TestClient 直测；生命周期走 httpx ASGITransport（同一事件循环内受理任务，
绕开 TestClient 门户线程没有运行中事件循环的限制）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.tasks import TaskCancelledError, TaskManager

_WAIT_TIMEOUT = 5.0


@pytest.fixture
def client(tmp_path: Path, temp_data_root: Path) -> TestClient:
    """挂临时空 frontend 目录的测试客户端（依赖 temp_data_root 隔离数据根）。"""
    return TestClient(create_app(frontend_dir=tmp_path))


def test_get_unknown_task_returns_problem_json_404(client: TestClient) -> None:
    """未知任务 404：problem+json 四件套齐，detail 只指动作（不解释机制）。"""
    response = client.get("/api/tasks/no-such-id")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "task-not-found"
    assert body["title"] == "任务不存在"
    assert body["status"] == 404
    assert "重新执行" in body["detail"]


def test_cancel_unknown_task_returns_problem_json_404(client: TestClient) -> None:
    """对失效任务取消同样 404（同一失效语义）。"""
    response = client.post("/api/tasks/no-such-id/cancel")

    assert response.status_code == 404
    assert response.json()["type"] == "task-not-found"


def test_task_lifecycle_over_http(tmp_path: Path, temp_data_root: Path) -> None:
    """受理 → 轮询 running → 协作取消 → 轮询到 cancelled（全程 HTTP）。"""

    async def scenario() -> None:
        app = create_app(frontend_dir=tmp_path)
        manager = cast(TaskManager, app.state.task_manager)

        def body(task_id: str, should_stop: threading.Event) -> None:
            # 阻塞等取消信号（5s 护栏）：保证轮询时稳定处于 running。
            if should_stop.wait(timeout=_WAIT_TIMEOUT):
                raise TaskCancelledError()

        task_id = manager.create(body)
        base = "http://testserver"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base
        ) as http:
            # 受理后可查到 running（任务体阻塞在信号等待上，状态确定）。
            running = (await http.get(f"/api/tasks/{task_id}")).json()
            assert running["status"] == "running"
            assert running["result"] is None
            assert running["error"] is None

            # 协作取消：置信号即返回快照，状态此一刻仍是 running（任务体还没退出）。
            cancelled_response = await http.post(f"/api/tasks/{task_id}/cancel")
            assert cancelled_response.status_code == 200
            assert cancelled_response.json()["status"] == "running"

            # 轮询等待任务体在安全点退出、归档 cancelled。
            deadline = time.monotonic() + _WAIT_TIMEOUT
            status = ""
            while time.monotonic() < deadline:
                status = (await http.get(f"/api/tasks/{task_id}")).json()["status"]
                if status == "cancelled":
                    break
                await asyncio.sleep(0.01)
            assert status == "cancelled"

    asyncio.run(scenario())
