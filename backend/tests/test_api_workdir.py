"""接口测试：workdir 端点（注册表读面 + 登记/导入的 202 任务面 + 导入历史 + 并发锁）。

读面与错误语义用 TestClient 直测；受理端点（202 + 任务）走 httpx ASGITransport——
任务受理需要运行中的事件循环（TestClient 门户线程没有），同一事件循环里受理 +
轮询到终态（test_api_tasks 同款模式）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.workdir import WorkdirRegistry

_PNG_BYTES = b"\x89PNG-fake-image-bytes"
_WAIT_TIMEOUT = 5.0


@pytest.fixture
def client(tmp_path: Path, temp_data_root: Path) -> TestClient:
    """挂临时空 frontend 目录的测试客户端（依赖 temp_data_root 隔离数据根）。"""
    return TestClient(create_app(frontend_dir=tmp_path))


def _write(directory: Path, name: str, content: bytes) -> None:
    """在目录里放一个文件（测试素材的统一写法）。"""
    (directory / name).write_bytes(content)


async def _wait_terminal(
    http: httpx.AsyncClient, task_id: str, timeout: float = _WAIT_TIMEOUT
) -> dict[str, Any]:
    """轮询任务直到终态（带超时护栏，绝不裸 sleep 赌调度）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body: dict[str, Any] = (await http.get(f"/api/tasks/{task_id}")).json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        await asyncio.sleep(0.01)
    pytest.fail("任务未在超时内到达终态")


def test_list_empty_registry_returns_empty_list(client: TestClient) -> None:
    """空注册表 → 空数组（不是 404——下拉数据源的空态）。"""
    response = client.get("/api/workdirs")

    assert response.status_code == 200
    assert response.json() == []


