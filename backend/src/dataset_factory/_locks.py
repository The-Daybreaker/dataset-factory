"""层中立共享文件锁注册表：同一把物理锁文件在全进程只有一个 ``FileLock`` 实例。

为什么需要它（缺陷修复，不是风格偏好）：filelock 的可重入计数**按实例**记账，
而 Windows 的 ``LockFileEx`` 按句柄记账——同一线程拿两个不同实例去锁同一个文件，
第二个 ``acquire()`` 会自己把自己挡住（Windows + filelock 3.32.6 实测：3 秒超时都拿不到）。
只要有一条调用链上先后两处各自 ``FileLock(path)``，就会当场卡死到超时。

按 realpath 共享实例后：同线程重入走计数、跨进程与跨线程互斥由 OS 锁本身保证，
两个语义都成立。key 用 realpath 而非字面路径：符号链接与 Windows 盘符大小写差异
必须解析成同一把锁，否则「看起来不同、物理相同」的实例照样自锁。

放在与 `_fs` / `_clock` / `_obs` 同层的本模块，是因为多个功能域都要用它，
而它们之间不许互相依赖（import-linter 的「数据域同层互相独立」契约）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from filelock import FileLock

#: 注册表（key = 锁文件 realpath）。进程生命周期内只增不减：
#: 条目是「每把物理锁一个小对象」，锁文件数量级很小，无需淘汰。
_SHARED_LOCKS: dict[str, FileLock] = {}
_SHARED_LOCKS_GUARD = threading.Lock()


def shared_file_lock(path: Path) -> FileLock:
    """按 realpath 取共享 ``FileLock`` 实例（同一物理锁文件全进程一个实例）。

    Args:
        path: 锁文件路径（不必存在；realpath 只用于归一 key）。

    Returns:
        共享实例——默认超时 -1（无限等），单次等待时限由调用方在 ``acquire`` 处给。
    """
    key = os.path.normcase(os.path.realpath(path))
    with _SHARED_LOCKS_GUARD:
        lock = _SHARED_LOCKS.get(key)
        if lock is None:
            lock = FileLock(Path(key), timeout=-1, preserve_lock_file=True)
            _SHARED_LOCKS[key] = lock
        return lock
