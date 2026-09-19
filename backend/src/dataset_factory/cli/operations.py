"""二期命令共用的登记解析、确认、结果输出与协作取消。"""

from __future__ import annotations

import json
import signal
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

import typer

from ..workdir import WorkdirRegistry


def registered_root(identifier: str) -> Path:
    """按登记 id 取工作目录根路径（各命令都要这一步，原来在 batch 与 workdir 里各写五遍）。

    Args:
        identifier: 工作目录登记 id（CLI 侧通常先由 ``registered_id`` 从用户给的路径换来）。

    Returns:
        该登记项指向的目录路径。

    Raises:
        WorkdirNotFoundError: id 未登记（上层按既有约定翻成错误消息与退出码 1）。
    """
    return Path(WorkdirRegistry.get(identifier).path)


def confirm_action(message: str, yes: bool) -> None:
    """危险操作默认确认；非终端缺 --yes 时立即按用法错误退出。"""
    if yes:
        return
    if not sys.stdin.isatty():
        typer.echo("错误：非交互环境执行此操作必须提供 --yes。", err=True)
        raise typer.Exit(2)
    typer.confirm(message, abort=True, err=True)


def confirm_or_abort(message: str, yes: bool) -> None:
    """删除类命令的确认：拒绝、或非交互下无法确认 → 取消（退出码 1）。

    与 `confirm_action` 是两套对外口径（提示写 stdout、非交互按取消算而不是用法错误），
    这是各命令现状的差别，收敛时保持原样，只把三处逐字相同的两行写法并到一处。
    """
    if yes:
        return
    if not typer.confirm(message):
        raise typer.Abort()


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
