"""workdir 数据域的异常类型：只抛类型化异常，消息可操作、fail loud、不甩原始栈。

二期新端点的错误响应一律 problem+json（api.problems）——异常映射到
type slug 的对应关系写在本模块各类的 docstring 里。
"""

from __future__ import annotations

from typing import Any


class WorkdirError(Exception):
    """工作目录域错误的基类；消息只描述「哪里错、怎么修」。"""


class RunOccupiedError(WorkdirError):
    """同一工作目录已有跑批在运行（运行锁被占用）——HTTP 409 problem+json（run-occupied）。

    Attributes:
        occupier: 占用者信息（pid / started_at / hostname / batch）；残留信息损坏时
            为 None，此时只给笼统提示。
    """

    def __init__(self, message: str, *, occupier: dict[str, Any] | None = None) -> None:
        """带占用者信息构造（occupier 供 problem+json 扩展字段与界面提示）。"""
        super().__init__(message)
        self.occupier = occupier


class StateLockTimeoutError(WorkdirError):
    """状态锁等锁超时（宽超时内没等到 ``state.json`` 的毫秒级临界区）——HTTP 500 problem+json（state-lock-timeout）。

    这是诊断信号不是常规拒绝路径：临界区只有毫秒级，等满宽超时说明有进程
    异常卡住（正常崩溃 / 强杀会让 OS 立即释放锁，不会造成等待）。
    """


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


class AssetNotFoundError(WorkdirError):
    """素材找不到（缺失，或在盘但未登记在册）——HTTP 404 problem+json（asset-not-found）。"""


class ProductNotFoundError(WorkdirError):
    """该批次没有这个条目的可用产物（还没打标，或产物为空 / 读不出）——HTTP 404 problem+json（product-not-found）。

    「读不出」也归这里而不是 500：产物是本工具写的 UTF-8 文本，读不出就等于没有
    可用 caption，条目视图同样把它算作未完成（产物异常、可重打）——两侧一个口径。
    """


class AssetPathError(WorkdirError):
    """条目名解析出的路径越出工作目录（符号链接指向外部 / 名字里带路径分隔符）——HTTP 400 problem+json（asset-path-invalid）。

    预览端点只服务工作目录内的文件：realpath 之后仍必须以工作目录为祖先，
    否则拒绝——这条 confine 校验是「只读端点也不能被拿来读任意文件」的底线。
    """
