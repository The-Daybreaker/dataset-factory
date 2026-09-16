"""工作目录里的素材文件：扫描、身份解析、出身登记与产物命名（二期 T37）。

本模块回答三类问题，都是「工作目录里现在有些什么」这一件事的不同切面：

- **扫描**：:func:`scan_assets` 给出「素材主干 → 文件路径」，是条目身份解析的单一
  事实源——跑批定计划、条目视图、素材预览都从这里取，防两处各扫一遍取到不同文件
  （同主干多扩展并存时错位会让哈希比对永久失真）。
- **身份与出身**：条目身份 = 素材主干（不含扩展名）；出身 = 包含它的最近一次导入
  记录（:func:`registered_origins`）。「在册」是批次成员的门槛（M2），素材预览端点
  的三重校验里「wid 存活 → **在册** → realpath confine」的后两重落在
  :func:`resolve_asset`。
- **产物命名**：``s<N>__<素材主干>.txt`` 是训练配对的红线契约（交付必须走打包归一），
  写产物、数产物、删产物、判定「已完成」四处共用同一份格式，集中在此。

只读纪律：本模块不写任何文件（``.dsf/`` 的写入统一走 :class:`~dataset_factory.workdir.store.WorkdirStore`）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..llm import (
    IMAGE_EXTENSIONS,
    IMAGE_MIME_BY_SUFFIX,
    VIDEO_EXTENSIONS,
    VIDEO_MIME_BY_SUFFIX,
)
from .errors import AssetNotFoundError, AssetPathError
from .importer import (
    ASSET_EXTENSIONS,
    REASON_OVERSIZE,
    REASON_UNSUPPORTED_EXTENSION,
    size_limit,
)
from .store import WorkdirStore

__all__ = [
    "ASSET_EXTENSIONS",
    "REASON_OVERSIZE",
    "REASON_UNREGISTERED",
    "REASON_UNSUPPORTED_EXTENSION",
    "ImportOrigin",
    "UnimportedFile",
    "confine_to_workdir",
    "is_product_name",
    "media_kind",
    "mime_for_suffix",
    "product_filename",
    "product_has_content",
    "product_path",
    "product_pattern",
    "registered_origins",
    "resolve_asset",
    "scan_assets",
    "size_limit",
    "unimported_files",
]

#: 「未导入」的第三种原因：文件在白名单内也没超限，只是从没登记过。
#: 与 importer 的两个原因常量同为「字符串即契约」（界面按原因呈现）。
REASON_UNREGISTERED = "未登记"

#: 产物文件的命名契约：``s<N>__<素材主干>.txt``（N 为正整数，主干非空）。
_PRODUCT_NAME_RE = re.compile(r"s([1-9][0-9]*)__.+\.txt")

#: 素材 MIME = 图片表 ∪ 视频表（两份单一事实源都在 llm，此处只做合并）。
_MIME_BY_SUFFIX: dict[str, str] = IMAGE_MIME_BY_SUFFIX | VIDEO_MIME_BY_SUFFIX

#: 未知扩展名的兜底 MIME（按「未知二进制」提供，浏览器不会误当文本渲染）。
_FALLBACK_MIME = "application/octet-stream"

#: 路径分隔符（条目名里出现任何一个都说明它不是「素材主干」而是想拼路径）。
_SEPARATORS = ("/", "\\")


@dataclass(frozen=True)
class ImportOrigin:
    """一份素材的出身：它由哪次导入登记进来。

    Attributes:
        name: 工作目录内的文件名（含扩展名）。
        source: 那次导入的来源目录（就地采用 = 工作目录自身）。
        imported_at: 导入时刻（UTC ISO 8601）。
    """

    name: str
    source: str
    imported_at: str


@dataclass(frozen=True)
class UnimportedFile:
    """工作目录里「不会被当作条目」的一个文件（左列「未导入」分组的一行）。

    Attributes:
        name: 文件名。
        reason: 原因（标准措辞常量：扩展名不支持 / 超出大小上限 / 未登记）。
        size: 文件字节数。
        limit: 该档大小上限；只有超限那一类有值（其余为 None）。
    """

    name: str
    reason: str
    size: int
    limit: int | None


def mime_for_suffix(suffix: str) -> str:
    """按扩展名取对外提供文件时用的 MIME（未知扩展名落 octet-stream）。"""
    return _MIME_BY_SUFFIX.get(suffix.lower(), _FALLBACK_MIME)


def media_kind(name: str) -> str:
    """按扩展名归类媒体形态：``image`` / ``video`` / ``file``（界面选图标用）。"""
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "file"


def product_filename(seq: int, item: str) -> str:
    """产物文件名：``s<N>__<素材主干>.txt``（与素材同目录）。"""
    return f"s{seq}__{item}.txt"


def product_pattern(seq: int) -> str:
    """某批次全部产物的 glob 模式（数产物 / 删批次用）。"""
    return f"s{seq}__*.txt"


def is_product_name(name: str) -> bool:
    """文件名是否长得像本工具的产物（用于把它排除出「未导入」清单）。

    产物是工具自己写进工作目录的，把它列成「未导入 · 扩展名不支持」既荒谬又会
    淹掉真正需要用户处置的文件——判定锚就是上面那条命名契约。
    """
    return _PRODUCT_NAME_RE.fullmatch(name) is not None


def scan_assets(workdir: Path) -> dict[str, Path]:
    """工作目录现状里的素材文件（白名单内、平铺不递归），素材主干 → 路径。

    这是「主干 → 素材文件」解析的**单一事实源**：跑批定计划与实际读取、条目视图、
    素材预览都经此取路径——同主干多扩展并存时各处必须拿到同一份文件，否则 E1 比对
    的哈希与送模型的素材错位、跳过判定永久失真。

    大小护栏不在这一层（运行时读取前另有校验）：扫描要如实反映目录现状，
    超限文件是「在盘上」的，只是打标时会被拦下。
    """
    result: dict[str, Path] = {}
    if not workdir.is_dir():
        return result
    for entry in sorted(workdir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix.lower() not in ASSET_EXTENSIONS:
            continue
        result[Path(entry.name).stem] = entry
    return result


def confine_to_workdir(workdir: Path, candidate: Path) -> Path:
    """确认路径落在工作目录内（realpath 比较），越界即拒。

    为什么用 realpath 而不是字符串前缀：威胁不是 ``..`` 这种字面穿越（那在路由与
    名字校验层就挡住了），而是**工作目录里的符号链接指向外部**——字符串前缀看着
    合法，解析后已经是别的目录。只读端点同样不能变成「读任意文件」的通道。

    Returns:
        原路径（校验用 realpath，返回仍用调用方给的那份）。

    Raises:
        AssetPathError: 解析后不以工作目录为祖先。
    """
    real_root = os.path.realpath(workdir)
    real_target = os.path.realpath(candidate)
    if real_target != real_root and not real_target.startswith(real_root + os.sep):
        raise AssetPathError(
            f"条目「{candidate.name}」解析后越出工作目录（可能是一个指向外部的符号链接）"
            "——已拒绝访问。请检查该文件是否被替换成了链接。",
        )
    return candidate


def _require_item_name(item: str) -> None:
    """校验条目名是「素材主干」而不是想拼路径。

    Raises:
        AssetPathError: 空名、含路径分隔符或含 NUL 字节。
    """
    if not item or any(sep in item for sep in _SEPARATORS) or "\x00" in item:
        raise AssetPathError(
            f"条目名「{item}」不合法——条目身份是素材主干（不含扩展名与路径），"
            "例如 cat_001。",
        )


def registered_origins(store: WorkdirStore) -> dict[str, ImportOrigin]:
    """导入登记册：文件名 → 最近一次导入的出身（追加序，后者覆盖前者）。

    「同一素材多次导入取最近一次」——imports.jsonl 是 append-only 事件流，
    当前有效值就是最后一条。
    """
    origins: dict[str, ImportOrigin] = {}
    for record in store.read_import_records():
        if record.get("kind") == "rebuild":
            origins.clear()
        source = cast(str, record["source"])
        imported_at = cast(str, record["imported_at"])
        for entry in cast("list[dict[str, object]]", record.get("files", [])):
            name = entry.get("name")
            if isinstance(name, str):
                origins[name] = ImportOrigin(
                    name=name, source=source, imported_at=imported_at
                )
    return origins


def resolve_asset(workdir: Path, item: str) -> Path:
    """按素材主干解析出可对外提供的素材文件（三重校验的后两重）。

    校验顺序：名字合法 → 素材在盘 → **在册**（导入登记过）→ realpath confine。
    在册这一重是 M2「批次成员由导入登记界定」在预览面上的延伸：没登记的文件不属于
    任何批次，预览端点不为它服务（它该出现在「未导入」列表里等用户处置）。

    Raises:
        AssetPathError: 条目名不合法，或解析后越出工作目录。
        AssetNotFoundError: 素材不在工作目录（缺失），或在盘但未登记。
    """
    _require_item_name(item)
    origins = registered_origins(WorkdirStore(workdir))
    path = scan_assets(workdir).get(item)
    if path is None:
        raise AssetNotFoundError(
            f"素材「{item}」不在工作目录中（缺失）——请先用「重新导入」补回素材再预览。",
        )
    if path.name not in origins:
        raise AssetNotFoundError(
            f"素材「{path.name}」未登记在册——先在左列「未导入」分组里导入它再预览。",
        )
    return confine_to_workdir(workdir, path)


def product_path(workdir: Path, seq: int, item: str) -> Path:
    """某批次某条目的产物路径（``s<N>__<主干>.txt``），名字与 confine 都已校验。

    路径是拼出来的（不像素材那样从目录扫描里取），所以必须过 confine——
    产物可能在素材被删后仍留着（「无素材产物」），不能靠「素材在册」兜住。

    Raises:
        AssetPathError: 条目名不合法，或解析后越出工作目录。
    """
    _require_item_name(item)
    return confine_to_workdir(workdir, workdir / product_filename(seq, item))


def product_has_content(path: Path) -> bool:
    """一份产物是否算「已有产物」：文件在，且去掉首尾空白后仍有内容。

    这就是「已完成」状态位的定义（PRD F4）——txt 存在但为空 / 全空白属**产物异常**，
    按未完成处理、可重打。跑批的续跑跳过判定与条目视图的分组判定共用这一份判定，
    两处口径不会各说各话。

    读不出（权限 / 编码不对）按「没有产物」处理：产物是本工具写的 UTF-8 文本，
    读不出就说明它不可用，重打比报错更符合用户目的（与跑批执行器既有口径一致）。
    """
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


def unimported_files(workdir: Path, registered: set[str]) -> list[UnimportedFile]:
    """工作目录里不会成为条目的文件清单（左列「未导入」分组的数据源）。

    三类原因（与导入扫描同一套措辞）：扩展名不支持 / 超出大小上限 / 未登记。
    本工具自己写的产物 txt 不列入——它不是「用户丢进来等着处置的素材」。

    Args:
        workdir: 工作目录路径。
        registered: 已登记在册的文件名集合（:func:`registered_origins` 的键）。

    Returns:
        按文件名排序的清单（目录不存在时为空）。
    """
    result: list[UnimportedFile] = []
    if not workdir.is_dir():
        return result
    for entry in sorted(workdir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.name in registered:
            continue
        if is_product_name(entry.name):
            continue
        suffix = entry.suffix.lower()
        size = entry.stat().st_size
        if suffix not in ASSET_EXTENSIONS:
            result.append(
                UnimportedFile(
                    name=entry.name,
                    reason=REASON_UNSUPPORTED_EXTENSION,
                    size=size,
                    limit=None,
                )
            )
            continue
        limit = size_limit(suffix)
        if size > limit:
            result.append(
                UnimportedFile(
                    name=entry.name, reason=REASON_OVERSIZE, size=size, limit=limit
                )
            )
            continue
        result.append(
            UnimportedFile(
                name=entry.name, reason=REASON_UNREGISTERED, size=size, limit=None
            )
        )
    return result
