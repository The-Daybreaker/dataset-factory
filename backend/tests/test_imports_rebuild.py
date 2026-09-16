"""重建导入记录：扫现状补记、旧记录保留、错误路径。"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from typing import cast

import httpx
import pytest

import dataset_factory.workdir.integrity as integrity_module
from dataset_factory.api import create_app
from dataset_factory.tasks import TaskCancelledError
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets
from dataset_factory.workdir.assets import registered_origins
from dataset_factory.workdir.errors import ImportInProgressError, WorkdirPathError
from dataset_factory.workdir.integrity import rebuild_import_records, scan_integrity


def test_rebuild_scans_current_files(tmp_path: Path, temp_data_root: Path) -> None:
    """工作目录里所有白名单素材（含未在册的）都进重建后的记录。"""
    asset = tmp_path / "a.png"
    asset.write_bytes(b"current")
    import_assets(tmp_path)
    (tmp_path / "b.jpg").write_bytes(b"unregistered")
    store = WorkdirStore(tmp_path)
    before = store.read_import_records()

    origin = WorkdirRegistry.register(tmp_path)

    async def scenario() -> None:
        app = create_app(frontend_dir=tmp_path / "frontend")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/workdirs/{origin.id}/imports/rebuild")
            assert response.status_code == 202
            task_id = response.json()["task_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                task = (await client.get(f"/api/tasks/{task_id}")).json()
                if task["status"] == "succeeded":
                    return
                assert task["status"] not in {"failed", "cancelled"}
                await asyncio.sleep(0.01)
            raise AssertionError("重建任务超时")

    asyncio.run(scenario())

    assert len(store.read_import_records()) == len(before) + 1
    rebuilt = store.read_import_records()[-1]
    assert rebuilt["source"] == ""
    files = cast(list[dict[str, str]], rebuilt["files"])
    assert [f["name"] for f in files] == ["a.png", "b.jpg"]


def test_rebuild_resets_missing_inventory_and_preserves_history(tmp_path: Path) -> None:
    """重建快照重置有效登记集合，原始历史字节仍是记录文件前缀。"""
    store = WorkdirStore(tmp_path)
    store.append_import_record(
        {
            "imported_at": "now",
            "source": "original",
            "files": [{"name": "missing.png", "sha256": "old"}],
        }
    )
    before = store.imports_file.read_bytes()
    (tmp_path / "present.jpg").write_bytes(b"present")

    report = rebuild_import_records(tmp_path)

    assert report["file_count"] == 1
    assert store.imports_file.read_bytes().startswith(before)
    assert list(registered_origins(store)) == ["present.jpg"]
    assert scan_integrity(tmp_path, {})[0].status == "unknown"


def test_cancelled_rebuild_does_not_append(tmp_path: Path) -> None:
    """取消发生在提交记录前时，不修改有效登记集合。"""
    store = WorkdirStore(tmp_path)
    stop = Event()
    stop.set()

    with pytest.raises(TaskCancelledError):
        rebuild_import_records(tmp_path, should_stop=stop)

    assert not store.imports_file.exists()


def test_rebuild_rejects_ambiguous_stems(tmp_path: Path) -> None:
    """同主干不同扩展名不会被重建静默吞并。"""
    (tmp_path / "a.png").write_bytes(b"first")
    (tmp_path / "a.jpg").write_bytes(b"second")

    with pytest.raises(WorkdirPathError, match="主干冲突"):
        rebuild_import_records(tmp_path)

    assert not WorkdirStore(tmp_path).imports_file.exists()


def test_import_after_rebuild_extends_current_inventory(tmp_path: Path) -> None:
    """重建后的普通追加导入仍可扩充当前登记集合。"""
    (tmp_path / "a.png").write_bytes(b"first")
    rebuild_import_records(tmp_path)
    store = WorkdirStore(tmp_path)

    store.append_import_record(
        {
            "imported_at": "later",
            "source": "new",
            "files": [{"name": "b.jpg", "sha256": "new"}],
        }
    )

    assert list(registered_origins(store)) == ["a.png", "b.jpg"]


def test_rebuild_ignores_unsupported_and_oversized_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重建同样遵守扩展名与大小护栏，子目录不递归登记。"""
    (tmp_path / "small.png").write_bytes(b"ok")
    (tmp_path / "large.mp4").write_bytes(b"too-large")
    (tmp_path / "note.txt").write_text("caption", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "hidden.png").write_bytes(b"ok")

    def limit(suffix: str) -> int:
        return 4

    monkeypatch.setattr(integrity_module, "size_limit", limit)

    report = rebuild_import_records(tmp_path)

    assert report["file_count"] == 1
    assert list(registered_origins(WorkdirStore(tmp_path))) == ["small.png"]


