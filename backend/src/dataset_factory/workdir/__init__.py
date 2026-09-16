"""workdir 数据域：工作目录注册表 + ``.dsf/`` 门面 + 素材导入（二期新增）。

公开面：WorkdirEntry / WorkdirRegistry（注册表）/ WorkdirStore（``.dsf/`` 唯一写者门面）
/ ensure_dsf_layout / import_assets（素材导入：窄清单 + 大小护栏 + 重复判定 + 登记）
/ 类型化异常（errors）。
"""

from .errors import (
    ImportInProgressError,
    ImportSourceConflictError,
    WorkdirError,
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
)
from .importer import (
    ASSET_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    REASON_OVERSIZE,
    REASON_UNSUPPORTED_EXTENSION,
    ensure_importable_source,
    import_assets,
)
from .store import (
    WorkdirEntry,
    WorkdirRegistry,
    WorkdirStore,
    ensure_dsf_layout,
)

__all__ = [
    "ASSET_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MAX_IMAGE_BYTES",
    "MAX_VIDEO_BYTES",
    "REASON_OVERSIZE",
    "REASON_UNSUPPORTED_EXTENSION",
    "ImportInProgressError",
    "ImportSourceConflictError",
    "WorkdirEntry",
    "WorkdirError",
    "WorkdirMetadataCorruptedError",
    "WorkdirNotFoundError",
    "WorkdirPathError",
    "WorkdirRegistry",
    "WorkdirStore",
    "ensure_dsf_layout",
    "ensure_importable_source",
    "import_assets",
]
