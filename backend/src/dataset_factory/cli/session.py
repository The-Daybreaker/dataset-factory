"""会话命令：``dsf session list / show``——查看会话与回放历史（恢复入口在 dsf chat / label --session）。"""

from __future__ import annotations

from typing import Annotated

import typer

from ..sessions import list_sessions, read_events
from ..sessions.model import MessageEvent
from .errors import handle_domain_errors

app = typer.Typer(help="会话查看（list / show）", no_args_is_help=True)


@app.command("list")
@handle_domain_errors
def session_list() -> None:
    """列出全部会话 id（按创建时间正序；最新在最后）。"""
    sessions = list_sessions()
    if not sessions:
        typer.echo("（还没有会话——dsf label / dsf chat 会自动创建）")
        return
    for session_id in sessions:
        typer.echo(session_id)


@app.command("show")
@handle_domain_errors
def session_show(
    session_id: Annotated[str, typer.Argument(help="会话 id")],
) -> None:
    """回放某会话的对话历史（user / assistant 消息 + 附件名）。"""
    events = read_events(session_id)
    messages = [
        event
        for event in events
        if isinstance(event, MessageEvent) and event.role in ("user", "assistant")
    ]
    if not messages:
        typer.echo("（该会话还没有对话消息）")
        return
    for event in messages:
        attachment = f"  [@{event.attachment}]" if event.attachment else ""
        typer.echo(f"{event.role}: {event.text}{attachment}")
