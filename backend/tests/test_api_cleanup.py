"""工作目录清理接口：预览与选择性清理的真实文件行为。"""

import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.workdir import WorkdirPathError, WorkdirRegistry, WorkdirStore
from dataset_factory.workdir.cleanup import remove_unimported
from dataset_factory.workdir.locks import RunLock, import_guard, maintenance_record
from dataset_factory.workdir.relocation import relocate_workdir

pytestmark = pytest.mark.usefixtures("temp_data_root")


def test_remove_unimported_preserves_exact_files_in_recovery(tmp_path: Path) -> None:
    """按完整名称移出一份文件，保留同主干文件、工具产物和恢复字节。"""
    store = WorkdirStore(tmp_path)
    for name in ("sample.png", "sample.jpg", "s1__sample.txt"):
        (tmp_path / name).write_bytes(name.encode())

    result = remove_unimported(tmp_path, ["sample.png", "sample.png"])

    assert result.count == 1
    assert result.recovery_path is not None
    assert (Path(result.recovery_path) / "sample.png").read_bytes() == b"sample.png"
    assert not (tmp_path / "sample.png").exists()
    assert (tmp_path / "sample.jpg").read_bytes() == b"sample.jpg"
    assert (tmp_path / "s1__sample.txt").read_bytes() == b"s1__sample.txt"
    assert store.read_import_records() == []


@pytest.mark.parametrize("name", ["registered.jpg", "s1__a.txt", "../outside", ""])
def test_remove_unimported_rejects_whole_stale_selection(
    tmp_path: Path, name: str
) -> None:
    """名单中混入在册、产物或非法名称时整单拒绝，不先移动有效项。"""
    store = WorkdirStore(tmp_path)
    for filename in ("fresh.png", "registered.jpg", "s1__a.txt"):
        (tmp_path / filename).write_bytes(filename.encode())
    store.append_import_record(
        {
            "source": str(tmp_path),
            "imported_at": "2026-09-17T00:00:00Z",
            "files": [
                {
                    "name": "registered.jpg",
                    "sha256": hashlib.sha256(b"registered.jpg").hexdigest(),
                }
            ],
        }
    )

    with pytest.raises(WorkdirPathError):
        remove_unimported(tmp_path, ["fresh.png", name])

    assert (tmp_path / "fresh.png").read_bytes() == b"fresh.png"
    assert (tmp_path / "registered.jpg").read_bytes() == b"registered.jpg"
    assert (tmp_path / "s1__a.txt").read_bytes() == b"s1__a.txt"


