"""两把锁的原语：运行锁（``run.lock``）+ 状态锁（``state.lock``）。

分工（design「并发保护（两把锁）」节 + ADR「运行锁与状态锁分离」）：两件事的
持有时长差五个数量级，各按自己的临界区定策略——

- **运行锁**：语义「这个工作目录有没有跑批在跑」，持有者 = 跑批调度线程、
  时长 = 整场运行；抢锁用**非阻塞单次尝试**，抢不到即拒绝并抛
  ``RunOccupiedError``（占用者信息来自 ``run-info.json``）。
- **状态锁**：语义「``state.json`` 的读—改—写互斥」，持有者 = 任何状态写者、
  时长 = 毫秒级临界区；抢锁**阻塞等待**（宽超时只用于诊断卡死——临界区
  只有毫秒级，排队正是目的，不该把用户挡回去）。

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

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout

from .._fs import atomic_write_text
from .errors import RunOccupiedError, StateLockTimeoutError

__all__ = ["RunLock", "StateLock", "read_occupier"]

logger = logging.getLogger(__name__)

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
    key = os.path.realpath(path)
    with _SHARED_LOCKS_GUARD:
        lock = _SHARED_LOCKS.get(key)
        if lock is None:
            lock = FileLock(Path(key), timeout=-1)
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

        释放顺序刻意先锁后文件：锁释放是必须成功的主体；run-info 删除失败只留
        残留（下次抢锁成功后覆盖），绝不能反过来让文件清理失败吞掉锁释放。
        """
        if not self._acquired:
            return
        try:
            self._lock.release()
        finally:
            self._acquired = False
            try:
                self._info_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "run-info.json 清理失败（残留文件将在下次抢锁时覆盖）：%s",
                    self._info_path,
                    exc_info=True,
                )


class StateLock:
    """一个工作目录的状态锁：阻塞抢锁（宽超时只用于诊断卡死）。

    只护 ``state.json`` 的读—改—写毫秒级临界区——正常用法是经
    ``WorkdirStore.mutate_state`` 间接使用，本类不单独暴露给业务调用方。
    """

    def __init__(
        self, dsf_path: Path, timeout: float = _STATE_LOCK_TIMEOUT_SECONDS
    ) -> None:
        """以 ``.dsf/`` 路径构造；timeout 为等锁宽超时（秒）。"""
        self._lock = _shared_file_lock(dsf_path / _STATE_LOCK_NAME)
        self._timeout = timeout
        self._acquired = False

    def acquire(self) -> None:
        """阻塞等待抢锁；等满宽超时仍未到手 = 异常卡死，抛 ``StateLockTimeoutError``。

        Raises:
            StateLockTimeoutError: 宽超时内没等到锁（诊断信号，不是常规拒绝路径）。
        """
        try:
            self._lock.acquire(timeout=self._timeout)
        except Timeout as exc:
            raise StateLockTimeoutError(
                f"等待工作目录状态锁超时（{self._timeout:g} 秒）——"
                "state.json 的改动临界区只有毫秒级，等满说明有进程异常卡住，"
                "请检查是否有残留的异常进程后重试。",
            ) from exc
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
