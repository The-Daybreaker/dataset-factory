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
