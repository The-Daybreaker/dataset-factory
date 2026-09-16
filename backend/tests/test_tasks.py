"""单元测试：tasks 包任务状态机（成功 / 失败 / 协作取消 / 进度钳制 / 404 语义）。

无 pytest-asyncio 依赖，场景体用 asyncio.run 驱动（每个用例独立事件循环）；
等待终态一律轮询 + 超时护栏，不赌调度时机。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from dataset_factory.tasks import (
    RETRY_AFTER_SECONDS,
    TaskCancelledError,
    TaskManager,
    TaskNotFoundError,
)

_WAIT_TIMEOUT = 5.0


async def wait_for_status(manager: TaskManager, task_id: str, want: str) -> None:
    """轮询等任务到达目标状态（超时即败，不用裸 sleep 赌运气）。"""
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if manager.get(task_id).status == want:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"任务未在 {_WAIT_TIMEOUT}s 内到达 {want}，当前 {manager.get(task_id).status}"
    )


def test_task_id_loss_on_unknown_query() -> None:
    """查不存在的任务抛 TaskNotFoundError，消息指向「重新执行」动作。"""

    async def scenario() -> None:
        manager = TaskManager()
        with pytest.raises(TaskNotFoundError, match="重新执行"):
            manager.get("no-such-id")

    asyncio.run(scenario())


def test_succeeded_task_returns_result_payload() -> None:
    """正常返回的任务记 succeeded，返回值成为 result 载荷。"""

    async def scenario() -> None:
        manager = TaskManager()

        def body(task_id: str, should_stop: threading.Event) -> dict[str, object]:
            return {"copied": 3}

        task_id = manager.create(body)
        await wait_for_status(manager, task_id, "succeeded")
        info = manager.get(task_id)
        assert info.result == {"copied": 3}
        assert info.error is None

    asyncio.run(scenario())


def test_failed_task_carries_operable_error_message() -> None:
    """任务体抛普通异常记 failed，异常消息进 error（PRD 验收 12 可操作消息）。"""

    async def scenario() -> None:
        manager = TaskManager()

        def body(task_id: str, should_stop: threading.Event) -> None:
            raise ValueError("导入源目录不存在——请检查路径后重试。")

        task_id = manager.create(body)
        await wait_for_status(manager, task_id, "failed")
        info = manager.get(task_id)
        assert info.error is not None
        assert "导入源目录不存在" in info.error
        assert info.result is None

    asyncio.run(scenario())


def test_cancel_signal_reaches_worker_and_yields_cancelled() -> None:
    """协作式取消：信号到达工作线程，任务体在安全点抛 TaskCancelled 记 cancelled。"""

    async def scenario() -> None:
        manager = TaskManager()

        def body(task_id: str, should_stop: threading.Event) -> None:
            # 模拟逐文件处理的取消检查点：阻塞等信号（5s 护栏），等到即取消。
            if should_stop.wait(timeout=_WAIT_TIMEOUT):
                raise TaskCancelledError()

        task_id = manager.create(body)
        await asyncio.sleep(0.05)  # 让任务体先进入等待
        manager.request_cancel(task_id)
        await wait_for_status(manager, task_id, "cancelled")
        assert manager.get(task_id).error is None

    asyncio.run(scenario())


def test_finished_task_completes_even_when_cancel_requested_late() -> None:
    """收尾竞态语义：取消信号已置但任务体自然跑完 → 记 succeeded（以实际完成为准）。"""

    async def scenario() -> None:
        manager = TaskManager()

        def body(task_id: str, should_stop: threading.Event) -> dict[str, object]:
            time.sleep(0.15)  # 不检查信号，直接跑完
            return {"done": True}

        task_id = manager.create(body)
        await asyncio.sleep(0.02)
        manager.request_cancel(task_id)  # 置信号，但任务体不看它
        await wait_for_status(manager, task_id, "succeeded")
        assert manager.get(task_id).result == {"done": True}

    asyncio.run(scenario())


def test_progress_is_clamped_and_frozen_after_terminal() -> None:
    """进度钳到 0.0–1.0；终态后的回报为 no-op（不改动已归档状态）。"""

    async def scenario() -> None:
        manager = TaskManager()

        def body(task_id: str, should_stop: threading.Event) -> None:
            manager.set_progress(task_id, 1.5)  # 越上界 → 1.0
            manager.set_progress(task_id, -1.0)  # 越下界 → 0.0

        task_id = manager.create(body)
        await wait_for_status(manager, task_id, "succeeded")
        manager.set_progress(task_id, 0.5)  # 终态后回报无效
        assert manager.get(task_id).progress == 0.0

    asyncio.run(scenario())


def test_cancel_on_unknown_task_raises_not_found() -> None:
    """对不存在的任务取消同样 404（同一失效语义）。"""

    async def scenario() -> None:
        manager = TaskManager()
        with pytest.raises(TaskNotFoundError, match="重新执行"):
            manager.request_cancel("no-such-id")

    asyncio.run(scenario())


def test_cancel_after_terminal_is_idempotent_noop() -> None:
    """已终态任务再取消 = 幂等 no-op：返回现状、不再变体、不抛错。"""

    async def scenario() -> None:
        manager = TaskManager()

        def body(task_id: str, should_stop: threading.Event) -> None:
            return None

        task_id = manager.create(body)
        await wait_for_status(manager, task_id, "succeeded")
        info = manager.request_cancel(task_id)
        assert info.status == "succeeded"

    asyncio.run(scenario())


def test_retry_after_constant_is_two_seconds() -> None:
    """202 受理响应的 Retry-After 建议间隔 = 2 秒（design 2026-09-16 定）。"""
    assert RETRY_AFTER_SECONDS == 2
