"""工作目录锁原语：运行锁、状态锁与导入锁。

分工（design「并发保护（两把锁）」节 + ADR「运行锁与状态锁分离」）：两件事的
持有时长差五个数量级，各按自己的临界区定策略——

- **运行锁**：语义「这个工作目录有没有跑批在跑」，持有者 = 跑批调度线程、
  时长 = 整场运行；抢锁用**非阻塞单次尝试**，抢不到即拒绝并抛
  ``RunOccupiedError``（占用者信息来自 ``run-info.json``）。
- **状态锁**：语义「``state.json`` 的读—改—写互斥」，持有者 = 任何状态写者、
  时长 = 毫秒级临界区；抢锁**阻塞等待**（宽超时只用于诊断卡死——临界区
  只有毫秒级，排队正是目的，不该把用户挡回去）。
- **导入锁**：保护导入与重建的完整扫描和登记过程，非阻塞竞争；与运行锁独立，
  允许运行期间补充导入，追加记录在同线程内可重入。

选型依据（两把锁同一选型）：文件锁由操作系统在进程终止时随句柄自动释放——
进程无论正常退出、崩溃还是被强杀，都不需要人工清理；「锁文件存在即占用」
方案在强杀后留下永久死锁，不取。

通用纪律：

- **锁序 ``run.lock`` → ``state.lock``，绝不反向**（跑批先持运行锁，收尾出列时
  经 ``WorkdirStore.mutate_state`` 短暂取状态锁）。单向就不成环。
- **锁实例按 realpath 共享、不每次新建**：filelock 的可重入计数按实例记账，
  而 Windows 的 ``LockFileEx`` 按句柄记账——同一线程拿两个不同实例去锁同一个
  文件会自己把自己锁死。实例按 realpath 共享后，同线程重入、跨线程互斥都由
  单实例保证（filelock 3.32 实测核实：可重入计数与句柄按实例 + 线程本地记账，
  跨线程互斥由 OS 锁本身成立）。
- ``run.lock`` / ``state.lock`` 都只表达占用与否、**锁内不写业务数据**；运行锁的
  占用者信息写 ``run-info.json``（启动时写、结束时删），残留无需人工处理——
  下次抢锁成功后直接覆盖。

库侧只发记录不配输出，应用（入口层）负责日志配置——本模块只 ``getLogger``。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout

from .._fs import atomic_write_text, data_root
from .errors import (
    ImportInProgressError,
    RunOccupiedError,
    StateLockTimeoutError,
    WorkdirPathError,
)

__all__ = ["RunLock", "StateLock", "import_guard", "read_occupier"]

logger = logging.getLogger(__name__)


def require_workdir_writable(dsf_path: Path) -> None:
    """拒绝继续写入已经搬迁的旧目录，标记内容不参与路径解析。"""
    if os.path.lexists(dsf_path / "MIGRATED"):
        raise WorkdirPathError("工作目录已搬迁，请刷新工作目录列表后重试。")
    record = maintenance_record(dsf_path.parent)
    if not record.exists():
        return
    with maintenance_guard(dsf_path.parent), registry_guard(data_root()):
        if not record.exists():
            return
        try:
            raw: object = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkdirPathError("搬迁记录无法读取，请检查记录文件后重试。") from exc
        if not isinstance(raw, dict):
            raise WorkdirPathError("搬迁记录格式不正确，请检查记录文件后重试。")
        payload = cast(dict[str, object], raw)
        if payload.get("operation") == "delete" and payload.get("status") != "cleaned":
            raise WorkdirPathError("工作目录正在删除或删除尚未完成，请重试删除。")
        if payload.get("status") == "aborted":
            return
        if payload.get("status") == "cleaned" and dsf_path.parent.is_dir():
            current_identity = dsf_path.parent.stat()
            if (current_identity.st_dev, current_identity.st_ino) != (
                payload.get("source_device"),
                payload.get("source_inode"),
            ):
                return
        if payload.get("status") in ("prepared", "copying"):
            # 延迟导入避免锁原语与注册表门面在模块装载时循环依赖。
            from .store import WorkdirRegistry

            wid = payload.get("wid")
            if isinstance(wid, str):
                current = WorkdirRegistry.get(wid)
                if os.path.normcase(os.path.realpath(current.path)) == os.path.normcase(
                    os.path.realpath(dsf_path.parent)
                ):
                    payload["status"] = "aborted"
                    atomic_write_text(record, json.dumps(payload, ensure_ascii=False))
                    return
        raise WorkdirPathError("工作目录已搬迁，请刷新工作目录列表后重试。")


def maintenance_record(workdir: Path) -> Path:
    """按实际路径定位维护记录，目录搬走后旧对象仍查得到。"""
    identity = os.path.normcase(os.path.realpath(workdir)).encode("utf-8")
    return (
        data_root()
        / "workdir-maintenance"
        / (hashlib.sha256(identity).hexdigest() + ".json")
    )


@contextmanager
def maintenance_guard(workdir: Path, *, timeout: float = 0) -> Generator[None]:
    """串行化同一源目录的搬迁及旧位置清理，锁不随源目录删除。"""
    path = maintenance_record(workdir).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _shared_file_lock(path)
    try:
        lock.acquire(timeout=timeout)
    except Timeout as exc:
        raise RunOccupiedError("工作目录正在维护，请等待完成后重试。") from exc
    try:
        yield
    finally:
        lock.release()


def workdir_write[**P, T](
    operation: Callable[P, T],
) -> Callable[P, T]:
    """保护跨多个文件的短写入操作，搬迁必须等整个操作完成后才能复制。"""
    signature = inspect.signature(operation)
    path_parameter = next(iter(signature.parameters))

    @wraps(operation)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        workdir = cast(Path, signature.bind(*args, **kwargs).arguments[path_parameter])
        with maintenance_guard(workdir, timeout=10):
            require_workdir_writable(workdir / ".dsf")
            return operation(*args, **kwargs)

    return guarded


@contextmanager
def registry_guard(directory: Path) -> Generator[None]:
    """保护全局注册表的读改写，锁放数据根而非将被搬迁的工作目录内。"""
    directory.mkdir(parents=True, exist_ok=True)
    lock = _shared_file_lock(directory / "workdirs.lock")
    try:
        lock.acquire(timeout=10)
    except Timeout as exc:
        raise StateLockTimeoutError("等待工作目录注册表锁超时，请稍后重试。") from exc
    try:
        yield
    finally:
        lock.release()


@contextmanager
def import_guard(
    dsf_path: Path, *, directory_identity: tuple[int, int] | None = None
) -> Generator[None]:
    """串行化目录内导入与重建，覆盖 CLI 和多个 HTTP 服务进程。

    导入会先扫描再写登记集合，必须保护整段操作而不只保护追加一行。
    与运行锁独立，补充导入不阻塞已经开始的跑批。
    """
    lock = _shared_file_lock(dsf_path / "imports.lock")
    try:
        if lock.is_locked:
            lock.acquire(timeout=0)
        else:
            with maintenance_guard(dsf_path.parent):
                require_workdir_writable(dsf_path)
                require_directory_identity(dsf_path.parent, directory_identity)
                lock.acquire(timeout=0)
    except Timeout as exc:
        raise ImportInProgressError(
            "该工作目录已有导入或重建任务，请等待完成后重试。"
        ) from exc
    try:
        yield
    finally:
        lock.release()


def require_directory_identity(path: Path, identity: tuple[int, int] | None) -> None:
    """过期对象不能写入同路径下后来创建的另一个目录。"""
    if identity is None:
        return
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise WorkdirPathError("工作目录不存在，请刷新工作目录列表后重试。") from exc
    if (info.st_dev, info.st_ino) != identity:
        raise WorkdirPathError("工作目录已被替换，请刷新工作目录列表后重试。")


_RUN_LOCK_NAME = "run.lock"
_RUN_INFO_NAME = "run-info.json"
_STATE_LOCK_NAME = "state.lock"

#: 状态锁的宽超时（秒）：临界区毫秒级，正常永远不会等满；等满 = 异常卡死的
#: 诊断信号。构造参数可注入更短的值（测试与特殊调用方用）。
_STATE_LOCK_TIMEOUT_SECONDS: float = 10.0

#: 共享锁实例注册表（key = 锁文件 realpath）。进程生命周期内只增不减：
#: 条目是「每把物理锁一个小对象」，工作目录数量级很小，无需淘汰。
_SHARED_LOCKS: dict[str, FileLock] = {}
_SHARED_LOCKS_GUARD = threading.Lock()


def _shared_file_lock(path: Path) -> FileLock:
    """按 realpath 取共享 ``FileLock`` 实例（同一物理锁文件全进程一个实例）。

    用 realpath 而不是字面路径做 key：符号链接与盘符大小写差异必须解析成
    同一把锁，否则同线程两个「看起来不同、物理相同」的实例照样自锁。
    """
    key = os.path.normcase(os.path.realpath(path))
    with _SHARED_LOCKS_GUARD:
        lock = _SHARED_LOCKS.get(key)
        if lock is None:
            lock = FileLock(Path(key), timeout=-1, preserve_lock_file=True)
            _SHARED_LOCKS[key] = lock
        return lock


class RunLock:
    """一个工作目录的运行锁：非阻塞抢锁 + run-info 写读清（同一个工作目录一把锁）。"""

    def __init__(self, dsf_path: Path) -> None:
        """以 ``.dsf/`` 路径构造（锁文件与占用者文件都落在这里）。"""
        self._lock = _shared_file_lock(dsf_path / _RUN_LOCK_NAME)
        self._info_path = dsf_path / _RUN_INFO_NAME
        self._acquired = False

    def acquire(self, occupier: dict[str, Any]) -> None:
        """非阻塞尝试抢锁；抢到后写占用者信息（覆盖可能的残留）。

        占用者信息写盘失败只记警告、不影响锁的持有——它是给人看的提示，
        不能成为「锁已到手却抛异常」的泄漏路径（调用方据此保证 release 恒执行）。

        Raises:
            RunOccupiedError: 已被占用（occupier 来自现存的 run-info.json；缺失或
                损坏时为 None，提示退回笼统文案）。
        """
        try:
            with maintenance_guard(self._info_path.parent.parent):
                require_workdir_writable(self._info_path.parent)
                self._lock.acquire(timeout=0)
        except Timeout as exc:
            existing = read_occupier(self._info_path)
            detail = (
                "工作目录正被另一个跑批运行占用。"
                if existing is None
                else _occupier_sentence(existing)
            )
            raise RunOccupiedError(detail, occupier=existing) from exc
        self._acquired = True
        try:
            atomic_write_text(
                self._info_path, json.dumps(occupier, ensure_ascii=False, indent=2)
            )
        except OSError:
            logger.warning(
                "run-info.json 写入失败（占用者提示将缺失，不影响本次运行）：%s",
                self._info_path,
                exc_info=True,
            )

    def release(self) -> None:
        """释放锁并清掉占用者信息（未持锁时调用是安全空操作）。

        占用者信息在锁内清理，避免删掉下一位持有者的提示；无论清理是否成功，
        finally 都释放系统锁。
        """
        if not self._acquired:
            return
        try:
            try:
                self._info_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "run-info.json 清理失败（残留文件将在下次抢锁时覆盖）：%s",
                    self._info_path,
                    exc_info=True,
                )
        finally:
            self._acquired = False
            self._lock.release()


class StateLock:
    """一个工作目录的状态锁：阻塞抢锁（宽超时只用于诊断卡死）。

    只护 ``state.json`` 的读—改—写毫秒级临界区——正常用法是经
    ``WorkdirStore.mutate_state`` 间接使用，本类不单独暴露给业务调用方。
    """

    def __init__(
        self,
        dsf_path: Path,
        timeout: float = _STATE_LOCK_TIMEOUT_SECONDS,
        *,
        directory_identity: tuple[int, int] | None = None,
    ) -> None:
        """以 ``.dsf/`` 路径构造；timeout 为等锁宽超时（秒）。"""
        self._lock = _shared_file_lock(dsf_path / _STATE_LOCK_NAME)
        self._dsf_path = dsf_path
        self._timeout = timeout
        self._directory_identity = directory_identity
        self._acquired = False

    def acquire(self) -> None:
        """阻塞等待抢锁；等满宽超时仍未到手 = 异常卡死，抛 ``StateLockTimeoutError``。

        Raises:
            StateLockTimeoutError: 宽超时内没等到锁（诊断信号，不是常规拒绝路径）。
        """
        if self._lock.is_locked:
            require_directory_identity(self._dsf_path.parent, self._directory_identity)
            self._lock.acquire(timeout=0)
            self._acquired = True
            return
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                with maintenance_guard(self._dsf_path.parent):
                    require_workdir_writable(self._dsf_path)
                    require_directory_identity(
                        self._dsf_path.parent, self._directory_identity
                    )
                    self._lock.acquire(timeout=0)
                break
            except (Timeout, RunOccupiedError) as exc:
                if time.monotonic() >= deadline:
                    raise StateLockTimeoutError(
                        f"等待工作目录状态锁超时（{self._timeout:g} 秒）——"
                        "请等待目录维护完成，或检查是否有残留进程后重试。",
                    ) from exc
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        self._acquired = True

    def release(self) -> None:
        """释放锁（未持锁时调用是安全空操作）。"""
        if not self._acquired:
            return
        self._acquired = False
        self._lock.release()


def read_occupier(info_path: Path) -> dict[str, Any] | None:
    """读占用者信息；文件缺失或损坏返回 None（信息只是给人看的，不该炸拒绝路径）。"""
    if not info_path.is_file():
        return None
    try:
        data: object = json.loads(info_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return cast("dict[str, Any] | None", data) if isinstance(data, dict) else None


def _occupier_sentence(occupier: dict[str, Any]) -> str:
    """把占用者信息拼成一句话（拒绝提示用；字段缺失就少说那句）。"""
    pid = occupier.get("pid")
    started_at = occupier.get("started_at")
    batch = occupier.get("batch")
    parts: list[str] = []
    if pid is not None:
        parts.append(f"进程号 {pid}")
    if started_at is not None:
        parts.append(f"启动于 {started_at}")
    if batch is not None:
        parts.append(f"正在跑批次 {batch}")
    body = "、".join(parts) if parts else "占用者信息不完整"
    return f"工作目录正被另一个跑批运行占用（{body}）；等它结束或停止后再试。"
