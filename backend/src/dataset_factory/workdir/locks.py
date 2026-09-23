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
- **锁实例按 realpath 共享、不每次新建**：注册表在层中立的 :mod:`dataset_factory._locks`
  （`shared_file_lock`），理由与实测都写在那儿；本模块与 skills / prompts 域共用同一份，
  任何一处都不再自己 ``FileLock(path)`` 新建实例。
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
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, cast

from filelock import Timeout

from .._fs import atomic_write_text, data_root
from .._locks import shared_file_lock
from .errors import (
    ImportInProgressError,
    RunOccupiedError,
    StateLockTimeoutError,
    WorkdirMaintenanceError,
    WorkdirPathError,
)

__all__ = [
    "MAINTENANCE_WAIT_SECONDS",
    "RunLock",
    "StateLock",
    "import_guard",
    "read_occupier",
    "sweep_cleaned_maintenance_records",
]

#: 开始一次维护类动作（搬迁 / 删除 / 导入 / 只读统计）时，等维护锁释放的窗口（秒）。
#:
#: 为什么要等：维护锁的持有者有两类、持有时长差三个数量级——跨文件短写入是毫秒级
#: （``workdir_write`` 自己就用 10 秒去抢锁，说明它预期会被短暂占用），而搬迁 / 删除是
#: 秒级以上。开始动作时若零容忍抢锁，一次碰巧并发的短写入就能让用户看到「工作目录正在
#: 搬迁或删除」，而那一刻既没搬迁也没删除——2026-09-20 用 request id 对日志实锤：只读的
#: ``GET .../stats`` 拿到 409（41ms），而同窗口的邻居请求全是 200。
#:
#: 窗口取 5 秒：足以吸收最慢的短写入（实测同进程内写类请求最长约 900ms），又能在真的
#: 搬迁 / 删除时尽快给出那条本来就准确的提示。**默认值仍是 0**——:func:`maintenance_guard`
#: 的既有契约不变，只有「开始一次动作」的调用点显式传这个窗口。
MAINTENANCE_WAIT_SECONDS = 5.0

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
            # cleaned = 旧位置已完整移除（删除 / 搬迁的清理成功后才落此状态）——
            # 路径下再出现的目录必然是重建的新目录，放行。不能用 (st_dev, st_ino)
            # 当目录身份来区分：删后重建会复用刚释放的 inode，新目录会被误判成
            # 旧目录而拒绝登记（CI ubuntu 上 3 跑 2 红的根因；本机 NTFS 的复用
            # 行为不同所以不显现，回归用例见 tests/test_relocation.py）。
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


def sweep_cleaned_maintenance_records() -> int:
    """清扫已完成（cleaned）的维护记录，返回清扫条数；应用启动时调用一次。

    为什么只清 cleaned：维护记录的全部消费方（``require_workdir_writable`` /
    ``relocation_status`` / 搬迁 resume / 删除重试 / 删除预览）对 cleaned 的读法
    要么「cleaned → 放行」（与无记录等价）、要么「跳过」——删除不改变任何行为；
    而 prepared / copying / deleting 等中断态记录承载重试与防呆语义
    （``require_workdir_writable`` 靠它拒绝向中断现场写入），必须保留。

    损坏的记录同样不动：读不出来该由 ``require_workdir_writable`` fail loud
    提示用户处置，清扫器不越权替用户删证据。``.lock`` 文件本体一律不清——
    删除锁文件有经典竞态（等待者持着旧句柄、新进程锁上新文件，互斥破防），
    它是 ``preserve_lock_file`` 设计下的无害残留。

    与并发写者的竞态是良性的：记录走原子写（外界看到完整旧版或完整新版），
    清扫器按内容判定，读到非 cleaned 就不碰；读到 cleaned 后哪怕写者刚把它
    换版，删掉的也是「删了与不删等价」的文件。
    """
    directory = data_root() / "workdir-maintenance"
    swept = 0
    for path in directory.glob("*.json"):
        try:
            record: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        payload = cast(dict[str, object], record)
        if payload.get("status") != "cleaned":
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        swept += 1
    return swept


@contextmanager
def maintenance_guard(workdir: Path, *, timeout: float = 0) -> Generator[None]:
    """串行化同一源目录的搬迁及旧位置清理，锁不随源目录删除。

    抢不到 = 该目录正在被搬迁或删除，抛 ``WorkdirMaintenanceError`` 而不是
    ``RunOccupiedError``——两者都劝用户「等会儿再来」，但占用者不同，提示不能
    张冠李戴（详见该异常类的 docstring）。

    超时怎么给：默认 0 适合「已经在临界区里、确认自己仍持有」的嵌套调用；**开始一次
    动作**（搬迁 / 删除 / 导入 / 只读统计）时应当传 :data:`MAINTENANCE_WAIT_SECONDS`，
    否则会与毫秒级的跨文件短写入撞出假占用（理由与实测见该常量）。
    """
    path = maintenance_record(workdir).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = shared_file_lock(path)
    try:
        lock.acquire(timeout=timeout)
    except Timeout as exc:
        raise WorkdirMaintenanceError(
            "工作目录正在搬迁或删除，请等待完成后重试。"
        ) from exc
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
    lock = shared_file_lock(directory / "workdirs.lock")
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
    lock = shared_file_lock(dsf_path / "imports.lock")
    try:
        if lock.is_locked:
            lock.acquire(timeout=0)
        else:
            # 维护锁给等待窗口：抢它的人可能是毫秒级短写入（理由见常量注释），零容忍会
            # 让只读统计在并发下凭空失败。imports 锁本身仍是「非阻塞竞争即返回」。
            with maintenance_guard(dsf_path.parent, timeout=MAINTENANCE_WAIT_SECONDS):
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


class RunLock:
    """一个工作目录的运行锁：非阻塞抢锁 + run-info 写读清（同一个工作目录一把锁）。"""

    def __init__(self, dsf_path: Path) -> None:
        """以 ``.dsf/`` 路径构造（锁文件与占用者文件都落在这里）。"""
        self._lock = shared_file_lock(dsf_path / _RUN_LOCK_NAME)
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
            with maintenance_guard(self._info_path.parent.parent, timeout=10):
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
        self._lock = shared_file_lock(dsf_path / _STATE_LOCK_NAME)
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
            except (Timeout, WorkdirMaintenanceError) as exc:
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


def read_live_occupier(dsf_path: Path) -> dict[str, Any] | None:
    """只有系统锁仍被持有时才返回占用信息，崩溃残留文件不表示运行中。"""
    with maintenance_guard(dsf_path.parent):
        require_workdir_writable(dsf_path)
        lock = shared_file_lock(dsf_path / _RUN_LOCK_NAME)
        if lock.is_locked:
            return read_occupier(dsf_path / _RUN_INFO_NAME)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return read_occupier(dsf_path / _RUN_INFO_NAME)
        else:
            lock.release()
            return None


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
