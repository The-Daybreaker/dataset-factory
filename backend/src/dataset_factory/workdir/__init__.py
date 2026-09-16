"""workdir 数据域：工作目录注册表 + ``.dsf/`` 门面 + 素材导入与素材清单（二期新增）。

公开面：WorkdirEntry / WorkdirRegistry（注册表）/ WorkdirStore（``.dsf/`` 唯一写者门面
+ ``mutate_state`` 状态唯一写入口）/ ensure_dsf_layout / import_assets（素材导入：
窄清单 + 大小护栏 + 重复判定 + 登记）/ 素材清单与身份解析（assets：扫描、出身、
产物命名、未导入分类、预览用的 confine 校验）/ 两把锁原语（locks：RunLock 运行锁 +
StateLock 状态锁，runs 与 workdir 的破坏性目录操作共用）/ 类型化异常（errors）。
"""

from .assets import (
    REASON_UNREGISTERED,
    ImportOrigin,
    UnimportedFile,
    confine_to_workdir,
    is_product_name,
    media_kind,
    mime_for_suffix,
    product_filename,
    product_has_content,
    product_path,
    product_pattern,
    registered_origins,
    resolve_asset,
    scan_assets,
    unimported_files,
)
from .errors import (
    AssetNotFoundError,
    AssetPathError,
    ImportInProgressError,
    ImportSourceConflictError,
    ProductNotFoundError,
    RunOccupiedError,
    StateLockTimeoutError,
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
    size_limit,
)
from .locks import RunLock, StateLock, read_occupier
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
    "REASON_UNREGISTERED",
    "REASON_UNSUPPORTED_EXTENSION",
    "AssetNotFoundError",
    "AssetPathError",
    "ImportInProgressError",
    "ImportOrigin",
    "ImportSourceConflictError",
    "ProductNotFoundError",
    "RunLock",
    "RunOccupiedError",
    "StateLock",
    "StateLockTimeoutError",
    "UnimportedFile",
    "WorkdirEntry",
    "WorkdirError",
    "WorkdirMetadataCorruptedError",
    "WorkdirNotFoundError",
    "WorkdirPathError",
    "WorkdirRegistry",
    "WorkdirStore",
    "confine_to_workdir",
    "ensure_dsf_layout",
    "ensure_importable_source",
    "import_assets",
    "is_product_name",
    "media_kind",
    "mime_for_suffix",
    "product_filename",
    "product_has_content",
    "product_path",
    "product_pattern",
    "read_occupier",
    "registered_origins",
    "resolve_asset",
    "scan_assets",
    "size_limit",
    "unimported_files",
]
