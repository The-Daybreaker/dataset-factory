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
