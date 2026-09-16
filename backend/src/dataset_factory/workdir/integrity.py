"""素材完整性读模型：用实际打标哈希对账，不修改素材、产物或导入基线。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Literal

from ..tasks import TaskCancelledError
from .assets import confine_to_workdir, registered_origins
from .errors import AssetPathError, WorkdirPathError
from .importer import ASSET_EXTENSIONS, hash_file, size_limit
from .locks import import_guard
from .store import WorkdirStore

IntegrityStatus = Literal["valid", "changed", "unknown", "missing", "unreadable"]


@dataclass(frozen=True)
class IntegrityItem:
    """单个在册素材的校验结果；未知锚点不能用导入哈希冒充。"""

    item: str
    name: str
    status: IntegrityStatus
    current_hash: str | None
    labeling_hash: str | None
    detail: str


def _registered_path(workdir: Path, name: str) -> Path:
    """验证登记文件名后解析目录内文件，拒绝跨平台路径穿越。"""
    if Path(name).name != name or "/" in name or "\\" in name or "\x00" in name:
        raise AssetPathError("登记文件名含路径分隔符，无法安全读取。")
    return confine_to_workdir(workdir, workdir / name)


def scan_integrity(
    workdir: Path,
    labeling_hashes: Mapping[str, str],
    *,
    should_stop: Event | None = None,
) -> list[IntegrityItem]:
    """对已登记素材逐条校验，调用方提供当前批次的成功打标哈希。

    哈希来自运行域，由入口编排传入，避免工作目录域反向依赖执行器。
    单条文件失效仍返回其余条目的结果；登记册损坏则保留 fail-loud 行为。
    """
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    origins = registered_origins(WorkdirStore(workdir))
    result: list[IntegrityItem] = []
    for name in origins:
        if should_stop is not None and should_stop.is_set():
            raise TaskCancelledError()
        path = workdir / name
        item = Path(name).stem
        anchor = labeling_hashes.get(item) or None
        current: str | None = None
        status: IntegrityStatus
        detail = ""
        try:
            path = _registered_path(workdir, name)
            if not path.is_file():
                status = "missing"
                detail = "素材缺失，请重新导入。"
            elif path.suffix.lower() not in ASSET_EXTENSIONS:
                status = "unreadable"
                detail = "登记的文件不是支持的素材格式。"
            else:
                current = hash_file(path)
                if anchor is None:
                    status = "unknown"
                    detail = "没有成功打标时的素材哈希，无法判断产物时效。"
                elif anchor == current:
                    status = "valid"
                else:
                    status = "changed"
                    detail = "打标后素材已变更。"
        except (OSError, ValueError, AssetPathError):
            status = "unreadable"
            detail = "素材不可读取或路径越界，请检查文件与权限。"
        result.append(IntegrityItem(item, name, status, current, anchor, detail))
    if should_stop is not None and should_stop.is_set():
        raise TaskCancelledError()
    return result


def rebuild_import_records(
    workdir: Path, *, should_stop: Event | None = None
) -> dict[str, object]:
    """持导入锁重建当前登记快照，避免扫描期间其他进程追加导入后被遗漏。"""
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    with import_guard(WorkdirStore(workdir).dsf_path):
        return _rebuild_import_records(workdir, should_stop=should_stop)


def _rebuild_import_records(
    workdir: Path, *, should_stop: Event | None = None
) -> dict[str, object]:
    """扫描当前合法素材并追加来源为空的记录；不会伪造打标哈希。

    先完整扫描再追加，取消与读盘失败均不留下半份重建记录。
    主干冲突必须先由用户处置，不能任意选一份素材登记。
    """
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    store = WorkdirStore(workdir)
    store.read_import_records()
    files: list[dict[str, str]] = []
    stems: set[str] = set()
    for path in sorted(workdir.iterdir(), key=lambda entry: entry.name):
        if should_stop is not None and should_stop.is_set():
            raise TaskCancelledError()
        if not path.is_file() or path.suffix.lower() not in ASSET_EXTENSIONS:
            continue
        confine_to_workdir(workdir, path)
        if path.stat().st_size > size_limit(path.suffix):
            continue
        if path.stem in stems:
            raise WorkdirPathError("素材主干冲突，请重命名其中一份后重建。")
        stems.add(path.stem)
        files.append({"name": path.name, "sha256": hash_file(path)})
    if should_stop is not None and should_stop.is_set():
        raise TaskCancelledError()
    store.append_import_record(
        {
            "imported_at": datetime.now(UTC).isoformat(),
            "source": "",
            "files": files,
            "kind": "rebuild",
        }
    )
    return {"file_count": len(files), "source": ""}
