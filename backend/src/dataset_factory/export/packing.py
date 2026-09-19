"""按文件现状生成交付计划，ZIP 只包含素材与同名 caption。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from zipfile import ZIP_DEFLATED, ZipFile

from .._fs import hash_stream, is_single_path_segment
from ..tasks import TaskCancelledError
from ..workdir.assets import (
    ASSET_EXTENSIONS,
    confine_to_workdir,
    product_path,
    registered_origins,
    unimported_files,
)
from ..workdir.errors import AssetPathError, WorkdirPathError
from ..workdir.integrity import IntegrityStatus, scan_integrity
from ..workdir.store import WorkdirStore


class ExportError(Exception):
    """导出无法完成，错误消息说明可重试或需要处置的原因。"""


@dataclass(frozen=True)
class ExportRow:
    """一条配对计划或排除记录；大小为未压缩的实际字节数。"""

    item: str
    name: str
    asset_name: str | None = None
    caption_name: str | None = None
    asset_bytes: int = 0
    caption_bytes: int = 0
    asset_hash: str | None = None
    caption_hash: str | None = None
    integrity: IntegrityStatus | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ExportPlan:
    """当前批次的将入包、被排除清单与原名兼容性提示。"""

    batch: int
    sequential: bool
    included: list[ExportRow]
    excluded: list[ExportRow]
    total_bytes: int
    non_ascii_names: bool


def build_export_plan(
    workdir: Path,
    seq: int,
    *,
    labeling_hashes: Mapping[str, str],
    failed_items: Set[str] = frozenset(),
    excluded_items: Set[str] = frozenset(),
    sequential: bool = True,
    should_stop: Event | None = None,
) -> ExportPlan:
    """扫描当前批次配对，按导入顺序生成清单；过期和未知锚点不自动排除。

    批次存在性、活跃状态与运行流水由入口层核实并传入；本模块只依赖工作目录域。
    磁盘上的同主干多素材按配对冲突排除，避免悄悄选择其中一份交付。
    """
    _check_cancel(should_stop)
    if seq < 1:
        raise ExportError("批次序号必须为正整数。")
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    origins = registered_origins(WorkdirStore(workdir))
    integrity = scan_integrity(workdir, labeling_hashes, should_stop=should_stop)
    counts: dict[str, int] = {}
    for path in workdir.iterdir():
        if path.is_file() and path.suffix.lower() in ASSET_EXTENSIONS:
            counts[path.stem] = counts.get(path.stem, 0) + 1
    included: list[ExportRow] = []
    excluded: list[ExportRow] = []
    for checked in integrity:
        _check_cancel(should_stop)
        row = ExportRow(checked.item, checked.name, integrity=checked.status)
        reason: str | None = None
        if checked.status == "missing":
            reason = "缺失"
        elif checked.status == "unreadable":
            reason = "素材不可读取"
        elif counts.get(checked.item, 0) > 1:
            reason = "配对冲突"
        elif checked.item in excluded_items:
            reason = "用户排除"
        elif checked.item in failed_items:
            reason = "未完成"
        else:
            try:
                caption = product_path(workdir, seq, checked.item)
                if not caption.is_file():
                    reason = "无配对产物"
                else:
                    content = caption.read_bytes()
                    if not content.decode("utf-8").strip():
                        reason = "产物异常"
                    else:
                        row = replace(
                            row,
                            asset_bytes=(workdir / checked.name).stat().st_size,
                            caption_bytes=len(content),
                            asset_hash=checked.current_hash,
                            caption_hash=hashlib.sha256(content).hexdigest(),
                        )
            except (OSError, UnicodeDecodeError, AssetPathError):
                reason = "产物异常"
        if reason is None:
            included.append(row)
        else:
            excluded.append(replace(row, reason=reason))
    for unimported in unimported_files(workdir, set(origins)):
        excluded.append(
            ExportRow(Path(unimported.name).stem, unimported.name, reason="未登记")
        )
    if not sequential:
        stems: dict[str, int] = {}
        for row in included:
            key = row.item.casefold()
            stems[key] = stems.get(key, 0) + 1
        conflicts = {key for key, count in stems.items() if count > 1}
        excluded.extend(
            replace(row, reason="配对冲突")
            for row in included
            if row.item.casefold() in conflicts
        )
        included = [row for row in included if row.item.casefold() not in conflicts]
    width = max(3, len(str(len(included))))
    included = [
        replace(
            row,
            asset_name=(
                f"{index:0{width}d}{Path(row.name).suffix}" if sequential else row.name
            ),
            caption_name=(
                f"{index:0{width}d}.txt" if sequential else f"{row.item}.txt"
            ),
        )
        for index, row in enumerate(included, 1)
    ]
    _check_cancel(should_stop)
    return ExportPlan(
        seq,
        sequential,
        included,
        excluded,
        sum(row.asset_bytes + row.caption_bytes for row in included),
        not sequential and any(not row.name.isascii() for row in included),
    )


def _check_cancel(should_stop: Event | None) -> None:
    """在扫描和写入的安全点响应协作取消。"""
    if should_stop is not None and should_stop.is_set():
        raise TaskCancelledError()


def _write_member(
    archive: ZipFile,
    source: Path,
    name: str,
    expected_hash: str,
    should_stop: Event | None,
) -> None:
    """分块写入并校验实际写入字节，避免计划与交付内容之间的变更被遗漏。"""
    with (
        source.open("rb") as reader,
        archive.open(name, "w", force_zip64=True) as writer,
    ):
        digest, _ = hash_stream(
            reader, writer, on_chunk=lambda _chunk: _check_cancel(should_stop)
        )
    if digest != expected_hash:
        raise ExportError(
            f"文件「{source.name}」在打包期间发生变化，请重新生成导出计划。"
        )


def write_export(
    workdir: Path,
    plan: ExportPlan,
    destination: Path,
    *,
    should_stop: Event | None = None,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """把已校验计划写为 ZIP，成功后原子发布，不覆盖已有文件。

    临时包与目标位于同一文件系统；Windows 用不覆盖改名，Unix 用硬链接原子发布。
    失败或取消只清除本次临时包，不修改素材、caption 和已存在的交付物。
    """
    _check_cancel(should_stop)
    if not plan.included:
        raise ExportError("没有可打包的配对，请先完成打标或检查排除清单。")
    destination = destination.absolute()
    if destination.exists():
        raise ExportError("输出文件已存在，请选择另一个文件名。")
    if destination.suffix.lower() != ".zip":
        raise ExportError("输出文件必须使用 .zip 扩展名。")
    if not destination.parent.is_dir():
        raise ExportError("输出目录不存在，请选择已有目录。")
    names: set[str] = set()
    for row in plan.included:
        for name in (row.asset_name, row.caption_name):
            if (
                not name
                or not is_single_path_segment(name)
                or (name.casefold() in names)
            ):
                raise ExportError("导出文件名存在路径或配对冲突，请重新生成导出计划。")
            names.add(name.casefold())
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".dsf-export-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for index, row in enumerate(plan.included, 1):
                _check_cancel(should_stop)
                if not row.asset_hash or not row.caption_hash:
                    raise ExportError("导出计划缺少文件哈希，请重新生成导出计划。")
                asset = confine_to_workdir(workdir, workdir / row.name)
                caption = product_path(workdir, plan.batch, row.item)
                _write_member(
                    archive, asset, str(row.asset_name), row.asset_hash, should_stop
                )
                _write_member(
                    archive,
                    caption,
                    str(row.caption_name),
                    row.caption_hash,
                    should_stop,
                )
                if progress is not None:
                    progress(index / len(plan.included))
        _check_cancel(should_stop)
        with temporary.open("r+b") as reader:
            os.fsync(reader.fileno())
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    except OSError as exc:
        raise ExportError(
            "写入导出文件失败，请检查输出目录权限、空间或文件占用。"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination
