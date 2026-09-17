"""服务器目录选择器接口的只读边界与失败反馈。"""

import asyncio
import platform
import socket
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app, routes_filesystem
from dataset_factory.workdir import WorkdirRegistry
from dataset_factory.workdir.locks import RunLock

pytestmark = pytest.mark.usefixtures("temp_data_root")


def test_default_location_is_server_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """省略路径时使用服务端主目录，不依赖浏览器或进程工作目录。"""
    home = tmp_path / "home"
    home.mkdir()

    def server_home() -> Path:
        return home

    monkeypatch.setattr(Path, "home", server_home)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.get("/api/filesystem")

    assert response.status_code == 200
    assert response.json()["path"] == str(home.resolve())
    assert response.json()["entries"] == []


def test_disappearing_entry_is_reported_in_unavailable_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """枚举后消失的条目计入不可读取数，而不是当作成功的空目录。"""

    class VanishedEntry:
        name = "vanished.txt"

        def stat(self) -> None:
            raise FileNotFoundError("gone")

    @contextmanager
    def scanner(path: object) -> Generator[Iterator[VanishedEntry]]:
        yield iter([VanishedEntry()])

    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))
    monkeypatch.setattr(routes_filesystem.os, "scandir", scanner)

    response = client.get(
        "/api/filesystem", params={"path": str(tmp_path), "show_files": True}
    )

    assert response.status_code == 200
    assert response.json()["unavailable_count"] == 1
    assert response.json()["entries"] == []


