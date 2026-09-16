"""导出 HTTP 闭环：计划、排除、202 任务、下载与失败响应。"""

import asyncio
import io
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app, routes_export
from dataset_factory.export import ExportPlan, write_export
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets
from dataset_factory.workdir.locks import RunLock, RunOccupiedError


def _batch(workdir: Path, active: bool = True) -> None:
    """登记一个无需调用模型的批次，真实文件用于验证打包。"""

    def mutate(state: dict[str, object]) -> None:
        state["batches"] = [
            {
                "seq": 1,
                "name": "test",
                "description": "",
                "snapshot": "s1.json",
                "active": active,
                "created_at": "now",
            }
        ]
        state["exclusions"] = {"1": ["b"]}

    WorkdirStore(workdir).mutate_state(mutate)


def test_export_plan_task_and_download(tmp_path: Path, temp_data_root: Path) -> None:
    """HTTP 全链路只交付未排除的配对，任务返回真实可下载 ZIP。"""
    for name in ("a", "b"):
        (tmp_path / f"{name}.jpg").write_bytes(name.encode())
        (tmp_path / f"s1__{name}.txt").write_text(f"caption {name}", encoding="utf-8")
    import_assets(tmp_path)
    _batch(tmp_path)
    exports = tmp_path / ".dsf" / "export"
    exports.mkdir()
    stale = exports / ".dsf-export-interrupted.tmp"
    stale.write_bytes(b"unfinished")
    previous = exports / "previous.zip"
    previous.write_bytes(b"keep")
    entry = WorkdirRegistry.register(tmp_path)
    app = create_app(frontend_dir=tmp_path / "frontend")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            prefix = f"/api/workdirs/{entry.id}/export"
            response = await client.get(f"{prefix}/plan?batch=s1")
            assert response.status_code == 200
            assert response.json()["included"][0]["asset_name"] == "001.jpg"
            assert response.json()["excluded"][0]["reason"] == "用户排除"
            accepted = await client.post(prefix, json={"batch": "s1"})
            assert accepted.status_code == 202
            assert accepted.headers["retry-after"] == "2"
            async with asyncio.timeout(10):
                while True:
                    task = (
                        await client.get(f"/api/tasks/{accepted.json()['task_id']}")
                    ).json()
                    if task["status"] != "running":
                        break
                    await asyncio.sleep(0.01)
            assert task["status"] == "succeeded", task
            assert task["progress"] == 1
            download = await client.get(task["result"]["download_url"])
            assert download.status_code == 200
            assert download.headers["content-type"] == "application/zip"
            assert "attachment" in download.headers["content-disposition"]
            with ZipFile(io.BytesIO(download.content)) as archive:
                assert archive.namelist() == ["001.jpg", "001.txt"]
                assert archive.read("001.txt") == b"caption a"

    asyncio.run(scenario())

    assert not stale.exists()
    assert previous.read_bytes() == b"keep"


def test_export_rejects_hidden_batch_and_invalid_download(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """停用批次不能计划或发起导出，下载文件名不能访问工具元数据。"""
    _batch(tmp_path, active=False)
    entry = WorkdirRegistry.register(tmp_path)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))
    prefix = f"/api/workdirs/{entry.id}/export"

    hidden = client.get(f"{prefix}/plan?batch=s1")
    start = client.post(prefix, json={"batch": "s1"})
    invalid = client.get(f"{prefix}/files/state.json")

    assert hidden.status_code == 409
    assert start.status_code == 409
    assert invalid.status_code == 400
    assert invalid.json()["type"] == "export-invalid"
    assert client.get(f"{prefix}/plan?batch=s99").status_code == 404


async def _terminal(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    """在有界等待中读取真实 HTTP 任务终态。"""
    async with asyncio.timeout(10):
        while True:
            task = (await client.get(f"/api/tasks/{task_id}")).json()
            if task["status"] != "running":
                return task
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("outcome", ["success", "cancel", "failure"])
def test_export_task_holds_and_releases_run_lock(
    tmp_path: Path,
    temp_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """真实导出任务持锁时拒绝竞争者，成功、取消和失败均释放锁。"""
    (tmp_path / "a.jpg").write_bytes(b"asset")
    (tmp_path / "s1__a.txt").write_text("caption", encoding="utf-8")
    import_assets(tmp_path)
    _batch(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    entered, release = Event(), Event()

    def gated_write(
        workdir: Path,
        plan: ExportPlan,
        destination: Path,
        *,
        should_stop: Event | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> Path:
        entered.set()
        if not release.wait(5):
            raise TimeoutError("测试未放行导出")
        if outcome == "failure":
            raise OSError("模拟磁盘写入失败")
        return write_export(
            workdir, plan, destination, should_stop=should_stop, progress=progress
        )

    monkeypatch.setattr(routes_export, "write_export", gated_write)
    app = create_app(frontend_dir=tmp_path / "frontend")
    competitor = RunLock(WorkdirStore(tmp_path).dsf_path)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            accepted = await client.post(
                f"/api/workdirs/{entry.id}/export", json={"batch": "s1"}
            )
            task_id = accepted.json()["task_id"]
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                with pytest.raises(RunOccupiedError):
                    competitor.acquire({"batch": "s1"})
                if outcome == "cancel":
                    await client.post(f"/api/tasks/{task_id}/cancel")
            finally:
                competitor.release()
                release.set()
            task = await _terminal(client, task_id)
            assert (
                task["status"]
                == {"success": "succeeded", "cancel": "cancelled", "failure": "failed"}[
                    outcome
                ]
            )
            if outcome != "success":
                assert task["result"] is None
                assert not list((tmp_path / ".dsf/export").glob("*.zip"))

    asyncio.run(scenario())

    competitor.acquire({"batch": "s1"})
    competitor.release()
    assert not list((tmp_path / ".dsf/export").glob("*.tmp"))


@pytest.mark.parametrize("occupied", [False, True])
def test_export_task_fails_cleanly_for_empty_plan_or_occupied_workdir(
    tmp_path: Path, temp_data_root: Path, occupied: bool
) -> None:
    """空计划或目录被跑批占用时任务明确失败，不发布空包且不泄漏锁。"""
    _batch(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    app = create_app(frontend_dir=tmp_path / "frontend")
    lock = RunLock(WorkdirStore(tmp_path).dsf_path)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            accepted = await client.post(
                f"/api/workdirs/{entry.id}/export", json={"batch": "s1"}
            )
            task = await _terminal(client, accepted.json()["task_id"])
            assert task["status"] == "failed"
            assert ("占用" if occupied else "没有可打包") in task["error"]
            assert task["result"] is None
            missing = await client.get(
                f"/api/workdirs/{entry.id}/export/files/s1-{'a' * 24}.zip"
            )
            assert missing.status_code == 404

    if occupied:
        lock.acquire({"batch": "s1"})
    try:
        asyncio.run(scenario())
    finally:
        lock.release()

    assert not list(tmp_path.rglob("*.zip"))
