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
    """删除类命令的两级确认：交互中拒绝 → 取消（退出码 1）；非交互缺 --yes → 用法错误（退出码 2）。

    两种结局分开取码，因为对调用方意味着不同的事：拒绝是「这次不做」，脚本该停就停；
    缺 --yes 是「你调用方式不对」——与参数缺失同类，补上 --yes 再来。后者与 Typer/click
    对用法错误一律给 2 的默认行为一致，也与 `confirm_action` 同口径。

    非交互环境（管道 / 脚本）一律要求显式 ``--yes``，不读 stdin 里的答复——否则
    「管道里恰好有个 y」和「有人真的按了 y」在退出码上分不开。
    """
    if yes:
        return
    if not sys.stdin.isatty():
        typer.echo("错误：非交互环境执行此操作必须提供 --yes。", err=True)
        raise typer.Exit(2)
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
