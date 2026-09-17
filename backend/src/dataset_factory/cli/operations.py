"""二期命令共用的确认、结果输出与协作取消。"""

from __future__ import annotations

import json
import signal
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from types import FrameType

import typer


def confirm_action(message: str, yes: bool) -> None:
    """危险操作默认确认；非终端缺 --yes 时立即按用法错误退出。"""
    if yes:
        return
    if not sys.stdin.isatty():
        typer.echo("错误：非交互环境执行此操作必须提供 --yes。", err=True)
        raise typer.Exit(2)
    typer.confirm(message, abort=True, err=True)


def print_result(value: object) -> None:
    """结构化结果只写 stdout，路径转字符串。"""
    typer.echo(json.dumps(value, ensure_ascii=False, default=str))


@contextmanager
def cancellation(
    on_stop: Callable[[], None] | None = None,
) -> Generator[threading.Event]:
    """Ctrl-C 只置取消信号，核心操作在安全点收尾后再返回。"""
    stop = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        if not stop.is_set():
            typer.echo("正在停止，等待当前操作安全收尾。", err=True)
            stop.set()
            if on_stop is not None:
                on_stop()

    previous = signal.signal(signal.SIGINT, request_stop)
    try:
        yield stop
    finally:
        signal.signal(signal.SIGINT, previous)


def report_progress(value: float) -> None:
    """长任务进度与可管道读取的正文分开。"""
    typer.echo(f"进度 {value:.0%}", err=True)
