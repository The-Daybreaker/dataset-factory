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
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..llm import VIDEO_EXTENSIONS
from ..tasks import TaskCancelledError
from .errors import ImportSourceConflictError, WorkdirError, WorkdirPathError
from .store import WorkdirStore

__all__ = [
    "ASSET_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MAX_IMAGE_BYTES",
    "MAX_VIDEO_BYTES",
    "REASON_OVERSIZE",
    "REASON_UNSUPPORTED_EXTENSION",
    "ensure_importable_source",
    "import_assets",
]

#: 图片扩展名窄清单（OpenAI 兼容端点事实标准 + torchvision 训练生态；design 项 12）。
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: 视频扩展名单一事实源 = llm.VIDEO_EXTENSIONS（mp4/m4v/mov/webm/avi/mkv，一期已验收语义；
#: workdir 与 labeling 同层、llm 在其下，直接复用防口径漂移——audit 2026-09-14 收敛结论）。

#: 全部可导入扩展名 = 图片 ∪ 视频。
ASSET_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

#: 大小护栏：图片 ≤ 20 MiB（沿用一期单图上限）；视频 ≤ 100 MiB（二期新增，批量
#: 无人值守必须本地限制——GB 级视频会复制、整读进内存、base64 再胀 1.33 倍）。
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024

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


def _size_limit(suffix: str) -> int:
    """按扩展名归类取大小上限（视频 / 图片各一档）。"""
    return MAX_VIDEO_BYTES if suffix in VIDEO_EXTENSIONS else MAX_IMAGE_BYTES


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
        if size > _size_limit(suffix):
            rejected.append({"name": entry.name, "reason": REASON_OVERSIZE})
            continue
        candidates.append(_Candidate(name=entry.name, path=entry, size=size))
    return candidates, rejected


def _hash_file(path: Path) -> str:
    """分块计算一个文件的 SHA-256（不整读进内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_into_workdir(store: WorkdirStore, source_file: Path, dest_name: str) -> None:
    """把源文件复制进工作目录：先落 ``.dsf/tmp/``，写完原子改名到最终位置。

    中断最多留下 tmp 垃圾（下次导入开头清掉），目标文件绝无半截——重入幂等的根基。
    """
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_target = store.tmp_dir / dest_name
    shutil.copyfile(source_file, tmp_target)
    os.replace(tmp_target, store.dsf_path.parent / dest_name)


def _workdir_asset_map(workdir: Path) -> dict[str, Path]:
    """工作目录现状里的合法素材（白名单 + 护栏内），文件名 → 路径（平铺）。"""
    return {
        entry.name: entry
        for entry in sorted(workdir.iterdir(), key=lambda item: item.name)
        if entry.is_file()
        and entry.suffix.lower() in ASSET_EXTENSIONS
        and entry.stat().st_size <= _size_limit(entry.suffix.lower())
    }


class _Baseline:
    """工作目录现状的比对基线：合法素材的文件名 → 路径，配按需缓存的哈希查询。

    内容比对前先做大小预筛——大小不同的文件内容必不同，不值得读盘。
    """

    def __init__(self, files: dict[str, Path]) -> None:
        """以「文件名 → 路径」建立基线。"""
        self._files = files
        self._hashes: dict[str, str] = {}

    @property
    def stems(self) -> set[str]:
        """基线里已被占用的素材主干。"""
        return {Path(name).stem for name in self._files}

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
            self._hashes[name] = _hash_file(self._files[name])
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
    baseline_files = _workdir_asset_map(workdir)
    if in_place:
        # 就地采用：候选就是工作目录里的合法素材本身，基线排除候选——
        # 文件和它自己比较必然同名同容，那不是重复；首次登记因此全量进记录，
        # 中断后的重新采用也自然全量重登记（自愈）。
        candidate_names = {candidate.name for candidate in candidates}
        baseline_files = {
            name: path
            for name, path in baseline_files.items()
            if name not in candidate_names
        }
    baseline = _Baseline(baseline_files)
    _clean_tmp(store)

    imported: list[str] = []
    skipped_identical: list[str] = []
    skipped_conflict: list[dict[str, Any]] = []
    skipped_duplicate: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = list(scan_rejected)
    registered: list[dict[str, str]] = []  # 进记录的（新导入 + 同名同容重登记）
    accepted_stems: set[str] = set()

    total = len(candidates)
    for index, candidate in enumerate(candidates):
        if should_stop is not None and should_stop.is_set():
            raise TaskCancelledError()
        if progress is not None:
            progress(0.05 + 0.9 * index / total)
        name = candidate.name
        stem = Path(name).stem
        same_name = baseline.path_of(name)
        if same_name is not None:
            incoming_hash = _hash_file(candidate.path)
            existing_hash = baseline.hash_of(name)
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
        # 主干冲突：与基线或本批已接受的素材争同一个条目身份（同名不同扩展名）。
        if stem in baseline.stems or stem in accepted_stems:
            rejected.append(
                {
                    "name": name,
                    "reason": "与已有素材主干冲突（同名不同扩展名），请重命名其一后再导入",
                },
            )
            continue
        incoming_hash = _hash_file(candidate.path)
        duplicate_of = _find_duplicate_content(baseline, incoming_hash, candidate.size)
        if duplicate_of is not None and name not in force_names:
            # 异名同容：默认跳过；可选仍按新名导入（force_names 逐条点名）。
            skipped_duplicate.append({"name": name, "duplicate_of": duplicate_of})
            continue
        if not in_place:
            try:
                _copy_into_workdir(store, candidate.path, name)
            except OSError as exc:
                raise WorkdirError(
                    f"复制素材「{name}」失败：{exc}——请检查磁盘空间与权限后重试。",
                ) from exc
        imported.append(name)
        registered.append({"name": name, "sha256": incoming_hash})
        accepted_stems.add(stem)

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
