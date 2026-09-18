"""素材导入：扫描窄清单 + 大小护栏 + 重复导入判定 + ``imports.jsonl`` 登记（二期新增）。

导入是往工作目录加素材的**唯一通道**（design「素材导入与出身边界」）：目录按图片 /
视频扩展名窄清单平铺扫描、不递归，复制进工作目录（或就地采用不复制），每次导入追加
一条记录。记录只被「看」（回看出身、判定缺失、判定产物有效性），**不参与运行判定**——
跑批按工作目录现状定打哪条，清单与事实源分离（CMake ``file(GLOB)`` 反例的教训）。

重复导入四情形只在导入那一刻判定（纯函数、不产生持久状态）：
同名同容 → 跳过（幂等）；同名异容 → 不覆盖、跳过（本版无替换操作）；
异名同容 → 默认跳过、可选仍按新名导入（force_names）；异名异容 → 正常导入。

条目身份 = 素材主干（不含扩展名），同主干多扩展并存会让两个素材争一个身份，
导入时拒绝并要求重命名其一。

自愈语义（任务句柄约定）：复制先落 ``.dsf/tmp/`` 再原子改名——中断最多留下 tmp 垃圾
（下次导入开头清掉），目标文件绝无半截；已复制但记录未写的文件重导时按同名同容
重登记，重入幂等。
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .._fs import hash_file
from ..llm import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    VIDEO_EXTENSIONS,
)
from ..tasks import TaskCancelledError
from .errors import ImportSourceConflictError, WorkdirError, WorkdirPathError
from .locks import import_guard
from .store import WorkdirStore

__all__ = [
    "ASSET_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MAX_IMAGE_BYTES",
    "MAX_VIDEO_BYTES",
    "REASON_OVERSIZE",
    "REASON_UNSUPPORTED_EXTENSION",
    "ensure_importable_source",
    "hash_file",
    "import_assets",
    "size_limit",
]

#: 图片 / 视频扩展名与大小护栏的单一事实源都在 llm（IMAGE_EXTENSIONS /
#: VIDEO_EXTENSIONS / MAX_IMAGE_BYTES / MAX_VIDEO_BYTES）——workdir 导入与 labeling
#: 纯素材路径共用，防两处各写一份清单或上限悄悄漂移（audit 2026-09-14 收敛口径的延伸）。

#: 全部可导入扩展名 = 图片 ∪ 视频。
ASSET_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

#: 「未导入」原因的标准措辞（界面按原因呈现，字符串即契约）。
REASON_UNSUPPORTED_EXTENSION = "扩展名不支持"
REASON_OVERSIZE = "超出大小上限"

#: 扫描与哈希共用读取块大小（1 MiB：分块读，100 MiB 视频内存峰值恒定）。
_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _Candidate:
    """通过窄清单与大小护栏的源文件（哈希在处理到它时才算，不预付全量）。"""

    name: str
    path: Path
    size: int


def ensure_importable_source(workdir: Path, source: Path) -> None:
    """校验复制导入的来源目录可用：存在、是目录、与工作目录不同且互不嵌套。

    Raises:
        WorkdirPathError: 来源不存在或不是目录。
        ImportSourceConflictError: 来源与工作目录相同或互为嵌套（复制会自我覆盖）。
    """
    if not source.is_dir():
        raise WorkdirPathError(
            f"来源目录「{source}」不存在或不是目录——请检查后重试。",
        )
    real_workdir = os.path.realpath(workdir)
    real_source = os.path.realpath(source)
    if real_workdir == real_source or _is_nested(real_workdir, real_source):
        raise ImportSourceConflictError(
            f"来源目录「{source}」与工作目录「{workdir}」相同或互为嵌套——"
            "复制导入会造成自我覆盖。请选择工作目录之外的目录；"
            "想直接使用目录内素材请用「就地采用」（不选来源目录）。",
        )


def _is_nested(a: str, b: str) -> bool:
    """两个 realpath 是否互为祖先目录（任一是另一个的前缀即嵌套）。"""
    return a.startswith(b + os.sep) or b.startswith(a + os.sep)


def size_limit(suffix: str) -> int:
    """按扩展名取大小护栏上限（图片 / 视频各一档；两个常量都在 llm 作单一事实源）。"""
    return MAX_VIDEO_BYTES if suffix.lower() in VIDEO_EXTENSIONS else MAX_IMAGE_BYTES


def _scan_assets(directory: Path) -> tuple[list[_Candidate], list[dict[str, str]]]:
    """平铺扫描一个目录（不递归），按窄清单与大小护栏分成候选与拒绝两组。

    只认文件（子目录跳过）；扩展名大小写不敏感；白名单外与超限的文件进拒绝组
    并带标准原因——它们会出现在「未导入」反馈里由用户处置。
    """
    candidates: list[_Candidate] = []
    rejected: list[dict[str, str]] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix not in ASSET_EXTENSIONS:
            rejected.append(
                {"name": entry.name, "reason": REASON_UNSUPPORTED_EXTENSION},
            )
            continue
        size = entry.stat().st_size
        if size > size_limit(suffix):
            rejected.append({"name": entry.name, "reason": REASON_OVERSIZE})
            continue
        candidates.append(_Candidate(name=entry.name, path=entry, size=size))
    return candidates, rejected


def _copy_into_workdir(store: WorkdirStore, source_file: Path, dest_name: str) -> str:
    """把源文件复制进工作目录：先落 ``.dsf/tmp/``，写完原子改名到最终位置。

    哈希与写入同一次读完成——返回的是**实际落盘内容**的 SHA-256（导入记录的
    锚点必须指向磁盘上那份字节，而不是「复制前那一刻的源文件」）。
    中断最多留下 tmp 垃圾（下次导入开头清掉），目标文件绝无半截——重入幂等的根基。
    """
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_target = store.tmp_dir / dest_name
    digest = hashlib.sha256()
    with source_file.open("rb") as src, tmp_target.open("wb") as dst:
        while chunk := src.read(_CHUNK_BYTES):
            dst.write(chunk)
            digest.update(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp_target, store.dsf_path.parent / dest_name)
    return digest.hexdigest()


def _whitelisted_files(directory: Path) -> dict[str, Path]:
    """目录里全部白名单内文件（不看大小护栏），文件名 → 路径（平铺）。

    「同名不覆盖」与「主干唯一」保护的是**物理存在**的文件——哪怕它超限、
    是用户手工放进去的，也不能被导入静默替换。
    """
    return {
        entry.name: entry
        for entry in sorted(directory.iterdir(), key=lambda item: item.name)
        if entry.is_file() and entry.suffix.lower() in ASSET_EXTENSIONS
    }


def _workdir_asset_map(workdir: Path) -> dict[str, Path]:
    """工作目录现状里的**合法素材**（白名单 + 护栏内），文件名 → 路径（平铺）。"""
    return {
        name: path
        for name, path in _whitelisted_files(workdir).items()
        if path.stat().st_size <= size_limit(path.suffix.lower())
    }


class _Baseline:
    """工作目录**合法素材**的比对基线：文件名 → 路径，配按需缓存的哈希查询。

    只收白名单内且护栏内的文件（内容比对只在合法素材之间进行）；大小不同的
    文件内容必不同，内容比对前先做大小预筛。同文名 / 主干冲突的检测不在这层
    （那些要对物理存在的文件生效，见 ``_whitelisted_files``）。
    """

    def __init__(self, files: dict[str, Path]) -> None:
        """以「文件名 → 路径」建立基线。"""
        self._files = files
        self._hashes: dict[str, str] = {}

    def path_of(self, name: str) -> Path | None:
        """基线里同名文件的路径；没有返回 None。"""
        return self._files.get(name)

    def same_size_names(self, size: int) -> list[str]:
        """基线里大小相同的文件名（内容比对前的预筛）。"""
        return [
            name for name, path in self._files.items() if path.stat().st_size == size
        ]

    def hash_of(self, name: str) -> str:
        """取基线文件的内容哈希（带缓存）。"""
        if name not in self._hashes:
            self._hashes[name] = hash_file(self._files[name])
        return self._hashes[name]


def _find_duplicate_content(
    baseline: _Baseline, incoming_hash: str, size: int
) -> str | None:
    """在基线里找内容相同（哈希相等）的文件名；没有返回 None。"""
    for name in baseline.same_size_names(size):
        if baseline.hash_of(name) == incoming_hash:
            return name
    return None


def _clean_tmp(store: WorkdirStore) -> None:
    """清掉上次中断留下的 tmp 垃圾（自愈语义的一部分；目录不存在即跳过）。"""
    if not store.tmp_dir.is_dir():
        return
    for entry in store.tmp_dir.iterdir():
        if entry.is_file():
            entry.unlink(missing_ok=True)


def import_assets(
    workdir: Path,
    source: Path | None = None,
    *,
    force_names: frozenset[str] | set[str] = frozenset(),
    names: set[str] | None = None,
    should_stop: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """持目录级导入锁执行素材导入，跨 CLI 与 HTTP 进程防止登记丢失。

    source 缺省为就地采用；force_names 控制异名同容文件的强制导入。
    should_stop 与 progress 分别接收取消信号和文件级进度回调。
    """
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    with import_guard(WorkdirStore(workdir).dsf_path):
        return _import_assets(
            workdir,
            source,
            force_names=force_names,
            names=names,
            should_stop=should_stop,
            progress=progress,
        )


def _import_assets(
    workdir: Path,
    source: Path | None = None,
    *,
    force_names: frozenset[str] | set[str] = frozenset(),
    names: set[str] | None = None,
    should_stop: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """执行一次导入：扫描 → 逐个判定 → 复制（或就地）→ 追加一条导入记录。

    Args:
        workdir: 工作目录路径（须已登记；``.dsf/`` 结构由 WorkdirStore 确保）。
        source: 来源目录；``None`` = 就地采用（来源记工作目录自身、不复制）。
            提供时经 :func:`ensure_importable_source` 校验。
        force_names: 异名同容时仍按新名强制导入的源文件名清单（同名异容不受
            影响——本版不提供替换操作）。
        names: 只处理指定文件名；None 表示扫描全部，不接受路径。
        should_stop: 协作取消信号；每处理完一个文件检查一次，置位即抛
            TaskCancelledError（任务体安全点约定）。
        progress: 进度回调（0.0–1.0，按候选文件数推进；扫描完成报 0.05、
            记录落盘前 0.95、完成 1.0）。

    Returns:
        导入报告（任务结果载荷 / 界面三态反馈的数据源）：``imported_at``、
        ``source``、``imported``（新进工作目录）、``skipped_identical``（同名
        同容，已重登记）、``skipped_conflict``（同名异容，含新旧大小与哈希
        对照）、``skipped_duplicate``（异名同容，含撞容条目名）、``rejected``
        （白名单外 / 超限 / 主干冲突，含原因）。

    Raises:
        WorkdirPathError: 来源目录不存在或不是目录。
        ImportSourceConflictError: 来源与工作目录相同或互为嵌套。
        WorkdirError: 复制失败（磁盘 / 权限等，消息带文件名）。
        TaskCancelledError: 取消信号置位。
    """
    store = WorkdirStore(workdir)
    if source is None:
        in_place = True
        source_path = workdir
    else:
        in_place = False
        ensure_importable_source(workdir, source)
        source_path = source

    candidates, scan_rejected = _scan_assets(source_path)
    if names is not None:
        if any(
            not name or name in {".", ".."} or any(c in name for c in "/\\\x00")
            for name in names
        ):
            raise WorkdirPathError("选择必须是文件名，不能包含路径。")
        candidates = [candidate for candidate in candidates if candidate.name in names]
        scan_rejected = [row for row in scan_rejected if row["name"] in names]
        found = {candidate.name for candidate in candidates} | {
            row["name"] for row in scan_rejected
        }
        scan_rejected.extend(
            {"name": name, "reason": "来源文件不存在或不可读取"}
            for name in sorted(names - found)
        )
    candidate_names = {candidate.name for candidate in candidates}
    all_files = _whitelisted_files(workdir)
    baseline_files = _workdir_asset_map(workdir)
    if in_place:
        # 就地采用：候选就是工作目录里的文件本身，两种视图都排除候选——
        # 文件和它自己比较必然同名同容，那不是重复；首次登记因此全量进记录，
        # 中断后的重新采用也自然全量重登记（自愈）。
        all_files = {
            name: path
            for name, path in all_files.items()
            if name not in candidate_names
        }
        baseline_files = {
            name: path
            for name, path in baseline_files.items()
            if name not in candidate_names
        }
    baseline = _Baseline(baseline_files)
    all_stems = {Path(name).stem for name in all_files}
    _clean_tmp(store)

    imported: list[str] = []
    skipped_identical: list[str] = []
    skipped_conflict: list[dict[str, Any]] = []
    skipped_duplicate: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = list(scan_rejected)
    registered: list[dict[str, str]] = []  # 进记录的（新导入 + 同名同容重登记）
    accepted_stems: set[str] = set()
    batch_hashes: dict[
        str, str
    ] = {}  # 本批已接受的内容哈希 → 首个文件名（批内同容比对）

    total = len(candidates)
    for index, candidate in enumerate(candidates):
        if should_stop is not None and should_stop.is_set():
            raise TaskCancelledError()
        if progress is not None:
            progress(0.05 + 0.9 * index / total)
        name = candidate.name
        stem = Path(name).stem
        # 同名检查对**物理存在**的白名单文件生效（含超限的手工文件）——绝不静默覆盖。
        same_name = all_files.get(name)
        if same_name is not None:
            incoming_hash = hash_file(candidate.path)
            if baseline.path_of(name) is not None:
                existing_hash = baseline.hash_of(name)
            else:
                # 同名但非法（超限）：内容不可能相等（同容必同尺寸同档），直接按冲突处理。
                existing_hash = hash_file(same_name)
            if incoming_hash == existing_hash:
                # 同名同容：不重复复制（幂等），但重登记——中断重入时已复制的
                # 文件由此回到记录里（自愈）；正常重导只是出身刷新到最近一次。
                skipped_identical.append(name)
                registered.append({"name": name, "sha256": incoming_hash})
            else:
                # 同名异容：不覆盖、跳过（本版不提供替换；要更新先删旧再导）。
                skipped_conflict.append(
                    {
                        "name": name,
                        "existing_size": same_name.stat().st_size,
                        "incoming_size": candidate.size,
                        "existing_sha256": existing_hash,
                        "incoming_sha256": incoming_hash,
                    },
                )
            continue
        # 主干冲突：同样对物理存在的文件（含超限）与本批已接受者生效。
        if stem in all_stems or stem in accepted_stems:
            rejected.append(
                {
                    "name": name,
                    "reason": "与已有素材主干冲突（同名不同扩展名），请重命名其一后再导入",
                },
            )
            continue
        incoming_hash = hash_file(candidate.path)
        # 异名同容：与基线（工作目录已有）和本批已接受者全量比对。
        duplicate_of = _find_duplicate_content(
            baseline, incoming_hash, candidate.size
        ) or batch_hashes.get(incoming_hash)
        if duplicate_of is not None and name not in force_names:
            skipped_duplicate.append({"name": name, "duplicate_of": duplicate_of})
            continue
        if not in_place:
            try:
                # 记录哈希取**实际落盘内容**（复制与哈希同一次读），锚点不撒谎。
                disk_hash = _copy_into_workdir(store, candidate.path, name)
            except OSError as exc:
                raise WorkdirError(
                    f"复制素材「{name}」失败：{exc}——请检查磁盘空间与权限后重试。",
                ) from exc
        else:
            disk_hash = incoming_hash
        imported.append(name)
        registered.append({"name": name, "sha256": disk_hash})
        accepted_stems.add(stem)
        if incoming_hash not in batch_hashes:
            batch_hashes[incoming_hash] = name

    if progress is not None:
        progress(0.95)
    record: dict[str, object] = {
        "imported_at": datetime.now(UTC).isoformat(),
        "source": str(source_path),
        "files": registered,
    }
    store.append_import_record(record)
    if progress is not None:
        progress(1.0)
    return {
        "imported_at": record["imported_at"],
        "source": record["source"],
        "imported": imported,
        "skipped_identical": skipped_identical,
        "skipped_conflict": skipped_conflict,
        "skipped_duplicate": skipped_duplicate,
        "rejected": rejected,
    }
