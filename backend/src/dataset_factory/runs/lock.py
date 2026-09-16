"""运行锁（filelock 系统级文件锁）与占用者信息（``run-info.json``）。

选型依据（design「并发保护（运行锁）」）：文件锁由操作系统在进程终止时随句柄自动
释放——进程无论正常退出、崩溃还是被强杀，都不需要人工清理；「锁文件存在即占用」
方案在强杀后留下永久死锁，不取。

用法约定（design 定案，本项目全遵守）：
- 抢锁用**非阻塞单次尝试**：抢不到即拒绝并抛 ``RunOccupiedError``（带占用者信息）；
- ``run.lock`` 只表达占用与否、锁内不写业务数据；占用者信息写 ``run-info.json``
  （启动时写、结束时删），两者分离；
- 极端情况下 ``run-info.json`` 残留（锁已释放、文件还在）：下次抢锁成功后直接覆盖；
- 锁归调度线程：持锁跑批与持锁改 state.json 同线程，filelock 同线程可重入、跨线程
  不共享——跨线程持锁属未排期池「批内并发」的事，届时按 design 三条扩展约定处理。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout

from .._fs import atomic_write_text
from .errors import RunOccupiedError

__all__ = ["RunLock", "read_occupier"]

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
        atomic_write_text(
            self._info_path, json.dumps(occupier, ensure_ascii=False, indent=2)
        )

    def release(self) -> None:
        """释放锁并清掉占用者信息（未持锁时调用是安全空操作）。"""
        if not self._acquired:
            return
        self._acquired = False
        self._info_path.unlink(missing_ok=True)
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