def test_rebuild_missing_directory_does_not_recreate_it(tmp_path: Path) -> None:
    """目录失效时报告路径错误，不通过门面构造器重建空目录。"""
    missing = tmp_path / "missing"

    with pytest.raises(WorkdirPathError):
        rebuild_import_records(missing)

    assert not missing.exists()


@pytest.mark.parametrize("tail", [b'{"unfinished":', b'{"name":"\xe4\xb8'])
def test_rebuild_recovers_incomplete_import_tail(tmp_path: Path, tail: bytes) -> None:
    """崩溃留下半行或半个 UTF-8 字符后，重建保留完整历史并可正常回读。"""
    store = WorkdirStore(tmp_path)
    store.append_import_record({"imported_at": "old", "source": "old", "files": []})
    history = store.imports_file.read_bytes()
    store.imports_file.write_bytes(history + tail)
    (tmp_path / "a.png").write_bytes(b"new")

    rebuild_import_records(tmp_path)

    assert store.imports_file.read_bytes().startswith(history)
    assert len(store.read_import_records()) == 2
    assert list(registered_origins(store)) == ["a.png"]


def test_rebuild_read_failure_preserves_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描中读取失败不会追加半份重建快照，也不会丢掉原登记集合。"""
    (tmp_path / "a.png").write_bytes(b"original")
    import_assets(tmp_path)
    store = WorkdirStore(tmp_path)
    before = store.imports_file.read_bytes()

    def fail_hash(path: Path) -> str:
        raise OSError("读取失败")

    monkeypatch.setattr(integrity_module, "hash_file", fail_hash)

    with pytest.raises(OSError, match="读取失败"):
        rebuild_import_records(tmp_path)

    assert store.imports_file.read_bytes() == before


def test_rebuild_cancelled_during_scan_preserves_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最后一个文件哈希完成时取消，仍不会提交重建快照。"""
    (tmp_path / "a.png").write_bytes(b"original")
    import_assets(tmp_path)
    store = WorkdirStore(tmp_path)
    before = store.imports_file.read_bytes()
    stop = Event()
    original_hash = integrity_module.hash_file

    def cancel_after_hash(path: Path) -> str:
        result = original_hash(path)
        stop.set()
        return result

    monkeypatch.setattr(integrity_module, "hash_file", cancel_after_hash)

    with pytest.raises(TaskCancelledError):
        rebuild_import_records(tmp_path, should_stop=stop)

    assert store.imports_file.read_bytes() == before


def test_import_and_rebuild_share_cross_process_lock(tmp_path: Path) -> None:
    """另一进程持导入锁时，两种写者都拒绝；进程退出后均可继续。"""
    script = (
        "import sys; from pathlib import Path; "
        "from dataset_factory.workdir.store import WorkdirStore; "
        "from dataset_factory.workdir.locks import import_guard; "
        "guard = import_guard(WorkdirStore(Path(sys.argv[1])).dsf_path); "
        "guard.__enter__(); Path(sys.argv[2]).touch(); sys.stdin.readline()"
    )
    ready = tmp_path / "lock-ready"
    process = subprocess.Popen(  # noqa: S603 - 固定脚本与测试临时路径，无外部命令输入
        [sys.executable, "-c", script, str(tmp_path), str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None, "持锁子进程提前退出"
            time.sleep(0.01)
        assert ready.exists(), "持锁子进程启动超时"

        with pytest.raises(ImportInProgressError):
            import_assets(tmp_path)
        with pytest.raises(ImportInProgressError):
            rebuild_import_records(tmp_path)
    finally:
        try:
            process.communicate("\n", timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)

    assert process.returncode == 0
    assert rebuild_import_records(tmp_path)["file_count"] == 0
