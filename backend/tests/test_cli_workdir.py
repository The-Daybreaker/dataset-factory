"""工作目录命令的真实磁盘操作、结构化输出与取消行为。"""

import json
import signal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dataset_factory.cli.main import app
from dataset_factory.cli.operations import cancellation
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore

pytestmark = pytest.mark.usefixtures("temp_data_root")
runner = CliRunner()


def test_copy_add_show_list_and_duplicate_feedback(tmp_path: Path) -> None:
    """复制导入保留来源，重复反馈可作为 JSON 读取且进度不混入正文。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"image")
    root = tmp_path / "work"
    root.mkdir()

    added = runner.invoke(app, ["workdir", "add", str(root), "--source", str(source)])
    shown = runner.invoke(app, ["workdir", "show", str(root)])
    listed = runner.invoke(app, ["workdir", "list"])
    duplicate = runner.invoke(app, ["workdir", "import", str(root), str(source)])

    assert added.exit_code == 0, added.stderr
    assert json.loads(added.stdout)["import"]["imported"] == ["a.jpg"]
    assert "100%" in added.stderr
    assert shown.exit_code == listed.exit_code == duplicate.exit_code == 0
    assert json.loads(shown.stdout)["imports"][0]["source"] == str(source)
    assert json.loads(listed.stdout)[0]["path"] == str(root)
    assert json.loads(duplicate.stdout)["skipped_identical"] == ["a.jpg"]
    assert (source / "a.jpg").read_bytes() == (root / "a.jpg").read_bytes()


def test_adopt_requires_confirmation_without_registering(tmp_path: Path) -> None:
    """非交互就地采用缺确认不登记，显式确认后来源为目录自身。"""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")

    refused = runner.invoke(app, ["workdir", "add", str(root)])
    before = WorkdirRegistry.list_all()
    accepted = runner.invoke(app, ["workdir", "add", str(root), "--yes"])

    assert refused.exit_code == 2
    assert before == []
    assert accepted.exit_code == 0, accepted.stderr
    assert json.loads(accepted.stdout)["import"]["source"] == str(root)


def test_noninteractive_blank_batch_name_is_rejected(tmp_path: Path) -> None:
    """显式空名不从零建批，避免策略名称校验前写入半份状态。"""
    root = tmp_path / "work"
    root.mkdir()
    WorkdirRegistry.register(root)

    result = runner.invoke(
        app,
        [
            "batch",
            "add",
            str(root),
            "--name",
            "   ",
            "--endpoint",
            "main",
            "--prompt",
            "详细描述",
        ],
    )

    assert result.exit_code == 2
    assert "名称" in result.stderr


def test_invalid_source_does_not_register_workdir(tmp_path: Path) -> None:
    """来源无效时在登记前报业务错误，不留下半份登记。"""
    root = tmp_path / "work"
    root.mkdir()

    result = runner.invoke(app, ["workdir", "add", str(root), "--source", str(root)])

    assert result.exit_code == 1
    assert "Traceback" not in result.stderr
    assert WorkdirRegistry.list_all() == []


def test_duplicate_new_name_can_be_explicitly_imported(tmp_path: Path) -> None:
    """异名同容先返回跳过报告，显式点名后才复制新名字。"""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    runner.invoke(app, ["workdir", "add", str(root), "--yes"])
    source = tmp_path / "source"
    source.mkdir()
    (source / "b.jpg").write_bytes(b"image")
    command = ["workdir", "import", str(root), str(source)]

    skipped = runner.invoke(app, command)
    absent = not (root / "b.jpg").exists()
    forced = runner.invoke(app, [*command, "--force-name", "b.jpg"])

    assert skipped.exit_code == forced.exit_code == 0
    assert json.loads(skipped.stdout)["skipped_duplicate"][0]["name"] == "b.jpg"
    assert absent
    assert json.loads(forced.stdout)["imported"] == ["b.jpg"]


def test_cleanup_defaults_to_preview_and_only_deletes_selection(tmp_path: Path) -> None:
    """预览与缺确认不删文件，显式选择只清指定孤立产物。"""
    root = tmp_path / "work"
    root.mkdir()
    WorkdirRegistry.register(root)
    for name in ("s1__a.txt", "s1__b.txt"):
        (root / name).write_text("caption", encoding="utf-8")
    command = ["workdir", "cleanup", str(root)]

    preview = runner.invoke(app, command)
    refused = runner.invoke(app, [*command, "--name", "s1__a.txt"])
    kept = (root / "s1__a.txt").exists()
    cleaned = runner.invoke(app, [*command, "--name", "s1__a.txt", "--yes"])

    assert preview.exit_code == 0
    assert len(json.loads(preview.stdout)) == 2
    assert refused.exit_code == 2
    assert kept
    assert cleaned.exit_code == 0, cleaned.stderr
    assert json.loads(cleaned.stdout)["count"] == 1
    assert not (root / "s1__a.txt").exists()
    assert (root / "s1__b.txt").exists()


def test_rebuild_and_relocate_preserve_identity_and_materials(tmp_path: Path) -> None:
    """重建记录后搬迁保持登记 ID 与素材字节，旧路径不再可用。"""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image")
    entry = WorkdirRegistry.register(root)
    destination = tmp_path / "moved"

    rebuilt = runner.invoke(app, ["workdir", "rebuild-imports", str(root), "--yes"])
    moved = runner.invoke(
        app, ["workdir", "relocate", str(root), str(destination), "--yes"]
    )
    verified = runner.invoke(app, ["workdir", "verify", str(destination)])

    assert rebuilt.exit_code == moved.exit_code == verified.exit_code == 0, moved.stderr
    assert json.loads(rebuilt.stdout)["file_count"] == 1
    assert json.loads(moved.stdout)["cleanup_pending"] is False
    assert WorkdirRegistry.get(entry.id).path == str(destination)
    assert not root.exists()
    assert (destination / "a.jpg").read_bytes() == b"image"
    assert WorkdirStore(destination).read_import_records()[-1]["source"] == ""


def test_cancellation_restores_signal_handler_after_repeated_interrupts() -> None:
    """重复 Ctrl-C 只置停止事件，退出上下文后恢复原处理器。"""
    previous = signal.getsignal(signal.SIGINT)

    with cancellation() as stop:
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
        stopped = stop.is_set()

    assert stopped
    assert signal.getsignal(signal.SIGINT) == previous
