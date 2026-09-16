"""工作目录清理接口：预览与选择性清理的真实文件行为。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.workdir import WorkdirPathError, WorkdirRegistry, WorkdirStore
from dataset_factory.workdir.locks import RunLock, import_guard


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


@pytest.mark.parametrize("endpoint", ["cleanup", "cleanup-runs"])
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


@pytest.mark.parametrize("endpoint", ["cleanup", "cleanup-runs"])
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


def test_cleanup_rejects_import_in_progress(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """导入期间不能清理产物，以免恢复配对与清理竞争。"""
    store = WorkdirStore(tmp_path)
    entry = WorkdirRegistry.register(tmp_path)
    path = tmp_path / "s1__a.txt"
    path.write_bytes(b"caption")
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    with import_guard(store.dsf_path):
        result = client.post(
            f"/api/workdirs/{entry.id}/cleanup", json={"names": [path.name]}
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