def test_selected_import_restore_and_in_place_registration(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """HTTP 选择性导入、缺失恢复与就地补登记均经任务返回真实结果。"""
    root = tmp_path / "work"
    source = tmp_path / "source"
    root.mkdir()
    source.mkdir()
    (source / "a.jpg").write_bytes(b"a")
    (source / "b.jpg").write_bytes(b"b")
    entry = WorkdirRegistry.register(root)

    async def scenario() -> None:
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            selected = await http.post(
                f"/api/workdirs/{entry.id}/imports",
                json={"source": str(source), "names": ["a.jpg"]},
            )
            imported = await _wait_terminal(http, selected.json()["task_id"])
            assert imported["status"] == "succeeded"
            assert (root / "a.jpg").read_bytes() == b"a"
            assert not (root / "b.jpg").exists()
            (root / "a.jpg").rename(tmp_path / "saved.jpg")
            restore = await http.post(
                f"/api/workdirs/{entry.id}/imports/reimport", json={"names": ["a.jpg"]}
            )
            restored = await _wait_terminal(http, restore.json()["task_id"])
            assert restored["status"] == "succeeded"
            assert (root / "a.jpg").read_bytes() == b"a"
            (root / "local.jpg").write_bytes(b"local")
            adopt = await http.post(
                f"/api/workdirs/{entry.id}/imports", json={"names": ["local.jpg"]}
            )
            adopted = await _wait_terminal(http, adopt.json()["task_id"])
            assert adopted["status"] == "succeeded"
            assert adopted["result"]["imported"] == ["local.jpg"]

    asyncio.run(scenario())


def test_list_returns_registered_entries(client: TestClient, tmp_path: Path) -> None:
    """登记后列表可见：字段齐全（id / path / title / last_used_at）。"""
    target = tmp_path / "photos"
    target.mkdir()
    entry = WorkdirRegistry.register(target, title="")

    response = client.get("/api/workdirs")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == entry.id
    assert body[0]["title"] == "photos"
    assert body[0]["path"] == str(target)
    assert isinstance(body[0]["last_used_at"], float)


def test_get_by_wid_returns_entry(client: TestClient, tmp_path: Path) -> None:
    """按 wid 查详情返回同一条目。"""
    target = tmp_path / "photos"
    target.mkdir()
    entry = WorkdirRegistry.register(target, title="我的图")

    response = client.get(f"/api/workdirs/{entry.id}")

    assert response.status_code == 200
    assert response.json()["title"] == "我的图"


def test_get_unknown_wid_returns_problem_json_404(client: TestClient) -> None:
    """未知 wid 404：problem+json 四件套（workdir-not-found）。"""
    response = client.get("/api/workdirs/no-such-wid")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "workdir-not-found"
    assert body["title"] == "工作目录不存在"
    assert body["status"] == 404
    assert "detail" in body


def test_get_imports_unknown_wid_returns_problem_json_404(
    client: TestClient,
) -> None:
    """导入历史端点同样按 wid 失效语义 404。"""
    response = client.get("/api/workdirs/no-such-wid/imports")

    assert response.status_code == 404
    assert response.json()["type"] == "workdir-not-found"


def test_post_workdir_rejects_invalid_path_problem_json(
    client: TestClient, tmp_path: Path
) -> None:
    """登记路径不存在 → 400 problem+json（workdir-path-invalid）。"""
    response = client.post(
        "/api/workdirs", json={"path": str(tmp_path / "no-such-dir")}
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "workdir-path-invalid"
    assert body["status"] == 400


def test_post_workdir_rejects_nested_source_problem_json(
    client: TestClient, tmp_path: Path
) -> None:
    """来源与工作目录相同 → 422 problem+json（import-source-conflict）。"""
    target = tmp_path / "photos"
    target.mkdir()

    response = client.post(
        "/api/workdirs",
        json={"path": str(target), "source": str(target)},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "import-source-conflict"
    assert body["title"] == "导入来源冲突"
    assert body["status"] == 422


def test_post_workdir_rejects_missing_source_problem_json(
    client: TestClient, tmp_path: Path
) -> None:
    """来源目录不存在 → 400 problem+json（workdir-path-invalid）。"""
    target = tmp_path / "photos"
    target.mkdir()

    response = client.post(
        "/api/workdirs",
        json={"path": str(target), "source": str(tmp_path / "nope")},
    )

    assert response.status_code == 400
    assert response.json()["type"] == "workdir-path-invalid"


def test_post_workdir_failed_validation_leaves_no_entry(
    client: TestClient, tmp_path: Path
) -> None:
    """校验失败（来源冲突）的请求不在注册表留痕。"""
    target = tmp_path / "photos"
    target.mkdir()

    client.post("/api/workdirs", json={"path": str(target), "source": str(target)})

    assert client.get("/api/workdirs").json() == []


def test_workdir_import_lifecycle(tmp_path: Path, temp_data_root: Path) -> None:
    """登记 → 202 受理 → 轮询终态 → 注册表与导入记录就位（就地采用全链路）。"""

    async def scenario() -> None:
        target = tmp_path / "photos"
        target.mkdir()
        app = create_app(frontend_dir=tmp_path)
        base = "http://testserver"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base
        ) as http:
            response = await http.post("/api/workdirs", json={"path": str(target)})

            assert response.status_code == 202
            assert response.headers["Retry-After"] == "2"
            body = response.json()
            assert body["workdir"]["path"] == str(target)
            assert body["workdir"]["title"] == "photos"

            finished = await _wait_terminal(http, body["task_id"])

            assert finished["status"] == "succeeded"
            assert finished["result"]["imported"] == []

            entries = (await http.get("/api/workdirs")).json()
            assert [entry["id"] for entry in entries] == [body["workdir"]["id"]]

            history = (
                await http.get(f"/api/workdirs/{body['workdir']['id']}/imports")
            ).json()
            assert len(history) == 1
            assert history[0]["source"] == str(target)
            assert history[0]["files"] == []

    asyncio.run(scenario())


def test_workdir_create_with_source_copies_files(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """带来源登记（复制导入）：任务完成后素材已复制、记录来源 = 原始目录。"""

    async def scenario() -> None:
        target = tmp_path / "photos"
        target.mkdir()
        source = tmp_path / "fresh"
        source.mkdir()
        _write(source, "cat_001.jpg", _PNG_BYTES)
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.post(
                "/api/workdirs",
                json={"path": str(target), "source": str(source)},
            )

            assert response.status_code == 202
            body = response.json()

            finished = await _wait_terminal(http, body["task_id"])

            assert finished["status"] == "succeeded"
            assert finished["result"]["imported"] == ["cat_001.jpg"]
            assert (target / "cat_001.jpg").read_bytes() == _PNG_BYTES
            assert (source / "cat_001.jpg").exists()

            history = (
                await http.get(f"/api/workdirs/{body['workdir']['id']}/imports")
            ).json()
            assert history[0]["source"] == str(source)
            assert history[0]["files"][0]["name"] == "cat_001.jpg"

    asyncio.run(scenario())


def test_workdir_create_reregister_reuses_wid(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """重复登记同一路径：wid 复用（幂等），两次任务各自成功。"""

    async def scenario() -> None:
        target = tmp_path / "photos"
        target.mkdir()
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            first = (
                await http.post("/api/workdirs", json={"path": str(target)})
            ).json()
            first_done = await _wait_terminal(http, first["task_id"])
            second = (
                await http.post(
                    "/api/workdirs", json={"path": str(target), "title": "新名"}
                )
            ).json()

            assert second["workdir"]["id"] == first["workdir"]["id"]
            assert second["workdir"]["title"] == "新名"

            second_done = await _wait_terminal(http, second["task_id"])

            assert first_done["status"] == "succeeded"
            assert second_done["status"] == "succeeded"
            assert len((await http.get("/api/workdirs")).json()) == 1

    asyncio.run(scenario())


def test_import_endpoint_appends_files_and_record(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """补充导入：202 受理 → 完成后素材复制、记录追加到历史末尾。"""

    async def scenario() -> None:
        target = tmp_path / "photos"
        target.mkdir()
        entry = WorkdirRegistry.register(target, title="")
        source = tmp_path / "more"
        source.mkdir()
        _write(source, "dog_001.jpg", _PNG_BYTES)
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.post(
                f"/api/workdirs/{entry.id}/imports", json={"source": str(source)}
            )

            assert response.status_code == 202
            assert response.headers["Retry-After"] == "2"
            body = response.json()

            finished = await _wait_terminal(http, body["task_id"])

            assert finished["status"] == "succeeded"
            assert finished["result"]["imported"] == ["dog_001.jpg"]
            assert (target / "dog_001.jpg").read_bytes() == _PNG_BYTES

            history = (await http.get(f"/api/workdirs/{entry.id}/imports")).json()
            assert len(history) == 1
            assert history[0]["source"] == str(source)
            assert history[0]["files"][0]["name"] == "dog_001.jpg"

    asyncio.run(scenario())


def test_import_endpoint_rejects_nested_source(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """补充导入来源 = 工作目录 → 422 problem+json（与登记同一条嵌套规则）。"""

    async def scenario() -> None:
        target = tmp_path / "photos"
        target.mkdir()
        entry = WorkdirRegistry.register(target, title="")
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.post(
                f"/api/workdirs/{entry.id}/imports", json={"source": str(target)}
            )

            assert response.status_code == 422
            assert response.json()["type"] == "import-source-conflict"

    asyncio.run(scenario())


def test_import_endpoint_unknown_wid_returns_problem_json(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """补充导入对未知 wid → 404 problem+json。"""

    async def scenario() -> None:
        source = tmp_path / "more"
        source.mkdir()
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.post(
                "/api/workdirs/no-such-wid/imports", json={"source": str(source)}
            )

            assert response.status_code == 404
            assert response.json()["type"] == "workdir-not-found"

    asyncio.run(scenario())


def test_concurrent_import_on_same_workdir_returns_409(
    tmp_path: Path, temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一工作目录同时只允许一个导入任务：占用中再受理 → 409 problem+json。"""
    import dataset_factory.api.routes_workdir as routes_workdir_module

    started = threading.Event()
    gate = threading.Event()

    def slow_import(
        workdir: Path,
        source: Path | None,
        *,
        force_names: frozenset[str] | set[str] = frozenset(),
        names: set[str] | None = None,
        should_stop: threading.Event | None = None,
        progress: Any = None,
    ) -> dict[str, Any]:
        started.set()
        gate.wait(timeout=_WAIT_TIMEOUT)
        return {
            "imported_at": "",
            "source": "",
            "imported": [],
            "skipped_identical": [],
            "skipped_conflict": [],
            "skipped_duplicate": [],
            "rejected": [],
        }

    monkeypatch.setattr(routes_workdir_module, "import_assets", slow_import)

    async def scenario() -> None:
        target = tmp_path / "photos"
        target.mkdir()
        entry = WorkdirRegistry.register(target, title="")
        source = tmp_path / "more"
        source.mkdir()
        _write(source, "dog_001.jpg", _PNG_BYTES)
        app = create_app(frontend_dir=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            first = await http.post(
                f"/api/workdirs/{entry.id}/imports", json={"source": str(source)}
            )
            assert first.status_code == 202
            assert started.wait(timeout=_WAIT_TIMEOUT), "第一个任务未开始"

            second = await http.post(
                f"/api/workdirs/{entry.id}/imports", json={"source": str(source)}
            )
            assert second.status_code == 409
            assert second.headers["content-type"] == "application/problem+json"
            body = second.json()
            assert body["type"] == "import-in-progress"
            assert body["status"] == 409

            rebuild = await http.post(f"/api/workdirs/{entry.id}/imports/rebuild")
            assert rebuild.status_code == 409
            assert rebuild.json()["type"] == "import-in-progress"

            gate.set()
            finished = await _wait_terminal(http, first.json()["task_id"])
            assert finished["status"] == "succeeded"

            # 槽位已随任务结束释放：可以再次受理。
            third = await http.post(
                f"/api/workdirs/{entry.id}/imports", json={"source": str(source)}
            )
            assert third.status_code == 202

    asyncio.run(scenario())


def test_imports_history_empty_for_new_workdir(
    client: TestClient, tmp_path: Path
) -> None:
    """新登记（未导入过）的工作目录 → 空历史（不是 404）。"""
    target = tmp_path / "photos"
    target.mkdir()
    entry = WorkdirRegistry.register(target, title="")

    response = client.get(f"/api/workdirs/{entry.id}/imports")

    assert response.status_code == 200
    assert response.json() == []