def test_lists_directories_first_and_never_returns_content(tmp_path: Path) -> None:
    """只返回当前层元信息，目录优先排序，不读取正文或递归子目录。"""
    tmp_path = tmp_path / "browse"
    tmp_path.mkdir()
    (tmp_path / "z-dir").mkdir()
    (tmp_path / "z-dir" / "nested.txt").write_text("nested")
    (tmp_path / "A.txt").write_text("private contents")
    (tmp_path / ".hidden").mkdir()
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.get(
        "/api/filesystem", params={"path": str(tmp_path), "show_files": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert [entry["name"] for entry in body["entries"]] == ["z-dir", "A.txt"]
    assert body["entries"][0]["size"] is None
    assert body["entries"][1]["size"] == 16
    assert body["hostname"] == socket.gethostname()
    assert body["system"] == platform.system()
    assert body["path"] == str(tmp_path.resolve())
    assert body["parent"] == str(tmp_path.parent.resolve())
    assert "private contents" not in response.text
    assert "nested.txt" not in response.text


def test_defaults_hide_files_and_dot_directories(tmp_path: Path) -> None:
    """默认仅显示非隐藏目录；显式参数才显示点开头目录。"""
    tmp_path = tmp_path / "browse"
    tmp_path.mkdir()
    (tmp_path / "directory").mkdir()
    (tmp_path / ".private").mkdir()
    (tmp_path / "file.txt").write_text("text")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    default = client.get("/api/filesystem", params={"path": str(tmp_path)})
    hidden = client.get(
        "/api/filesystem", params={"path": str(tmp_path), "show_hidden": True}
    )

    assert [entry["name"] for entry in default.json()["entries"]] == ["directory"]
    assert [entry["name"] for entry in hidden.json()["entries"]] == [
        ".private",
        "directory",
    ]


def test_suffix_filter_is_case_insensitive_and_keeps_directories(
    tmp_path: Path,
) -> None:
    """技能过滤仅展示 md 和 txt 文件，仍能浏览包目录。"""
    tmp_path = tmp_path / "browse"
    tmp_path.mkdir()
    (tmp_path / "package").mkdir()
    for name in ("SKILL.MD", "notes.txt", "picture.png"):
        (tmp_path / name).write_text("text")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.get(
        "/api/filesystem",
        params={
            "path": str(tmp_path),
            "show_files": "true",
            "suffixes": [".md", ".txt"],
        },
    )

    assert [entry["name"] for entry in response.json()["entries"]] == [
        "package",
        "notes.txt",
        "SKILL.MD",
    ]


@pytest.mark.parametrize(
    ("kind", "status_code"),
    [("missing", 404), ("file", 400), ("relative", 400), ("nul", 400)],
)
def test_invalid_paths_return_actionable_problems(
    tmp_path: Path, kind: str, status_code: int
) -> None:
    """非法路径与消失目录按类型返回问题详情，不伪装成空目录。"""
    file = tmp_path / "file.txt"
    file.write_text("data")
    paths = {
        "missing": str(tmp_path / "absent"),
        "file": str(file),
        "relative": "relative",
        "nul": str(tmp_path) + "\0",
    }
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.get("/api/filesystem", params={"path": paths[kind]})

    assert response.status_code == status_code
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"]


def test_permission_error_is_not_an_empty_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描无权限时返回 403，避免把不可访问误认为没有文件。"""
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    def denied(path: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(routes_filesystem.os, "scandir", denied)
    response = client.get("/api/filesystem", params={"path": str(tmp_path)})

    assert response.status_code == 403
    assert response.json()["type"].endswith("filesystem-permission-denied")


@pytest.mark.parametrize("registered", [False, True])
def test_rename_directory_returns_the_new_server_path(
    tmp_path: Path, registered: bool
) -> None:
    """改名任务保留内容，登记目录的稳定身份与地址同步更新。"""
    source = tmp_path / "old-name"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"original")
    entry = WorkdirRegistry.register(source) if registered else None

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(frontend_dir=tmp_path)),
            base_url="http://testserver",
        ) as http:
            response = await http.post(
                "/api/filesystem/rename",
                json={"path": str(source), "new_name": "new-name"},
            )
            assert response.status_code == 202
            task_id = response.json()["task_id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                task = (await http.get(f"/api/tasks/{task_id}")).json()
                if task["status"] != "running":
                    assert task["status"] == "succeeded", task
                    assert task["result"]["path"] == str(tmp_path / "new-name")
                    return
                await asyncio.sleep(0.01)
            pytest.fail("改名任务超时")

    asyncio.run(scenario())

    assert (tmp_path / "new-name" / "image.jpg").read_bytes() == b"original"
    assert not source.exists()
    if entry:
        assert WorkdirRegistry.get(entry.id).path == str(tmp_path / "new-name")


@pytest.mark.parametrize("name", ["..", "../other", "a\\b", "C:other", " ", "x."])
def test_rename_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    """路径片段与跨平台歧义名称均不能改名或移动源目录。"""
    source = tmp_path / "source"
    source.mkdir()
    client = TestClient(create_app(frontend_dir=tmp_path))

    response = client.post(
        "/api/filesystem/rename", json={"path": str(source), "new_name": name}
    )

    assert response.status_code == 400
    assert source.is_dir()


@pytest.mark.parametrize("inside", [False, True])
def test_rename_rejects_workdir_ancestor_or_child(tmp_path: Path, inside: bool) -> None:
    """普通改名不能绕过工作目录登记与素材记录的维护边界。"""
    workdir = tmp_path / "workdir"
    child = workdir / "child"
    child.mkdir(parents=True)
    WorkdirRegistry.register(workdir)
    source = child if inside else tmp_path
    client = TestClient(create_app(frontend_dir=tmp_path))

    response = client.post(
        "/api/filesystem/rename", json={"path": str(source), "new_name": "renamed"}
    )

    assert response.status_code == 400
    assert source.is_dir()


def test_create_directory_preserves_existing_content(tmp_path: Path) -> None:
    """创建一层目录返回绝对路径，重复提交拒绝覆盖既有内容。"""
    client = TestClient(create_app(frontend_dir=tmp_path))
    payload = {"parent": str(tmp_path), "name": "new-parent"}

    created = client.post("/api/filesystem/directories", json=payload)
    target = tmp_path / "new-parent"
    (target / "keep.txt").write_text("keep")
    conflict = client.post("/api/filesystem/directories", json=payload)

    assert created.status_code == 201
    assert created.json()["path"] == str(target)
    assert conflict.status_code == 400
    assert (target / "keep.txt").read_text() == "keep"


async def _rename_terminal(source: Path) -> dict[str, object]:
    """受理真实改名任务并等待终态，超时即失败。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(frontend_dir=source.parent)),
        base_url="http://testserver",
    ) as http:
        response = await http.post(
            "/api/filesystem/rename", json={"path": str(source), "new_name": "renamed"}
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            task: dict[str, object] = (await http.get(f"/api/tasks/{task_id}")).json()
            if task["status"] != "running":
                return task
            await asyncio.sleep(0.01)
        pytest.fail("改名任务超时")


def test_rename_rejects_directory_replaced_after_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受理后同路径被换成另一目录时拒绝执行，两份目录内容均保留。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "original.txt").write_text("original")
    original_guard = routes_filesystem.maintenance_guard
    calls = 0

    @contextmanager
    def replace_before_execution(workdir: Path) -> Generator[None]:
        nonlocal calls
        calls += 1
        if calls == 1:
            source.rename(tmp_path / "saved")
            source.mkdir()
            (source / "replacement.txt").write_text("replacement")
        with original_guard(workdir):
            yield

    monkeypatch.setattr(
        routes_filesystem, "maintenance_guard", replace_before_execution
    )

    task = asyncio.run(_rename_terminal(source))

    assert task["status"] == "failed"
    assert (source / "replacement.txt").read_text() == "replacement"
    assert (tmp_path / "saved" / "original.txt").read_text() == "original"
    assert not (tmp_path / "renamed").exists()


