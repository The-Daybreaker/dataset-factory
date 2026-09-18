"""整个工作目录的删除预览、确认与失败重试。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .._fs import atomic_write_text, data_root, hash_file
from .errors import WorkdirPathError
from .locks import (
    RunLock,
    StateLock,
    import_guard,
    maintenance_guard,
    maintenance_record,
)
from .store import WorkdirRegistry, WorkdirStore


@dataclass(frozen=True)
class DeletionPreview:
    """二次确认展示的实际删除范围。"""

    path: str
    file_count: int
    total_bytes: int
    original_materials: bool
    confirmation: str


@dataclass(frozen=True)
class DeletionResult:
    """删除成功后移除登记，失败仍保留登记与残留路径。"""

    deleted: bool
    remaining_path: str | None


def _files(root: Path) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}

    def fail(error: OSError) -> None:
        raise error

    for directory, folders, names in os.walk(root, onerror=fail, followlinks=False):
        for name in folders + names:
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                raise WorkdirPathError("工作目录包含链接，请检查后再删除。")
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root)
            if relative.parent == Path(".dsf") and name in (
                "run.lock",
                "imports.lock",
                "state.lock",
                "run-info.json",
            ):
                continue
            if not path.is_file():
                raise WorkdirPathError("工作目录包含非普通文件，请检查后再删除。")
            digest = hash_file(path)
            result[str(relative)] = (path.stat().st_size, digest)
    return result


def _root(wid: str) -> Path:
    path = Path(WorkdirRegistry.get(wid).path).absolute()
    if path.is_symlink() or path.is_junction() or path == Path(path.anchor):
        raise WorkdirPathError("不能删除文件系统根目录或目录链接。")
    root = path.resolve()
    if data_root().resolve().is_relative_to(root):
        raise WorkdirPathError("工作目录包含应用数据根，不能整体删除。")
    if any(
        entry.id != wid and Path(entry.path).resolve().is_relative_to(root)
        for entry in WorkdirRegistry.list_all()
    ):
        raise WorkdirPathError("目录内还有其他已登记的工作目录，请先单独处理。")
    return root


def _read_record(root: Path) -> dict[str, object]:
    path = maintenance_record(root)
    if not path.exists():
        return {}
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkdirPathError("维护记录无法读取，请检查后重试。") from exc
    if not isinstance(raw, dict):
        raise WorkdirPathError("维护记录格式不正确，请检查后重试。")
    return cast(dict[str, object], raw)


def _require_original_directory(root: Path, payload: dict[str, object]) -> None:
    if root.exists():
        info = root.stat()
        if (info.st_dev, info.st_ino) != (
            payload.get("source_device"),
            payload.get("source_inode"),
        ):
            raise WorkdirPathError("原路径已被其他目录替换，不能继续删除。")


def preview_workdir_deletion(wid: str) -> DeletionPreview:
    """列出整个目录的文件数量与大小，就地采用时明确标记原始素材风险。"""
    root = _root(wid)
    with maintenance_guard(root, timeout=10):
        if _root(wid) != root:
            raise WorkdirPathError("工作目录位置已变化，请刷新后重试。")
        previous = _read_record(root)
        if previous.get("operation") == "delete" and previous.get("wid") == wid:
            _require_original_directory(root, previous)
            original = previous.get("original_materials", True) is not False
            files = _files(root) if root.exists() else {}
        else:
            original = _original_materials(root)
            files = _files(root)
        return DeletionPreview(
            str(root),
            len(files),
            sum(value[0] for value in files.values()),
            original,
            "此工作目录即原始素材目录，删除后不可恢复。"
            if original
            else "将删除此工作目录内的全部素材、产物与记录，删除后不可恢复。",
        )


def _original_materials(root: Path) -> bool:
    if not root.is_dir():
        raise WorkdirPathError("工作目录不存在，请刷新后重试。")
    store = WorkdirStore(root)
    records = store.read_import_records()
    return any(
        isinstance(record.get("source"), str)
        and bool(record["source"])
        and Path(cast(str, record["source"])).resolve() == root
        for record in records
    )


def delete_workdir(wid: str, confirmed_path: Path) -> DeletionResult:
    """核对确认路径后删除整个目录；中途失败保留记录以便重试。

    运行、导入和状态写入均需退出后才能删除。删除记录位于数据根，目录内
    Windows 锁句柄释放后仍由维护锁拒绝新写者。
    """
    root = _root(wid)
    if confirmed_path.resolve() != root:
        raise WorkdirPathError("确认路径与当前工作目录不同，请重新查看删除范围。")
    with maintenance_guard(root):
        if _root(wid) != root:
            raise WorkdirPathError("工作目录位置已变化，请刷新后重试。")
        record_path = maintenance_record(root)
        previous = _read_record(root)
        retry = previous.get("operation") == "delete" and previous.get("wid") == wid
        if retry:
            payload = previous
            _require_original_directory(root, payload)
            if root.exists():
                expected = payload.get("files")
                if not isinstance(expected, dict):
                    raise WorkdirPathError("删除记录缺少文件清单，不能继续删除。")
                known = cast(dict[str, object], expected)
                for name, value in _files(root).items():
                    if known.get(name) != list(value):
                        raise WorkdirPathError(
                            "删除中断后目录新增或改写了文件，请检查。"
                        )
        else:
            if not root.is_dir():
                raise WorkdirPathError("工作目录不存在，请刷新后重试。")
            store = WorkdirStore(root)
            run = RunLock(store.dsf_path)
            state = StateLock(store.dsf_path)
            run.acquire({"operation": "delete", "pid": os.getpid()})
            try:
                with import_guard(store.dsf_path):
                    state.acquire()
                    try:
                        info = root.stat()
                        payload = {
                            "operation": "delete",
                            "wid": wid,
                            "source": str(root),
                            "status": "deleting",
                            "source_device": info.st_dev,
                            "source_inode": info.st_ino,
                            "original_materials": _original_materials(root),
                            "files": {
                                name: list(value)
                                for name, value in _files(root).items()
                            },
                        }
                        atomic_write_text(record_path, json.dumps(payload))
                    finally:
                        state.release()
            finally:
                run.release()
        try:
            if root.exists():
                shutil.rmtree(root)
        except OSError:
            return DeletionResult(False, str(root))
        payload["status"] = "cleaned"
        atomic_write_text(record_path, json.dumps(payload))
        WorkdirRegistry.remove(wid)
        return DeletionResult(True, None)
