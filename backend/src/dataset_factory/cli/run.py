"""前台跑批命令：业务事件写 stderr，最终报告写 stdout。"""

from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from ..runs import (
    RUN_STATUS_INTERRUPTED,
    BatchRunner,
    ItemDeltaEvent,
    RunEvent,
    completer_for_snapshot,
)
from ..strategies import read_snapshot
from ..workdir import WorkdirStore
from ..workdir.assets import registered_origins, unimported_files
from .batch import batch_location
from .errors import handle_domain_errors
from .operations import cancellation, confirm_action, print_result


class Mode(StrEnum):
    """Typer 使用的运行模式枚举。"""

    full = "full"
    retry = "retry"


@handle_domain_errors
def run(
    path: Path,
    batch: str,
    mode: Annotated[Mode, typer.Option("--mode")] = Mode.full,
    yes: Annotated[
        bool, typer.Option("--yes", help="确认跳过未导入文件，继续本次跑批")
    ] = False,
) -> None:
    """前台执行批次，Ctrl-C 等待当前条目安全收尾并输出中断报告。"""
    root, seq = batch_location(path, batch)
    store = WorkdirStore(root)
    unimported = unimported_files(root, set(registered_origins(store)))
    if unimported:
        typer.echo(f"以下 {len(unimported)} 个文件未导入，本次不会打标：", err=True)
        for entry in unimported:
            typer.echo(f"  {entry.name}：{entry.reason}", err=True)
        confirm_action("可先用 workdir import 导入素材；仍要开始本次跑批？", yes)
    snapshot = read_snapshot(root, seq)
    runner = BatchRunner(
        root,
        seq,
        completer_for_snapshot(snapshot.endpoint),
        mode="retry" if mode == Mode.retry else "full",
        trigger="cli",
    )

    def progress(event: RunEvent) -> None:
        if isinstance(event, ItemDeltaEvent):
            return  # 逐字增量只服务 Web 界面；CLI 有逐条终态，刷几千行 delta 是噪音
        typer.echo(f"{event.kind}: {event.to_payload()}", err=True)

    unsubscribe = runner.subscribe(progress)
    try:
        with cancellation(runner.stop) as stop:
            report = runner.run()
    finally:
        unsubscribe()
    result = asdict(report)
    result["log_path"] = str(report.run_dir / "run.log")
    print_result(result)
    if stop.is_set() or report.status == RUN_STATUS_INTERRUPTED:
        raise typer.Exit(130)
    if report.counters["failed"]:
        raise typer.Exit(1)
