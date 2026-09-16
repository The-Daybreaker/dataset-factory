"""运行锁（filelock 系统级文件锁）与占用者信息（``run-info.json``）。

选型依据（design「并发保护（运行锁）」）：文件锁由操作系统在进程终止时随句柄自动
释放——进程无论正常退出、崩溃还是被强杀，都不需要人工清理；「锁文件存在即占用」
方案在强杀后留下永久死锁，不取。

用法约定（design 定案，本项目全遵守）：
- 抢锁用**非阻塞单次尝试**：抢不到即拒绝并抛 ``RunOccupiedError``（带占用者信息）；
- ``run.lock`` 只表达占用与否、锁内不写业务数据；占用者信息写 ``run-info.json``
  （启动时写、结束时删），两者分离；
- **占用者信息是提示性附属，绝不波及锁生命周期**：写 run-info 失败只损失
  「谁在占用」的提示（409 退回笼统文案），锁照常持有；删除失败留下残留文件，
  下次抢锁成功后直接覆盖（design 既有约定）——这两处防御是「进程持锁期间无论
  发生什么，锁都能正常释放」承诺的一部分；
- 极端情况下 ``run-info.json`` 残留（锁已释放、文件还在）：下次抢锁成功后直接覆盖；
- 锁归调度线程：持锁跑批与持锁改 state.json 同线程，filelock 同线程可重入、跨线程
  不共享——跨线程持锁属未排期池「批内并发」的事，届时按 design 三条扩展约定处理。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout

from .._fs import atomic_write_text
from .errors import RunOccupiedError

__all__ = ["RunLock", "read_occupier"]

logger = logging.getLogger(__name__)

_RUN_LOCK_NAME = "run.lock"
_RUN_INFO_NAME = "run-info.json"


class RunLock:
    """一个工作目录的运行锁：非阻塞抢锁 + run-info 写读清（同一个工作目录一把锁）。"""

    def __init__(self, dsf_path: Path) -> None:
        """以 ``.dsf/`` 路径构造（锁文件与占用者文件都落在这里）。"""
        self._lock = FileLock(dsf_path / _RUN_LOCK_NAME)
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
