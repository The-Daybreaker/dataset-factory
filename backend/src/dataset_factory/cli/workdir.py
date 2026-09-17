"""工作目录命令：路径解析、维护操作与终端确认。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from ..runs.journal import load_recent_success_hashes
from ..strategies import list_batches
from ..workdir import (
    WorkdirRegistry,
    WorkdirStore,
    ensure_importable_source,
    import_assets,
)
from ..workdir.cleanup import (
    cleanup_products,
    cleanup_runs,
    preview_cleanup,
    preview_run_cleanup,
)
from ..workdir.deletion import delete_workdir, preview_workdir_deletion
from ..workdir.errors import WorkdirNotFoundError
from ..workdir.integrity import rebuild_import_records, scan_integrity
from ..workdir.relocation import (
    relocate_workdir,
    relocation_status,
    retry_relocation_cleanup,
)
from .errors import handle_domain_errors
from .operations import cancellation, confirm_action, print_result, report_progress

app = typer.Typer(help="工作目录管理", no_args_is_help=True)


@app.command("add")
@handle_domain_errors
def add_workdir(
    path: Annotated[Path, typer.Argument(help="工作目录路径")],
    source: Annotated[
        Path | None, typer.Option("--source", help="复制导入的来源目录")
    ] = None,
    title: Annotated[str, typer.Option("--title", help="显示名称")] = "",
    yes: Annotated[bool, typer.Option("--yes", help="确认就地采用")] = False,
) -> None:
    """登记并导入素材；不传来源时就地采用目录内素材。"""
    path = path.expanduser().resolve()
    if source is None:
        confirm_action(
            f"就地采用 {path}，后续目录维护将直接作用于原始素材，继续？", yes
        )
    else:
        source = source.expanduser().resolve()
        ensure_importable_source(path, source)
    entry = WorkdirRegistry.register(path, title=title)
    with cancellation() as stop:
        result = import_assets(path, source, should_stop=stop, progress=report_progress)
    print_result({"workdir": asdict(entry), "import": result})


@app.command("show")
@handle_domain_errors
def show_workdir(path: Annotated[Path, typer.Argument(help="工作目录路径")]) -> None:
    """显示目录登记、导入记录和搬迁待处理状态。"""
    wid = registered_id(path)
    entry = WorkdirRegistry.get(wid)
    print_result(
        {
            "workdir": asdict(entry),
            "imports": WorkdirStore(Path(entry.path)).read_import_records(),
            "relocations": relocation_status(wid),
        }
    )


@app.command("import")
@handle_domain_errors
def import_workdir(
    path: Annotated[Path, typer.Argument(help="工作目录路径")],
    source: Annotated[Path, typer.Argument(help="来源目录")],
    force_names: Annotated[
        list[str] | None,
        typer.Option("--force-name", help="异名同容时仍按新名导入，可重复"),
    ] = None,
) -> None:
    """增量复制导入素材，按内容判定重复与冲突。"""
    entry = WorkdirRegistry.get(registered_id(path))
    with cancellation() as stop:
        result = import_assets(
            Path(entry.path),
            source.expanduser().resolve(),
            force_names=set(force_names or []),
            should_stop=stop,
            progress=report_progress,
        )
    print_result(result)


@app.command("relocate")
@handle_domain_errors
def relocate(
    path: Annotated[Path, typer.Argument(help="工作目录路径")],
    destination: Annotated[Path, typer.Argument(help="目标路径")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """复制并校验后搬迁，完整成功后清理旧位置。"""
    confirm_action(f"将 {path} 搬迁到 {destination}，校验后删除旧位置，继续？", yes)
    with cancellation() as stop:
        result = relocate_workdir(
            registered_id(path),
            destination.expanduser().absolute(),
            should_stop=stop,
            progress=report_progress,
        )
    print_result(result)


@app.command("cleanup-old")
@handle_domain_errors
def cleanup_old(
    path: Annotated[Path, typer.Argument(help="当前工作目录路径")],
    old_path: Annotated[Path, typer.Argument(help="待清理旧位置")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """重试清理搬迁记录证明的旧位置。"""
    confirm_action(f"重试清理旧位置 {old_path}？", yes)
    print_result(
        retry_relocation_cleanup(registered_id(path), old_path.expanduser().absolute())
    )


@app.command("verify")
@handle_domain_errors
def verify(path: Annotated[Path, typer.Argument(help="工作目录路径")]) -> None:
    """按活跃批次校验当前素材与打标时哈希，同时列出孤立产物。"""
    root = Path(WorkdirRegistry.get(registered_id(path)).path)
    store = WorkdirStore(root)
    print_result(
        {
            "batches": [
                {
                    "batch": f"s{batch.seq}",
                    "items": [
                        asdict(item)
                        for item in scan_integrity(
                            root, load_recent_success_hashes(store.runs_dir, batch.seq)
                        )
                    ],
                }
                for batch in list_batches(root)
                if batch.active
            ],
            "orphan_products": [asdict(item) for item in preview_cleanup(root)],
        }
    )


@app.command("rebuild-imports")
@handle_domain_errors
def rebuild(
    path: Annotated[Path, typer.Argument(help="工作目录路径")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """以现状重建导入集合，来源记空，旧历史保留。"""
    confirm_action("重建导入记录，缺失对账归零且来源记为空，继续？", yes)
    root = Path(WorkdirRegistry.get(registered_id(path)).path)
    with cancellation() as stop:
        result = rebuild_import_records(root, should_stop=stop)
    print_result(result)


@app.command("cleanup")
@handle_domain_errors
def clean_products(
    path: Annotated[Path, typer.Argument(help="工作目录路径")],
    names: Annotated[
        list[str] | None, typer.Option("--name", help="选择文件名，可重复")
    ] = None,
    list_only: Annotated[bool, typer.Option("--list", help="只显示清单")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """列出或清理明确选择的孤立产物，默认不选择任何文件。"""
    root = Path(WorkdirRegistry.get(registered_id(path)).path)
    if list_only or not names:
        print_result([asdict(item) for item in preview_cleanup(root)])
        return
    confirm_action(f"清理所选 {len(names)} 份孤立产物？", yes)
    print_result(asdict(cleanup_products(root, names)))


@app.command("cleanup-runs")
@handle_domain_errors
def clean_runs(
    path: Annotated[Path, typer.Argument(help="工作目录路径")],
    names: Annotated[
        list[str] | None, typer.Option("--name", help="运行目录名，可重复")
    ] = None,
    list_only: Annotated[bool, typer.Option("--list", help="只显示清单")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """列出或清理所选运行记录，清理将丢失打标时的哈希与失败原因。"""
    root = Path(WorkdirRegistry.get(registered_id(path)).path)
    if list_only or not names:
        print_result([asdict(item) for item in preview_run_cleanup(root)])
        return
    confirm_action(f"清理 {len(names)} 份运行记录，相关产物时效将无法校验，继续？", yes)
    print_result(asdict(cleanup_runs(root, names)))


def registered_id(path: Path) -> str:
    """按真实路径找已有登记，维护命令不隐式创建登记。"""
    resolved = path.expanduser().resolve()
    for entry in WorkdirRegistry.list_all():
        if Path(entry.path).resolve() == resolved:
            return entry.id
    raise WorkdirNotFoundError("此路径尚未登记为工作目录。")


@app.command("list")
@handle_domain_errors
def list_workdirs() -> None:
    """列出已登记工作目录。"""
    typer.echo(
        json.dumps(
            [asdict(entry) for entry in WorkdirRegistry.list_all()], ensure_ascii=False
        )
    )


@app.command("rm")
@handle_domain_errors
def remove_workdir(
    path: Annotated[Path, typer.Argument(help="已登记工作目录路径")],
    yes: Annotated[bool, typer.Option("--yes", help="确认删除全部内容")] = False,
) -> None:
    """删除整个工作目录；交互两次确认，脚本必须显式提供 --yes。"""
    if not yes and not sys.stdin.isatty():
        typer.echo("错误：非交互环境删除工作目录必须提供 --yes。", err=True)
        raise typer.Exit(2)
    wid = registered_id(path)
    if not yes:
        preview = preview_workdir_deletion(wid)
        typer.echo(
            f"{preview.path}\n{preview.file_count} 个文件 · {preview.total_bytes} 字节\n{preview.confirmation}",
            err=True,
        )
        typer.confirm("继续删除此工作目录？", abort=True, err=True)
        confirmed = typer.prompt("再次输入完整路径确认删除", err=True)
        if confirmed != preview.path:
            typer.echo("错误：确认路径不一致，未执行删除。", err=True)
            raise typer.Exit(2)
    result = delete_workdir(wid, path.expanduser().resolve())
    typer.echo(json.dumps(asdict(result), ensure_ascii=False))
    if not result.deleted:
        typer.echo(f"删除未完成，请检查权限后重试：{result.remaining_path}", err=True)
        raise typer.Exit(1)