def test_http_remove_unimported_returns_recoverable_file(tmp_path: Path) -> None:
    """HTTP 精确移出未导入文件，返回可恢复位置，其他文件保持原样。"""
    WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    (tmp_path / "sample.png").write_bytes(b"selected")
    (tmp_path / "sample.jpg").write_bytes(b"keep")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.post(
        f"/api/workdirs/{entry.id}/unimported/remove", json={"names": ["sample.png"]}
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert not (tmp_path / "sample.png").exists()
    assert (tmp_path / "sample.jpg").read_bytes() == b"keep"
    recovery = Path(response.json()["recovery_path"])
    assert recovery.is_relative_to(tmp_path / ".dsf" / "trash")
    assert (recovery / "sample.png").read_bytes() == b"selected"


def test_http_retry_completes_partial_copy_after_app_restart(tmp_path: Path) -> None:
    """重启应用后经搬迁入口补全中断副本，校验全部字节后清理源目录。"""
    source = tmp_path / "source"
    source.mkdir()
    store = WorkdirStore(source)
    store.mutate_state(lambda state: state.update({"keep": "metadata"}))
    original = b"partial-marker-file-with-complete-content"
    (source / "marker.jpg").write_bytes(original)
    (source / "second.png").write_bytes(b"second complete file")
    (source / "empty").mkdir()
    entry = WorkdirRegistry.register(source)
    destination = tmp_path / "destination"
    with TestClient(create_app(frontend_dir=tmp_path / "frontend")) as first_app:
        assert first_app.get(f"/api/workdirs/{entry.id}").status_code == 200
        destination.mkdir()
        (destination / "marker.jpg").write_bytes(original[:7])
        source_info = source.stat()
        destination_info = destination.stat()
        maintenance_record(source).write_text(
            json.dumps(
                {
                    "wid": entry.id,
                    "source": str(source),
                    "destination": str(destination),
                    "status": "copying",
                    "source_device": source_info.st_dev,
                    "source_inode": source_info.st_ino,
                    "destination_device": destination_info.st_dev,
                    "destination_inode": destination_info.st_ino,
                }
            ),
            encoding="utf-8",
        )

    with TestClient(create_app(frontend_dir=tmp_path / "frontend")) as restarted_app:
        accepted = restarted_app.post(
            f"/api/workdirs/{entry.id}/relocate", json={"path": str(destination)}
        )
        assert accepted.status_code == 202, accepted.text
        task_url = f"/api/tasks/{accepted.json()['task_id']}"
        deadline = time.monotonic() + 10
        task = restarted_app.get(task_url).json()
        while task["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = restarted_app.get(task_url).json()
        current = restarted_app.get(f"/api/workdirs/{entry.id}")

    assert task["status"] == "succeeded", task
    assert task["result"]["cleanup_pending"] is False
    assert current.json()["id"] == entry.id
    assert Path(current.json()["path"]) == destination
    assert (destination / "marker.jpg").read_bytes() == original
    assert (destination / "second.png").read_bytes() == b"second complete file"
    assert (destination / "empty").is_dir()
    assert WorkdirStore(destination).read_state() == {"keep": "metadata"}
    assert not source.exists()


def test_relocation_task_moves_files_and_preserves_workdir_id(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """HTTP 搬迁受理后经任务轮询完成，注册表标识不变且旧位置自动清理。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"image")
    entry = WorkdirRegistry.register(source)
    destination = tmp_path / "destination"

    with TestClient(create_app(frontend_dir=tmp_path / "frontend")) as client:
        accepted = client.post(
            f"/api/workdirs/{entry.id}/relocate", json={"path": str(destination)}
        )
        assert accepted.status_code == 202
        assert accepted.headers["retry-after"] == "2"
        task_url = f"/api/tasks/{accepted.json()['task_id']}"
        deadline = time.monotonic() + 10
        task = client.get(task_url).json()
        while task["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = client.get(task_url).json()
        current = client.get(f"/api/workdirs/{entry.id}")

    assert task["status"] == "succeeded", task
    assert task["result"]["cleanup_pending"] is False
    assert current.json()["id"] == entry.id
    assert Path(current.json()["path"]) == destination
    assert (destination / "a.jpg").read_bytes() == b"image"
    assert not source.exists()


def test_relocation_cleanup_rejects_unrecorded_directory(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """不能借旧位置清理接口删除未由搬迁记录证明的目录。"""
    source = tmp_path / "source"
    source.mkdir()
    entry = WorkdirRegistry.register(source)
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_bytes(b"keep")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.post(
        f"/api/workdirs/{entry.id}/relocate/cleanup", json={"old_path": str(other)}
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert (other / "keep.txt").read_bytes() == b"keep"


def test_http_relocation_retries_recorded_copy_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新应用能发现中断副本并重新搬迁，不将已记录目标误判为路径冲突。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"image")
    entry = WorkdirRegistry.register(source)
    destination = tmp_path / "destination"

    def interrupt(wid: str, new_path: Path) -> None:
        raise KeyboardInterrupt("interrupted before switch")

    with monkeypatch.context() as patch:
        patch.setattr(WorkdirRegistry, "update_path", interrupt)
        with pytest.raises(KeyboardInterrupt):
            relocate_workdir(entry.id, destination)

    with TestClient(create_app(frontend_dir=tmp_path / "frontend")) as client:
        status = client.get(f"/api/workdirs/{entry.id}/relocate/status")
        accepted = client.post(
            f"/api/workdirs/{entry.id}/relocate", json={"path": str(destination)}
        )
        assert accepted.status_code == 202, accepted.text
        task_url = f"/api/tasks/{accepted.json()['task_id']}"
        deadline = time.monotonic() + 10
        task = client.get(task_url).json()
        while task["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = client.get(task_url).json()
        after = client.get(f"/api/workdirs/{entry.id}/relocate/status")

    assert status.status_code == 200
    assert status.json() == [
        {
            "old_path": str(source),
            "path": str(destination),
            "status": "copy-retained",
        }
    ]
    assert task["status"] == "succeeded", task
    assert after.json() == []
    assert not source.exists()
    assert (destination / "a.jpg").read_bytes() == b"image"


def test_cleanup_preview_lists_only_orphan_products(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """预览只列无素材配对的工具产物，返回批次、文件名和真实字节数。"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    WorkdirStore(workdir)
    entry = WorkdirRegistry.register(workdir)
    (workdir / "paired.jpg").write_bytes(b"image")
    (workdir / "s1__paired.txt").write_text("paired", encoding="utf-8")
    (workdir / "s2__orphan.txt").write_bytes(b"orphan caption")
    (workdir / "notes.txt").write_bytes(b"keep")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.get(f"/api/workdirs/{entry.id}/cleanup-preview")

    assert response.status_code == 200
    assert response.json() == {
        "products": [{"batch": 2, "name": "s2__orphan.txt", "size": 14}],
        "total_bytes": 14,
    }
    assert (workdir / "s2__orphan.txt").read_bytes() == b"orphan caption"


def test_cleanup_only_deletes_selected_orphans(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """只清理选中的孤立产物，其他文件保留且不积累暂存副本。"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    WorkdirStore(workdir)
    entry = WorkdirRegistry.register(workdir)
    (workdir / "s1__first.txt").write_bytes(b"first")
    (workdir / "s2__second.txt").write_bytes(b"second")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.post(
        f"/api/workdirs/{entry.id}/cleanup", json={"names": ["s1__first.txt"]}
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert not (workdir / "s1__first.txt").exists()
    assert (workdir / "s2__second.txt").read_bytes() == b"second"
    assert response.json()["recovery_path"] is None
    assert not list((workdir / ".dsf/trash").iterdir())


def test_cleanup_rechecks_restored_pair_before_any_move(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """预览后恢复素材，含过期选择的整份请求被拒绝，不部分清理。"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    WorkdirStore(workdir)
    entry = WorkdirRegistry.register(workdir)
    for name in ("first", "second"):
        (workdir / f"s1__{name}.txt").write_bytes(name.encode())
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))
    client.get(f"/api/workdirs/{entry.id}/cleanup-preview")
    (workdir / "second.jpg").write_bytes(b"restored")

    response = client.post(
        f"/api/workdirs/{entry.id}/cleanup",
        json={"names": ["s1__first.txt", "s1__second.txt"]},
    )

    assert response.status_code == 400
    assert (workdir / "s1__first.txt").is_file()
    assert (workdir / "s1__second.txt").is_file()


def test_run_cleanup_preserves_unselected_records_and_products(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """整份清理所选损坏运行记录，其他运行和产物不受影响。"""
    store = WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    for name in ("first", "second"):
        directory = store.runs_dir / name
        directory.mkdir()
        (directory / "items.jsonl").write_bytes(b"broken")
    (tmp_path / "s1__a.txt").write_bytes(b"caption")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    preview = client.get(f"/api/workdirs/{entry.id}/cleanup-runs-preview")
    result = client.post(
        f"/api/workdirs/{entry.id}/cleanup-runs", json={"names": ["first"]}
    )

    assert preview.status_code == 200
    assert {row["name"]: row["size"] for row in preview.json()} == {
        "first": 6,
        "second": 6,
    }
    assert result.status_code == 200
    assert not (store.runs_dir / "first").exists()
    assert (store.runs_dir / "second/items.jsonl").read_bytes() == b"broken"
    assert result.json()["recovery_path"] is None
    assert (tmp_path / "s1__a.txt").read_bytes() == b"caption"


@pytest.mark.parametrize("endpoint", ["cleanup", "cleanup-runs", "unimported/remove"])
def test_cleanup_rejects_running_workdir(
    tmp_path: Path, temp_data_root: Path, endpoint: str
) -> None:
    """跑批占用时两种清理都返回 409，不改变文件。"""
    store = WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))
    lock = RunLock(store.dsf_path)
    lock.acquire({"batch": "s1"})
    try:
        response = client.post(
            f"/api/workdirs/{entry.id}/{endpoint}", json={"names": ["unused"]}
        )
    finally:
        lock.release()

    assert response.status_code == 409


@pytest.mark.parametrize("endpoint", ["cleanup", "cleanup-runs", "unimported/remove"])
@pytest.mark.parametrize("name", ["../outside", "..\\outside", "missing"])
def test_cleanup_invalid_selection_is_rejected(
    tmp_path: Path, temp_data_root: Path, endpoint: str, name: str
) -> None:
    """不在实时清单中的选择被拒绝，不能拼路径访问清单外内容。"""
    WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    result = client.post(f"/api/workdirs/{entry.id}/{endpoint}", json={"names": [name]})

    assert result.status_code == 400


@pytest.mark.parametrize("endpoint", ["cleanup", "unimported/remove"])
def test_cleanup_rejects_import_in_progress(
    tmp_path: Path, temp_data_root: Path, endpoint: str
) -> None:
    """导入期间不能清理产物，以免恢复配对与清理竞争。"""
    store = WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    path = tmp_path / "s1__a.txt"
    path.write_bytes(b"caption")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    with import_guard(store.dsf_path):
        result = client.post(
            f"/api/workdirs/{entry.id}/{endpoint}", json={"names": [path.name]}
        )

    assert result.status_code == 409
    assert path.read_bytes() == b"caption"


@pytest.mark.parametrize("recreate_source", [False, True])
def test_cleanup_move_failure_preserves_all_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recreate_source: bool
) -> None:
    """后续移动失败时回滚已移动项，原位置被占用也不能覆盖新文件。"""
    store = WorkdirStore(tmp_path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    rename = Path.rename

    def fail_second(source: Path, target: Path) -> Path:
        if source == second:
            if recreate_source:
                first.write_bytes(b"new")
            raise PermissionError("test move failure")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_second)

    with pytest.raises(WorkdirPathError):
        store.quarantine_paths([first, second])

    assert second.read_bytes() == b"second"
    assert first.read_bytes() == (b"new" if recreate_source else b"first")
    saved = list((store.dsf_path / "trash").rglob("first.txt"))
    assert len(saved) == int(recreate_source)
    if recreate_source:
        assert saved[0].read_bytes() == b"first"


