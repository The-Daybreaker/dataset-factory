"""工作目录注册表与 ``.dsf/`` 门面（二期新增）。

设计要点：
- **工作目录 = 素材容器**，一个目录可以有多个批次（= 策略）并存；元数据在 ``.dsf/`` 子目录。
- **注册表** = 数据根里一份「最近使用工作目录」索引（短 ID + canonical path + 显示名 +
  最后使用时间），**是地址簿不是花名册**——存在性以磁盘为准，丢失自愈。
- **幂等判定 = realpath 比较**：同一物理目录走不同路径串（符号链接 / 盘符大小写差异）
  注册为同一条，避免重复登记。
- **wid 不随搬迁改变**（dsh 源码注释「path normalization rewrites paths, and a reference
  anchor must stay stable」）——注册表原地更新 path，wid 保持不变（RESEARCH-0006）。

本模块是 ``.dsf/`` 的唯一写者门面——外部模块（runs / strategies / export）均经此写。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import wraps
from pathlib import Path
from typing import Any, cast

from .._fs import atomic_write_text, data_root
from .errors import (
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
)
from .locks import StateLock, import_guard, registry_guard

__all__ = [
    "WorkdirEntry",
    "WorkdirRegistry",
    "WorkdirStore",
    "ensure_dsf_layout",
]

#: wid 长度（与 task_id 同一「随机短 ID + 查重」模式）。
_WID_LENGTH = 11

#: ``.dsf/`` 元数据子目录名（工作目录内的隐藏目录）。
_DSF_DIR_NAME = ".dsf"

#: 注册表文件路径（在数据根里，全局唯一）。
_REGISTRY_FILE_NAME = "workdirs.json"

#: 内部状态文件（在 ``.dsf/`` 里，存储本工作目录的批次列表与重试列表）。
_STATE_FILE_NAME = "state.json"


@dataclass
class WorkdirEntry:
    """注册表条目：wid + canonical path + 显示名 + 最后使用时间戳。

    path 用 os.path.normpath 规范化，但不做 realpath（realpath 只用于幂等比较、
    注册表存规范化路径更利于人读与跨平台对齐）。
    """

    id: str
    path: str
    title: str
    last_used_at: float  # epoch seconds（UTC）


def _generate_wid() -> str:
    """生成随机短 ID（uuid 截短、生成时查重）。"""
    return secrets.token_urlsafe(8)[:_WID_LENGTH]


def _realpath(path: Path) -> str:
    """取 realpath 字符串——幂等判定锚点。

    符号链接与盘符大小写差异都被 realpath 解析为同一物理路径。
    """
    return os.path.realpath(path)


def _read_registry() -> list[WorkdirEntry]:
    """读注册表。文件损坏抛 WorkdirMetadataCorruptedError（fail loud）。"""
    registry_file = data_root() / _REGISTRY_FILE_NAME
    data: list[dict[str, Any]] = []
    if registry_file.exists():
        try:
            raw = registry_file.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else []
        except (json.JSONDecodeError, OSError) as exc:
            raise WorkdirMetadataCorruptedError(
                f"工作目录注册表文件损坏（{registry_file}），请重建或删除该文件后重试。",
            ) from exc
    return [WorkdirEntry(**item) for item in data]


def _write_registry(entries: list[WorkdirEntry]) -> None:
    """原子写注册表。"""
    registry_file = data_root() / _REGISTRY_FILE_NAME
    payload = [asdict(entry) for entry in entries]
    atomic_write_text(registry_file, json.dumps(payload, ensure_ascii=False, indent=2))


def _registry_writer[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    """注册表写入口共用同一临界区，覆盖读取、检查和原子写入。"""

    @wraps(operation)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        with registry_guard(data_root()):
            return operation(*args, **kwargs)

    return guarded


class WorkdirRegistry:
    """全局工作目录注册表（数据根下唯一文件，跨工作目录共享）。

    幂等判定 = realpath 比较：同一物理目录只保留一条（最后使用的 wid），
    重复登记不创建新条目、只更新已有条目的 last_used_at 与 title。
    """

    @staticmethod
    def list_all() -> list[WorkdirEntry]:
        """列出全部注册条目，按最后使用时间倒序（最近在前）——顶部两级下拉的数据源。"""
        entries = _read_registry()
        entries.sort(key=lambda entry: entry.last_used_at, reverse=True)
        return entries

    @staticmethod
    def get(wid: str) -> WorkdirEntry:
        """按 wid 查条目。不存在抛 WorkdirNotFoundError。"""
        for entry in _read_registry():
            if entry.id == wid:
                return entry
        raise WorkdirNotFoundError(
            f"工作目录 {wid} 不在注册表中——可能已被删除，请从顶部下拉重新选择。",
        )

    @staticmethod
    @_registry_writer
    def register(path: Path, title: str = "") -> WorkdirEntry:
        """登记一个工作目录；幂等（同 realpath 只更新 last_used_at 与路径）。

        Args:
            path: 工作目录路径（会做 is_dir 校验）。
            title: 显示名；空串 = 用目录名兜底（更新时空串 = 沿用旧名不改）。

        Returns:
            注册表条目（新建或更新后的现状）。
        """
        if not path.is_dir():
            raise WorkdirPathError(
                f"路径「{path}」不存在或不是目录——请检查后重试。",
            )
        canonical = str(path)
        real = _realpath(path)
        entries = _read_registry()
        # 幂等：同物理目录只保留一条（更新已有条目）。
        for existing in entries:
            if _realpath(Path(existing.path)) == real:
                existing.path = canonical
                if title:
                    existing.title = title
                existing.last_used_at = time.time()
                _write_registry(entries)
                return existing
        # 新登记：分配随机 wid、查重。
        wid = _generate_wid()
        existing_ids = {e.id for e in entries}
        while wid in existing_ids:
            wid = _generate_wid()
        entry = WorkdirEntry(
            id=wid,
            path=canonical,
            title=title if title else path.name,
            last_used_at=time.time(),
        )
        entries.append(entry)
        _write_registry(entries)
        return entry

    @staticmethod
    @_registry_writer
    def update_path(wid: str, new_path: Path) -> WorkdirEntry:
        """搬迁后原地更新 path（wid 不变）。"""
        if not new_path.is_dir():
            raise WorkdirPathError(
                f"新路径「{new_path}」不存在或不是目录——搬迁未完成。",
            )
        entries = _read_registry()
        if any(
            entry.id != wid and _realpath(Path(entry.path)) == _realpath(new_path)
            for entry in entries
        ):
            raise WorkdirPathError("新路径已属于另一个工作目录，请选择其他位置。")
        for entry in entries:
            if entry.id == wid:
                entry.path = str(new_path)
                entry.last_used_at = time.time()
                _write_registry(entries)
                return entry
        raise WorkdirNotFoundError(
            f"工作目录 {wid} 不在注册表中——无法更新路径。",
        )

    @staticmethod
    @_registry_writer
    def remove(wid: str) -> None:
        """从注册表移除（工作目录本身不动）。"""
        entries = _read_registry()
        filtered = [e for e in entries if e.id != wid]
        if len(filtered) == len(entries):
            raise WorkdirNotFoundError(
                f"工作目录 {wid} 不在注册表中——无法删除。",
            )
        _write_registry(filtered)


def ensure_dsf_layout(workdir_path: Path) -> Path:
    """确保工作目录内 ``.dsf/`` 子目录结构就位，返回 ``.dsf`` 路径。

    幂等：已存在的目录不报错、不重建。创建层级：

    - ``.dsf/``（元数据根）
    - ``.dsf/strategies/``（策略快照全文）
    - ``.dsf/runs/``（每次运行一个子目录）

    ``state.json`` / ``imports.jsonl`` / ``run.lock`` / ``state.lock`` / ``run-info.json``
    由各自写入方在首次写时创建，本函数只保证目录结构。
    """
    dsf = workdir_path / _DSF_DIR_NAME
    (dsf / "strategies").mkdir(parents=True, exist_ok=True)
    (dsf / "runs").mkdir(parents=True, exist_ok=True)
    return dsf


class WorkdirStore:
    """单个工作目录的 ``.dsf/`` 门面——``.dsf/`` 下的唯一写者。

    外部模块（runs / strategies / export）均经此写文件，保证所有写入走原子写
    且路径在 ``.dsf/`` 内（不做 realpath confine 校验，那是素材预览端点的事——
    本模块只写元数据）。
    """

    def __init__(self, workdir_path: Path) -> None:
        """以工作目录路径构造门面。调用方负责确保该路径已登记（register 过）。"""
        self._workdir = workdir_path
        self._dsf = ensure_dsf_layout(workdir_path)

    @property
    def dsf_path(self) -> Path:
        """``.dsf/`` 绝对路径。"""
        return self._dsf

    @property
    def state_file(self) -> Path:
        """``.dsf/state.json`` 路径（批次列表 + 重试列表 + 排除名单的唯一可变共享状态）。"""
        return self._dsf / _STATE_FILE_NAME

    @property
    def strategies_dir(self) -> Path:
        """``.dsf/strategies/`` 路径（每套策略一份快照全文）。"""
        return self._dsf / "strategies"

    @property
    def runs_dir(self) -> Path:
        """``.dsf/runs/`` 路径（每次运行一个子目录）。"""
        return self._dsf / "runs"

    @property
    def imports_file(self) -> Path:
        """``.dsf/imports.jsonl`` 路径（append-only 导入记录）。"""
        return self._dsf / "imports.jsonl"

    @property
    def tmp_dir(self) -> Path:
        """``.dsf/tmp/`` 暂存目录（复制导入的临时落点，写完原子改名进工作目录）。"""
        return self._dsf / "tmp"

    def read_state(self) -> dict[str, object]:
        """读 ``state.json``。不存在或空 → 空状态字典（不含任何键）。

        读不需要状态锁：写入侧只有 ``mutate_state`` 一个入口 + 原子写，读到的
        要么是完整旧版、要么是完整新版，不存在半截文件。文件损坏抛
        WorkdirMetadataCorruptedError（fail loud——坏文件不静默兜底）。
        """
        if not self.state_file.exists():
            return {}
        try:
            raw = self.state_file.read_text(encoding="utf-8")
            return json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError) as exc:
            raise WorkdirMetadataCorruptedError(
                f"工作目录状态文件损坏（{self.state_file}），请重建或删除该文件后重试。",
            ) from exc

    def mutate_state[T](self, mutator: Callable[[dict[str, object]], T]) -> T:
        """``state.json`` 的唯一写入口：读—改—写三步全在状态锁内完成。

        调用方只交一个 mutator：**原地修改**传入的 state 字典（不要整体替换——
        落盘的始终是传入的这个字典），返回值原样透传给调用方（新 state 本身或
        需要带出的值，如建批分配到的序号）。相比「暴露一个锁上下文管理器让大家
        自己包」，单一写入口结构性地杜绝漏包——写点分散在 strategies 与 runs
        两个域，靠自觉必然漏（Web 与 CLI 是两个进程，原子写防不住跨进程的
        读—读—写—写丢更新，见 design「并发保护」节）。

        非状态资源的 IO（如建批先写快照文件）留在锁外由调用方自己安排——
        临界区越短，排队越不可感。

        Args:
            mutator: 拿到旧 state、原地修改、返回透传值的函数。mutator 内抛出的
                异常原样冒泡（锁随 finally 释放），state.json 保持改前原样。

        Returns:
            mutator 的返回值。
        """
        lock = StateLock(self._dsf)
        lock.acquire()
        try:
            state = self.read_state()
            result = mutator(state)
            atomic_write_text(
                self.state_file,
                json.dumps(state, ensure_ascii=False, indent=2),
            )
            return result
        finally:
            lock.release()

    def export_directory(self) -> Path:
        """持运行锁的调用方准备交付目录，清理上次异常退出的临时包。"""
        directory = self.dsf_path / "export"
        directory.mkdir(exist_ok=True)
        for temporary in directory.glob(".dsf-export-*.tmp"):
            if temporary.is_file() or temporary.is_symlink():
                temporary.unlink()
        return directory

    def quarantine_paths(self, paths: list[Path]) -> Path | None:
        """暂存整份清理选择；移动失败时回滚，遇到占位则保留暂存副本。"""
        if not paths:
            return None
        sources = list(dict.fromkeys(path.absolute() for path in paths))
        for path in sources:
            self.validate_cleanup_path(path)
            if not path.exists():
                raise WorkdirPathError("清理文件已变化，请刷新清单后重试。")
            if any(other != path and path.is_relative_to(other) for other in sources):
                raise WorkdirPathError("清理选择包含嵌套目录，请重新选择。")
        recovery = self._dsf / "trash" / secrets.token_hex(12)
        self.validate_cleanup_path(recovery)
        recovery.mkdir(parents=True)
        moved: list[tuple[Path, Path]] = []
        try:
            for source in sources:
                self.validate_cleanup_path(source)
                destination = recovery / source.relative_to(self._workdir.absolute())
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
                moved.append((source, destination))
        except (OSError, WorkdirPathError) as exc:
            remaining: list[str] = []
            for source, destination in reversed(moved):
                try:
                    self.validate_cleanup_path(source)
                    if os.path.lexists(source):
                        remaining.append(str(source))
                        continue
                    if destination.is_file():
                        os.link(destination, source)
                        destination.unlink()
                    else:
                        destination.rename(source)
                except (OSError, WorkdirPathError):
                    remaining.append(str(source))
            if remaining:
                raise WorkdirPathError(
                    f"清理未完成，部分文件未能回滚；请检查原位置及暂存目录 {recovery}。"
                ) from exc
            raise WorkdirPathError(
                "清理未完成，文件已回滚，请检查权限后重试。"
            ) from exc
        return recovery

    def validate_cleanup_path(self, path: Path) -> None:
        """清理只接受目录内的真实子路径，拒绝链接、目录联接与根目录本身。"""
        root = self._workdir.absolute()
        candidate = path.absolute()
        if candidate == root or not candidate.is_relative_to(root):
            raise WorkdirPathError("清理路径必须是工作目录的子路径。")
        resolved = candidate.resolve()
        if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
            raise WorkdirPathError("清理路径越出工作目录，请检查链接。")
        while candidate != root:
            if candidate.is_symlink() or candidate.is_junction():
                raise WorkdirPathError("清理路径包含链接，请检查后重试。")
            candidate = candidate.parent

    def finish_cleanup(self, recovery: Path | None) -> str | None:
        """删除本次暂存文件，失败时返回残留位置供调用方明确呈现。"""
        if recovery is None:
            return None
        self.validate_cleanup_path(recovery)
        if recovery.parent != self._dsf / "trash":
            raise WorkdirPathError("清理暂存目录不合法。")
        try:
            shutil.rmtree(recovery)
        except OSError:
            return str(recovery)
        return None

    def append_import_record(self, record: dict[str, object]) -> None:
        """追加一条导入记录到 ``imports.jsonl``（append-only，一行一次导入）。

        写入后 flush + fsync 才算落盘；进程在写入中途被杀最多留下残缺尾行，
        读取侧忽略残缺尾行；下一次追加在导入锁内截去该未完成行，完整历史不变。

        Args:
            record: 序列化为一条 JSON 的导入记录（imported_at / source / files）。

        Raises:
            OSError: 打开 / 写入 / 刷盘失败。
        """
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with import_guard(self.dsf_path), self.imports_file.open("a+b") as handle:
            handle.seek(0)
            raw = handle.read()
            if raw and not raw.endswith(b"\n"):
                handle.truncate(raw.rfind(b"\n") + 1)
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def read_import_records(self) -> list[dict[str, object]]:
        """读全部导入记录（追加序 = 时间正序）。

        崩溃安全读法（与 sessions 事件流一致）：文件末尾无换行 = 写到一半的残缺行，
        砍掉残缺尾巴再解析；中间的完整行损坏则 fail loud（坏文件不静默兜底），
        缺失字段或类型不对同样按损坏处理。

        Returns:
            导入记录列表，每条含 imported_at（str）/ source（str）/ files（list[dict]）。

        Raises:
            WorkdirMetadataCorruptedError: 记录文件损坏（JSON 不合法或形状不对）。
        """
        if not self.imports_file.exists():
            return []
        try:
            raw = self.imports_file.read_bytes()
        except OSError as exc:
            raise WorkdirMetadataCorruptedError(
                f"导入记录文件无法读取（{self.imports_file}）：{exc}",
            ) from exc
        if raw and not raw.endswith(b"\n"):
            raw = raw[: raw.rfind(b"\n") + 1]
        records: list[dict[str, object]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                data: object = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise WorkdirMetadataCorruptedError(
                    f"导入记录文件损坏（{self.imports_file}）——"
                    "可删除该文件后用「重建导入记录」补记，或修复后重试。",
                ) from exc
            self._validate_import_record(data)
            records.append(cast("dict[str, object]", data))
        return records

    @staticmethod
    def _validate_import_record(data: object) -> None:
        """校验一条导入记录的形状（fail loud：形状不对按文件损坏处理）。

        Raises:
            WorkdirMetadataCorruptedError: 不是对象、缺字段或字段类型不对。
        """

        def _corrupted() -> WorkdirMetadataCorruptedError:
            return WorkdirMetadataCorruptedError(
                "导入记录文件里有一条记录形状不对（缺字段或类型不符）——"
                "可删除该文件后用「重建导入记录」补记，或修复后重试。",
            )

        if not isinstance(data, dict):
            raise _corrupted()
        record = cast("dict[str, object]", data)
        if record.get("kind", "import") not in ("import", "rebuild"):
            raise _corrupted()
        if not isinstance(record.get("imported_at"), str):
            raise _corrupted()
        if not isinstance(record.get("source"), str):
            raise _corrupted()
        files = record.get("files")
        if not isinstance(files, list):
            raise _corrupted()
        for item in cast("list[object]", files):
            if not isinstance(item, dict):
                raise _corrupted()
            entry = cast("dict[str, object]", item)
            if not isinstance(entry.get("name"), str) or not isinstance(
                entry.get("sha256"), str
            ):
                raise _corrupted()
