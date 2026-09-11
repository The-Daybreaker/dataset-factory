"""跨模块共享的底层存储工具：数据根定位 + 原子写。

llm（配置与密钥）、prompts、skills、sessions 都要「按数据根存文件、且崩溃不留半个损坏
文件」。把这两件 correctness-critical、与具体数据域无关的事收敛到一处，各域复用、只负责
把自己的错误消息套上去——本模块只抛标准 OSError / UnicodeEncodeError，不掺任何域专属异常
（否则会反向依赖某个数据域，破坏分层）。

- 数据根：环境变量 DATASET_FACTORY_HOME 覆盖，否则 ~/.dataset_factory；
- 原子写：同目录临时文件 + fsync + os.replace（POSIX 下再刷父目录项），外界要么看到
  旧文件、要么看到新文件。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

ENV_HOME = "DATASET_FACTORY_HOME"  # 数据根覆盖（默认 ~/.dataset_factory）

_HOME_DIRNAME = ".dataset_factory"


def data_root() -> Path:
    """数据根目录：环境变量 DATASET_FACTORY_HOME 覆盖，否则 ~/.dataset_factory。"""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / _HOME_DIRNAME


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本文件：先编码成字节（编码错误在碰磁盘之前就暴露），再走 `atomic_write_bytes`。

    Args:
        path: 目标文件路径。
        text: 要写入的文本内容。

    Raises:
        OSError: 临时文件创建 / 写入 / 刷盘 / 改名失败。
        UnicodeEncodeError: 内容含 UTF-8 无法编码的字符（如孤立代理项）。
    """
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写字节文件：同目录临时文件 + fsync + os.replace + 刷父目录项。

    先把内容写进同目录的临时文件、刷盘，再用 os.replace 改名成目标文件——外界要么看到
    旧文件、要么看到新文件，绝不会看到写了一半的损坏文件。几点关键：

    - 临时文件必须和目标同目录：os.replace 的原子性只在同一文件系统内成立；
    - 用 os.replace 而非 os.rename：Windows 上目标已存在时 rename 会失败，replace
      两平台都能原子覆盖；
    - tempfile.mkstemp 在 Unix 上默认以 0600 建临时文件（仅本人可读写）；Windows 靠
      所在目录的 ACL 隔离；
    - 改名成功后刷一次父目录项（POSIX）：掉电时 os.replace 的改名本身也可能丢，刷目录
      把这条 rename 也钉进盘。Windows 无法以文件描述符打开目录，按 no-op 跳过（NTFS
      的元数据一致性由系统保证）。

    调用方须先确保 path.parent 已存在（自行 mkdir，以便区分「建目录失败」与「写文件
    失败」给出各自的错误消息）。

    Args:
        path: 目标文件路径。
        data: 要写入的字节内容。

    Raises:
        OSError: 临时文件创建 / 写入 / 刷盘 / 改名失败。
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        # 兜底清理：改名成功后临时文件已不存在（missing_ok 即 no-op）；任何失败路径
        # （含非 OSError 的编码错误）都清掉它，绝不留下垃圾临时文件。
        tmp_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """尽量刷目录项（best effort）：Windows 打不开目录的文件描述符，按 no-op 处理。"""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