def test_rename_running_workdir_preserves_registry_and_content(tmp_path: Path) -> None:
    """运行锁占用时改名任务失败，登记地址与素材保持原样。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"original")
    entry = WorkdirRegistry.register(source)
    lock = RunLock(source / ".dsf")
    lock.acquire({"operation": "test"})

    try:
        task = asyncio.run(_rename_terminal(source))
    finally:
        lock.release()

    assert task["status"] == "failed"
    assert WorkdirRegistry.get(entry.id).path == str(source)
    assert (source / "image.jpg").read_bytes() == b"original"
    assert not (tmp_path / "renamed").exists()


def test_rename_name_conflict_preserves_both_directories(tmp_path: Path) -> None:
    """同名目标已存在时拒绝改名，两边文件均不覆盖。"""
    source = tmp_path / "source"
    destination = tmp_path / "renamed"
    source.mkdir()
    destination.mkdir()
    (source / "keep.txt").write_text("source")
    (destination / "keep.txt").write_text("destination")
    client = TestClient(create_app(frontend_dir=tmp_path))

    response = client.post(
        "/api/filesystem/rename", json={"path": str(source), "new_name": "renamed"}
    )

    assert response.status_code == 400
    assert (source / "keep.txt").read_text() == "source"
    assert (destination / "keep.txt").read_text() == "destination"


@pytest.mark.parametrize(
    ("system", "session", "display", "available", "expected"),
    [
        ("Windows", "Console", "", True, True),
        ("Windows", "Services", "", True, False),
        ("Darwin", "", "", True, True),
        ("Linux", "", ":0", True, True),
        ("Linux", "", "", True, False),
        ("Linux", "", ":0", False, False),
    ],
)
def test_file_manager_capability_comes_from_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    session: str,
    display: str,
    available: bool,
    expected: bool,
) -> None:
    """桌面会话与可执行程序共同决定系统打开能力，不使用浏览器平台。"""
    monkeypatch.setattr(routes_filesystem.platform, "system", lambda: system)
    monkeypatch.setenv("SESSIONNAME", session)
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setattr(
        routes_filesystem.shutil,
        "which",
        Mock(return_value="manager" if available else None),
    )
    client = TestClient(create_app(frontend_dir=tmp_path))

    response = client.get("/api/filesystem/capabilities")

    assert response.json() == {"open_in_file_manager": expected}


def test_open_directory_passes_path_as_single_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只将目录作为独立进程参数，不把路径作为 shell 命令解析。"""
    target = tmp_path / "space and text"
    target.mkdir()
    launch = Mock()
    monkeypatch.setattr(routes_filesystem, "_file_manager_command", lambda: ["manager"])
    monkeypatch.setattr(routes_filesystem.subprocess, "Popen", launch)
    client = TestClient(create_app(frontend_dir=tmp_path))

    response = client.post("/api/filesystem/open", json={"path": str(target)})

    assert response.status_code == 204
    assert launch.call_args.args == (["manager", str(target)],)
    assert "shell" not in launch.call_args.kwargs