@pytest.mark.parametrize("root_alias", [".", "child/.."])
def test_cleanup_rejects_root_before_moving(tmp_path: Path, root_alias: str) -> None:
    """调用清理门面时不能把整个工作目录当成一个清理条目。"""
    store = WorkdirStore(tmp_path)
    path = tmp_path / "keep.txt"
    path.write_bytes(b"keep")

    with pytest.raises(WorkdirPathError):
        store.quarantine_paths([path, tmp_path / root_alias])

    assert path.read_bytes() == b"keep"


def test_cleanup_delete_failure_returns_residual_location(
    tmp_path: Path, temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终删除失败时响应明确返回残留位置，数据仍可查找。"""
    store = WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    (tmp_path / "s1__a.txt").write_bytes(b"caption")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    def fail_delete(path: Path) -> None:
        raise PermissionError("test delete failure")

    with monkeypatch.context() as patch:
        patch.setattr("dataset_factory.workdir.store.shutil.rmtree", fail_delete)
        result = client.post(
            f"/api/workdirs/{entry.id}/cleanup", json={"names": ["s1__a.txt"]}
        )

    assert result.status_code == 200
    remaining = Path(result.json()["recovery_path"])
    assert remaining.is_relative_to(store.dsf_path)
    assert (remaining / "s1__a.txt").read_bytes() == b"caption"


@pytest.mark.parametrize(
    ("endpoint", "method"),
    [
        ("cleanup", "post"),
        ("cleanup-runs", "post"),
        ("cleanup-preview", "get"),
        ("cleanup-runs-preview", "get"),
    ],
)
def test_cleanup_problem_media_matches_contract(
    tmp_path: Path, temp_data_root: Path, endpoint: str, method: str
) -> None:
    """清理端点的错误响应媒体类型与 OpenAPI 契约一致。"""
    app = create_app(frontend_dir=tmp_path / "frontend")
    client = TestClient(app)

    result = client.request(
        method, f"/api/workdirs/missing/{endpoint}", json={"names": ["missing"]}
    )
    response = app.openapi()["paths"][f"/api/workdirs/{{wid}}/{endpoint}"][method][
        "responses"
    ]["404"]

    assert result.status_code == 404
    assert result.headers["content-type"] == "application/problem+json"
    assert "application/problem+json" in response["content"]
