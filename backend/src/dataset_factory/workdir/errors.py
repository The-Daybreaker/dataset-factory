"""workdir 数据域的异常类型：只抛类型化异常，消息可操作、fail loud、不甩原始栈。

二期新端点的错误响应一律 problem+json（api.problems）——异常映射到
type slug 的对应关系写在本模块各类的 docstring 里。
"""

from __future__ import annotations


class WorkdirError(Exception):
    """工作目录域错误的基类；消息只描述「哪里错、怎么修」。"""


class WorkdirNotFoundError(WorkdirError):
    """wid 不在注册表中（查 / 改不存在的条目）——HTTP 404 problem+json（workdir-not-found）。"""


class WorkdirPathError(WorkdirError):
    """工作目录路径不合法（不存在、不是目录）——HTTP 400 problem+json（workdir-path-invalid）。"""


class WorkdirMetadataCorruptedError(WorkdirError):
    """注册表或 ``state.json`` 元数据文件损坏（JSON 不合法）——HTTP 500 problem+json（workdir-metadata-corrupted）。"""


class ImportSourceConflictError(WorkdirError):
    """导入来源目录与工作目录相同或互为嵌套（复制会自我覆盖）——HTTP 422 problem+json（import-source-conflict）。"""


class ImportInProgressError(WorkdirError):
    """同一工作目录已有导入任务在跑（并发导入会破坏条目身份不变量）——HTTP 409 problem+json（import-in-progress）。

    detail 携带占用任务的 task_id（与运行锁拒绝时的占用者信息同思路）。
    """
