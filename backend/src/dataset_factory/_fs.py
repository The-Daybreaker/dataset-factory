"""跨模块共享的底层存储与内容指纹工具：数据根定位 + 原子写 + 文件哈希 + 规范 JSON 哈希 + 路径段判定。

llm（配置与密钥）、prompts、skills、sessions 都要「按数据根存文件、且崩溃不留半个损坏
文件」。把这两件 correctness-critical、与具体数据域无关的事收敛到一处，各域复用、只负责
把自己的错误消息套上去——本模块只抛标准 OSError / UnicodeEncodeError，不掺任何域专属异常
（否则会反向依赖某个数据域，破坏分层）。

- 内容指纹：`hash_file`（整文件 SHA-256）与 `canonical_sha256`（键排序 JSON 的 SHA-256）
  全项目只此一份口径——快照哈希、策略内容哈希、导入记录哈希必须能互相对得上；
- 名字能不能当目录里的一个文件名，统一由 `is_single_path_segment` 判（各域再按自己的
  严格程度追加禁字符，见调用点）；
- 数据根：环境变量 DATASET_FACTORY_HOME 覆盖，否则 ~/.dataset_factory；
- 原子写：同目录临时文件 + fsync + os.replace（POSIX 下再刷父目录项），外界要么看到
  旧文件、要么看到新文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
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
        # Windows 的短暂读取句柄可能未共享删除权限；重试同一份完整临时文件。
        for attempt in range(6):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.01 * 2**attempt)
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


_NUL = chr(0)  # 源码里写 NUL 转义易被工具链吞成裸字节，这里按码位取。


def hash_file(path: Path) -> str:
    """一个文件的 SHA-256（`hashlib.file_digest`：C 级分块读，不把整文件拉进内存）。

    Args:
        path: 要哈希的文件。

    Returns:
        小写十六进制摘要。

    Raises:
        OSError: 文件打不开或读失败。
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def canonical_sha256(data: object) -> str:
    """结构化内容的规范哈希：键排序、保留非 ASCII 的 JSON，再取 SHA-256。

    快照与库策略内容共用这一份口径——两侧各自实现时，一处改了排序或编码，
    「从库更新」的就地比对就会静默失真。

    Args:
        data: 可 JSON 序列化的结构（dict / list / 标量）。

    Returns:
        小写十六进制摘要。
    """
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_single_path_segment(name: str) -> bool:
    """这个名字能不能当「某个目录里的一个文件名」用。

    三件底线：不含 NUL、不含两种路径分隔符、且 ``Path(name).name`` 不改变它（挡掉
    ``a/b``、绝对路径）。各域若要更严（空名、Windows 禁 ``<>:"|?*`` 与控制字符等），
    在本判定之上再加自己的字符集——判定集合与替换前逐字一致，不顺手收紧。

    Args:
        name: 待判的名字。

    Returns:
        是单段安全名字返回 True。
    """
    return (
        _NUL not in name
        and "/" not in name
        and "\\" not in name
        and Path(name).name == name
    )
