"""工作目录统计：设置页基本信息与策略分母的读模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .assets import confine_to_workdir, scan_assets
from .errors import WorkdirPathError
from .locks import import_guard
from .store import WorkdirStore

__all__ = ["WorkdirStats", "scan_workdir_stats"]


@dataclass(frozen=True)
class WorkdirStats:
    """工作目录当前素材体积；策略与产物分母由批次列表另算。"""

    asset_count: int
    asset_bytes: int


def scan_workdir_stats(workdir: Path) -> WorkdirStats:
    """统计当前素材清单的真实字节数，与工具导入操作互斥。

    每个条目的当前素材按主干取一个文件（``scan_assets`` 是全站唯一事实源），
    不会把同主干的多扩展名重复计入；统计包含尚未登记的在盘素材。
    """
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    try:
        with import_guard(WorkdirStore(workdir).dsf_path):
            assets = scan_assets(workdir)
            return WorkdirStats(
                asset_count=len(assets),
                asset_bytes=sum(
                    confine_to_workdir(workdir, asset).stat().st_size
                    for asset in assets.values()
                ),
            )
    except OSError as exc:
        raise WorkdirPathError(
            "无法读取完整素材统计，请检查目录权限与文件是否仍存在后重试。"
        ) from exc
