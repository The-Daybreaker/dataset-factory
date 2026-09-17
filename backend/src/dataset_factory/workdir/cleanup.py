"""工作目录清理：按实时配对关系列出可清理的文件。"""

from dataclasses import dataclass
from pathlib import Path

from .assets import (
    confine_to_workdir,
    is_product_name,
    registered_origins,
    scan_assets,
    unimported_files,
)
from .errors import WorkdirPathError
from .locks import RunLock, import_guard
from .store import WorkdirStore


@dataclass(frozen=True)
class OrphanProduct:
    """一份没有素材配对的批次产物。"""

    batch: int
    name: str
    size: int


def preview_cleanup(workdir: Path) -> list[OrphanProduct]:
    """按文件名顺序列出孤立产物，不把普通文本或现有配对列入。"""
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    assets = scan_assets(workdir)
    result: list[OrphanProduct] = []
    for path in sorted(workdir.iterdir()):
        if not path.is_file() or not is_product_name(path.name):
            continue
        batch, item = path.stem.split("__", 1)
        if item in assets:
            continue
        confine_to_workdir(workdir, path)
        result.append(OrphanProduct(int(batch[1:]), path.name, path.stat().st_size))
    return result


@dataclass(frozen=True)
class CleanupResult:
    """已移出数量及删除失败时的残留目录，完整清理后路径为空。"""

    count: int
    recovery_path: str | None


def remove_unimported(workdir: Path, names: list[str]) -> CleanupResult:
    """将仍未登记的指定文件移入可恢复暂存区，保留其原始字节。

    导入锁覆盖资格复查与移动，避免文件已入册却仍按旧清单移出。
    素材使用完整文件名定位，同主干的其他扩展名不受影响。
    """
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    store = WorkdirStore(workdir)
    lock = RunLock(store.dsf_path)
    try:
        lock.acquire({"operation": "remove-unimported"})
        with import_guard(store.dsf_path):
            candidates = {
                row.name
                for row in unimported_files(workdir, set(registered_origins(store)))
            }
            selected = list(dict.fromkeys(names))
            if not selected or not set(selected).issubset(candidates):
                raise WorkdirPathError("选择为空或文件已不在未导入清单，请刷新后重试。")
            recovery = store.quarantine_paths([workdir / name for name in selected])
            return CleanupResult(len(selected), str(recovery) if recovery else None)
    finally:
        lock.release()


def cleanup_products(workdir: Path, names: list[str]) -> CleanupResult:
    """持锁重新核对整份选择，只有仍为孤立产物的文件可清理。"""
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    store = WorkdirStore(workdir)
    lock = RunLock(store.dsf_path)
    lock.acquire({"operation": "cleanup"})
    try:
        with import_guard(store.dsf_path):
            candidates = {row.name for row in preview_cleanup(workdir)}
            selected = list(dict.fromkeys(names))
            if not selected or not set(selected).issubset(candidates):
                raise WorkdirPathError("选择为空或文件已不再是孤立产物，请刷新清单。")
            recovery = store.quarantine_paths([workdir / name for name in selected])
            return CleanupResult(len(selected), store.finish_cleanup(recovery))
    finally:
        lock.release()


@dataclass(frozen=True)
class RunCleanupEntry:
    """一份运行记录目录的清理预览，不依赖记录内容能否解析。"""

    name: str
    size: int
    modified_at: float


def preview_run_cleanup(workdir: Path) -> list[RunCleanupEntry]:
    """列出运行目录与真实体积，损坏流水仍可通过本入口清理。"""
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    runs = confine_to_workdir(workdir, workdir / ".dsf" / "runs")
    if not runs.is_dir():
        return []
    result: list[RunCleanupEntry] = []
    for directory in sorted(runs.iterdir(), reverse=True):
        if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
            continue
        confine_to_workdir(workdir, directory)
        total = 0
        for path in directory.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise WorkdirPathError("运行记录包含链接，请检查后重试。")
            confine_to_workdir(workdir, path)
            if path.is_file():
                total += path.stat().st_size
        result.append(RunCleanupEntry(directory.name, total, directory.stat().st_mtime))
    return result


def cleanup_runs(workdir: Path, names: list[str]) -> CleanupResult:
    """运行锁内整份移动所选记录，其他运行、素材和产物保持原样。"""
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    store = WorkdirStore(workdir)
    lock = RunLock(store.dsf_path)
    lock.acquire({"operation": "cleanup-runs"})
    try:
        candidates = {row.name for row in preview_run_cleanup(workdir)}
        selected = list(dict.fromkeys(names))
        if not selected or not set(selected).issubset(candidates):
            raise WorkdirPathError("选择为空或运行记录已变化，请刷新清单。")
        recovery = store.quarantine_paths([store.runs_dir / name for name in selected])
        return CleanupResult(len(selected), store.finish_cleanup(recovery))
    finally:
        lock.release()
