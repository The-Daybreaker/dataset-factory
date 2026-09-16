"""workdir 数据域：工作目录注册表 + ``.dsf/`` 门面（二期新增）。

公开面：WorkdirEntry / WorkdirRegistry（注册表）/ WorkdirStore（``.dsf/`` 唯一写者门面）
/ ensure_dsf_layout / 类型化异常（errors）。
"""

from .errors import (
    WorkdirError,
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
)
from .store import (
    WorkdirEntry,
    WorkdirRegistry,
    WorkdirStore,
    ensure_dsf_layout,
)

__all__ = [
    "WorkdirEntry",
    "WorkdirError",
    "WorkdirMetadataCorruptedError",
    "WorkdirNotFoundError",
    "WorkdirPathError",
    "WorkdirRegistry",
    "WorkdirStore",
    "ensure_dsf_layout",
]
