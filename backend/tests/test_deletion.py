"""目录删除的 HTTP、CLI、失败重试与并发保护。"""

import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dataset_factory.api import create_app
from dataset_factory.cli.main import app
from dataset_factory.cli.workdir import remove_workdir
from dataset_factory.workdir import WorkdirPathError, WorkdirRegistry, WorkdirStore
from dataset_factory.workdir.deletion import delete_workdir, preview_workdir_deletion
from dataset_factory.workdir.locks import RunLock, maintenance_guard

pytestmark = pytest.mark.usefixtures("temp_data_root")


class TerminalInput(io.StringIO):
    """提供真实确认文本并模拟终端属性，不替换确认函数。"""

    def isatty(self) -> bool:
        """此输入流代表交互终端。"""
        return True


def test_http_deletion_previews_original_materials_and_requires_matching_path(
    tmp_path: Path,
) -> None:
    """就地采用有重警告，错误确认不删，正确确认删除全目录并移除登记。"""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    store = WorkdirStore(root)
    store.append_import_record({"imported_at": "now", "source": str(root), "files": []})
    entry = WorkdirRegistry.register(root)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    preview = client.get(f"/api/workdirs/{entry.id}/delete-preview")
    wrong = client.request(
        "DELETE", f"/api/workdirs/{entry.id}", json={"confirmed_path": str(tmp_path)}
    )
    kept = (root / "a.jpg").read_bytes()
    deleted = client.request(
        "DELETE", f"/api/workdirs/{entry.id}", json={"confirmed_path": str(root)}
    )

    assert preview.status_code == 200
    assert preview.json()["original_materials"] is True
    assert "原始素材" in preview.json()["confirmation"]
    assert wrong.status_code == 400
    assert kept == b"image"
    assert deleted.json() == {"deleted": True, "remaining_path": None}
    assert not root.exists()
    assert WorkdirRegistry.list_all() == []
    with pytest.raises(WorkdirPathError):
        store.mutate_state(lambda state: state.update({"unexpected": True}))
    assert not root.exists()


def test_deletion_preview_waits_for_short_maintenance(tmp_path: Path) -> None:
    """删除预览等待并发状态读取结束，返回登记中的当前路径。"""
    root = tmp_path / "relocated"
    root.mkdir()
    entry = WorkdirRegistry.register(root)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))
    started = threading.Event()
    finished = threading.Event()
    responses: list[tuple[int, str | None]] = []

    def read_preview() -> None:
        started.set()
        try:
            response = client.get(f"/api/workdirs/{entry.id}/delete-preview")
            responses.append((response.status_code, response.json().get("path")))
        finally:
            finished.set()

    with maintenance_guard(root):
        reader = threading.Thread(target=read_preview)
        reader.start()
        assert started.wait(5)
        finished_early = finished.wait(0.1)
    reader.join(5)

    assert not reader.is_alive()
    assert not finished_early
    assert responses == [(200, str(root.resolve()))]


