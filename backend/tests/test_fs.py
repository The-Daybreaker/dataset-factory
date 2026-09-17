"""单元测试：跨模块共享的底层存储工具 _fs（数据根定位 + 原子写）。

_fs 被 llm / prompts / skills / sessions 复用，这里直接测它本身的契约（不借道任何数据域），
让「共享工具正确」这件事有独立的钉子，而不是只靠某一个消费方的测试间接覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dataset_factory._fs import atomic_write_text, data_root


def test_data_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设 DATASET_FACTORY_HOME 时，数据根 = ~/.dataset_factory。"""
    monkeypatch.delenv("DATASET_FACTORY_HOME", raising=False)
    assert data_root() == Path.home() / ".dataset_factory"


def test_data_root_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """设了 DATASET_FACTORY_HOME 就用它覆盖。"""
    monkeypatch.setenv("DATASET_FACTORY_HOME", str(tmp_path))
    assert data_root() == tmp_path


def test_atomic_write_text_creates_file(tmp_path: Path) -> None:
    """正常写：目标文件落地、内容一致，且不留临时文件。"""
    target = tmp_path / "out.txt"

    atomic_write_text(target, "hello 中文")

    assert target.read_text(encoding="utf-8") == "hello 中文"
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_atomic_write_text_overwrites_existing(tmp_path: Path) -> None:
    """覆盖写：目标已存在时原子替换成新内容。"""
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_text_replace_failure_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改名失败（如磁盘满）→ 抛 OSError，且清掉临时文件、目标不产生。"""

    def _boom(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _boom)
    target = tmp_path / "out.txt"

    with pytest.raises(OSError, match="No space left"):
        atomic_write_text(target, "data")

    assert not target.exists()
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_atomic_write_text_unencodable_raises_and_cleans_up(tmp_path: Path) -> None:
    """内容含 UTF-8 无法编码的字符（孤立代理项）→ UnicodeEncodeError，且清掉临时文件、目标不产生。"""
    target = tmp_path / "out.txt"

    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "bad-" + chr(0xD800))

    assert not target.exists()
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_atomic_write_retries_transient_windows_file_occupation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    """短暂的 Windows 句柄占用结束后完整替换文件，不丢失本次写入。"""
    target = tmp_path / "run.json"
    target.write_text("old", encoding="utf-8")
    replace = os.replace
    attempts = 0

    def occupied_then_replace(src: Path, dst: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError("occupied")
            monkeypatch.setattr(error, "winerror", winerror, raising=False)
            raise error
        replace(src, dst)

    monkeypatch.setattr(os, "replace", occupied_then_replace)

    atomic_write_text(target, "new")

    assert attempts == 3
    assert target.read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json"]


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows 读取句柄的替换冲突")
def test_atomic_write_recovers_after_real_windows_reader_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实读取句柄挡住首次替换，关闭后重试成功且内容完整。"""
    target = tmp_path / "run.json"
    target.write_text("old", encoding="utf-8")
    replace = os.replace
    conflicts: list[int | None] = []

    with target.open("rb") as reader:

        def replace_after_reader_releases(src: Path, dst: Path) -> None:
            try:
                replace(src, dst)
            except PermissionError as exc:
                code: object = getattr(exc, "winerror", None)
                assert code is None or isinstance(code, int)
                conflicts.append(code)
                reader.close()
                raise

        monkeypatch.setattr(os, "replace", replace_after_reader_releases)

        atomic_write_text(target, "new")

    assert len(conflicts) == 1
    assert conflicts[0] in {5, 32, 33}
    assert target.read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json"]


@pytest.mark.parametrize("winerror", [5, None])
def test_atomic_write_permanent_denial_preserves_original_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int | None
) -> None:
    """永久拒绝访问有界失败，原件保持不变且临时文件清理完成。"""
    target = tmp_path / "run.json"
    target.write_text("old", encoding="utf-8")
    attempts = 0

    def deny_replace(src: Path, dst: Path) -> None:
        nonlocal attempts
        attempts += 1
        error = PermissionError("denied")
        if winerror is not None:
            monkeypatch.setattr(error, "winerror", winerror, raising=False)
        raise error

    monkeypatch.setattr(os, "replace", deny_replace)

    with pytest.raises(PermissionError, match="denied"):
        atomic_write_text(target, "new")

    assert attempts == (6 if winerror else 1)
    assert target.read_text(encoding="utf-8") == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json"]
