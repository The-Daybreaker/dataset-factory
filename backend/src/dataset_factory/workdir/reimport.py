"""按已记录来源恢复缺失素材，复用导入护栏及冲突判定。"""

from pathlib import Path
from threading import Event
from typing import Any

from .assets import registered_origins
from .errors import WorkdirPathError
from .importer import import_assets
from .locks import import_guard
from .store import WorkdirStore


def reimport_missing(
    workdir: Path,
    names: set[str] | None = None,
    *,
    force_names: frozenset[str] | set[str] = frozenset(),
    should_stop: Event | None = None,
) -> dict[str, Any]:
    """恢复指定缺失文件或全部可恢复文件，来源失效逐条报告，不导入额外文件。"""
    store = WorkdirStore(workdir)
    with import_guard(store.dsf_path):
        origins = registered_origins(store)
        selected = set(origins) if names is None else names
        if selected - origins.keys():
            raise WorkdirPathError("所选文件不在导入记录中，请检查文件名。")
        if force_names - selected:
            raise WorkdirPathError("强制导入的文件必须包含在本次恢复名单中。")
        sources: dict[Path, set[str]] = {}
        unavailable: list[dict[str, str]] = []
        for name in sorted(selected):
            if not name or name in {".", ".."} or any(c in name for c in "/\\\x00"):
                raise WorkdirPathError("导入记录含不合法的文件名。")
            if (workdir / name).exists():
                continue
            origin = origins[name]
            source = Path(origin.source)
            if (
                not origin.source
                or source.resolve() == workdir.resolve()
                or not (source / name).is_file()
            ):
                unavailable.append(
                    {"name": name, "reason": "原始来源不可用，请从别处导入"}
                )
                continue
            sources.setdefault(source, set()).add(name)
        reports = [
            import_assets(
                workdir,
                source,
                names=chosen,
                force_names=force_names & chosen,
                should_stop=should_stop,
            )
            for source, chosen in sources.items()
        ]
        return {"imports": reports, "unavailable": unavailable}
