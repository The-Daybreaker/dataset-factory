"""当前批次的导出预览与前台 ZIP 打包。"""

import os
import secrets
from dataclasses import asdict
from pathlib import Path
from threading import Event
from typing import Annotated

import typer

from ..export import ExportPlan, build_export_plan, write_export
from ..runs import BatchInactiveError
from ..runs.journal import load_latest_item_records, load_recent_success_hashes
from ..strategies import get_batch
from ..strategies.batches import read_exclusions
from ..workdir import WorkdirStore
from ..workdir.locks import RunLock, import_guard
from .batch import batch_location
from .errors import handle_domain_errors
from .operations import cancellation, print_result, report_progress

app = typer.Typer(help="数据集导出", no_args_is_help=True)


def _plan(root: Path, seq: int, sequential: bool, stop: Event) -> ExportPlan:
    if not get_batch(root, seq).active:
        raise BatchInactiveError("该批次已停用，请先显示该批次再导出。")
    store = WorkdirStore(root)
    latest = load_latest_item_records(store.runs_dir, seq)
    return build_export_plan(
        root,
        seq,
        labeling_hashes=load_recent_success_hashes(store.runs_dir, seq),
        failed_items={item for item, row in latest.items() if row.status == "failed"},
        excluded_items=set(read_exclusions(root, seq)),
        sequential=sequential,
        should_stop=stop,
    )


@app.command("plan")
@handle_domain_errors
def plan(
    path: Path,
    batch: str,
    sequential: Annotated[bool, typer.Option("--sequential/--original-names")] = True,
) -> None:
    """查看将入包与被排除清单，默认按导入顺序从 001 编号。"""
    root, seq = batch_location(path, batch)
    with cancellation() as stop:
        result = _plan(root, seq, sequential, stop)
    print_result(asdict(result))


@app.command("run")
@handle_domain_errors
def run(
    path: Path,
    batch: str,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    sequential: Annotated[bool, typer.Option("--sequential/--original-names")] = True,
) -> None:
    """按当前配对状态导出平铺 ZIP，不覆盖已有文件。"""
    root, seq = batch_location(path, batch)
    store = WorkdirStore(root)
    lock = RunLock(store.dsf_path)
    with cancellation() as stop:
        lock.acquire({"pid": os.getpid(), "batch": batch, "operation": "export"})
        try:
            with import_guard(store.dsf_path):
                current = _plan(root, seq, sequential, stop)
                destination = (
                    output.expanduser().absolute()
                    if output is not None
                    else store.export_directory()
                    / f"s{seq}-{secrets.token_hex(12)}.zip"
                )
                if current.non_ascii_names:
                    typer.echo(
                        "注意：原始文件名含非 ASCII 字符，请核对训练环境的文件名兼容性。",
                        err=True,
                    )
                completed = write_export(
                    root,
                    current,
                    destination,
                    should_stop=stop,
                    progress=report_progress,
                )
        finally:
            lock.release()
    print_result(
        {
            "path": str(completed),
            "file_count": len(current.included),
            "total_bytes": current.total_bytes,
        }
    )