def test_failed_deletion_keeps_registration_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除失败保留位置与登记，过期写入拒绝，之后重试能完成。"""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    entry = WorkdirRegistry.register(root)

    def occupied(path: Path) -> None:
        raise PermissionError("occupied")

    with monkeypatch.context() as patch:
        patch.setattr("dataset_factory.workdir.deletion.shutil.rmtree", occupied)
        result = delete_workdir(entry.id, root)
    kept = WorkdirRegistry.get(entry.id)
    with pytest.raises(WorkdirPathError, match="删除"):
        WorkdirStore(root)
    retry = delete_workdir(entry.id, root)

    assert result.deleted is False
    assert result.remaining_path == kept.path
    assert retry.deleted is True
    assert not root.exists()


def test_running_workdir_delete_returns_409(tmp_path: Path) -> None:
    """持有运行锁时 HTTP 删除拒绝，文件与登记保留。"""
    root = tmp_path / "photos"
    root.mkdir()
    store = WorkdirStore(root)
    entry = WorkdirRegistry.register(root)
    lock = RunLock(store.dsf_path)
    lock.acquire({"pid": 123})
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))
    try:
        response = client.request(
            "DELETE", f"/api/workdirs/{entry.id}", json={"confirmed_path": str(root)}
        )
    finally:
        lock.release()

    assert response.status_code == 409
    assert root.exists()
    assert WorkdirRegistry.get(entry.id).id == entry.id


def test_partial_deletion_preview_retains_warning_and_cli_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导入历史已被部分删除后，预览仍保留原始素材警告且交互重试可完成。"""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    store = WorkdirStore(root)
    store.append_import_record({"imported_at": "now", "source": str(root), "files": []})
    entry = WorkdirRegistry.register(root)

    def partial_delete(path: Path) -> None:
        (path / ".dsf" / "imports.jsonl").unlink()
        raise PermissionError("occupied")

    with monkeypatch.context() as patch:
        patch.setattr("dataset_factory.workdir.deletion.shutil.rmtree", partial_delete)
        pending = delete_workdir(entry.id, root)
    preview = preview_workdir_deletion(entry.id)
    monkeypatch.setattr(sys, "stdin", TerminalInput(f"y\n{root}\n"))
    remove_workdir(root)

    assert pending.deleted is False
    assert preview.original_materials is True
    assert "原始素材" in preview.confirmation
    assert not root.exists()
    assert WorkdirRegistry.list_all() == []


@pytest.mark.parametrize("answer", ["n\n", "y\nwrong-path\n"])
def test_interactive_delete_rejects_decline_or_wrong_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """两级确认中的任一步被拒绝时，原始素材与登记均保持。"""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    entry = WorkdirRegistry.register(root)
    monkeypatch.setattr(sys, "stdin", TerminalInput(answer))

    with pytest.raises((typer.Abort, typer.Exit)):
        remove_workdir(root)

    assert (root / "a.jpg").read_bytes() == b"image"
    assert WorkdirRegistry.get(entry.id).path == str(root)


def test_cli_delete_requires_yes_outside_terminal(tmp_path: Path) -> None:
    """脚本无确认参数立即退出 2，有 --yes 才删除真实目录。"""
    root = tmp_path / "photos"
    root.mkdir()
    WorkdirRegistry.register(root)
    runner = CliRunner()

    refused = runner.invoke(app, ["workdir", "rm", str(root)])
    exists = root.exists()
    deleted = runner.invoke(app, ["workdir", "rm", str(root), "--yes"])

    assert refused.exit_code == 2
    assert "--yes" in refused.stderr
    assert exists
    assert deleted.exit_code == 0, deleted.output
    assert not root.exists()


def test_cli_subprocess_deletes_only_confirmed_workdir(tmp_path: Path) -> None:
    """真实 CLI 子进程先拒绝无确认命令，再删除目标并持久更新注册表。"""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    sibling = tmp_path / "keep.txt"
    sibling.write_bytes(b"keep")
    WorkdirRegistry.register(root)
    command = [
        sys.executable,
        "-c",
        "from dataset_factory.cli import app; app()",
        "workdir",
        "rm",
        str(root),
    ]

    refused = subprocess.run(  # noqa: S603 -- 固定解释器与测试生成的目录参数。
        command, input=b"", capture_output=True, timeout=60, check=False
    )
    kept = (root / "a.jpg").read_bytes()
    deleted = subprocess.run(  # noqa: S603 -- 固定解释器与测试生成的目录参数。
        [*command, "--yes"], input=b"", capture_output=True, timeout=60, check=False
    )

    assert refused.returncode == 2
    assert b"--yes" in refused.stderr
    assert kept == b"image"
    assert deleted.returncode == 0, deleted.stderr
    assert json.loads(deleted.stdout) == {"deleted": True, "remaining_path": None}
    assert not root.exists()
    assert WorkdirRegistry.list_all() == []
    assert sibling.read_bytes() == b"keep"
