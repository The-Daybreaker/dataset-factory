"""打标命令：``dsf label``（单发 + 会话续接，供外部 agent）与 ``dsf chat``（终端多轮）。

两个命令走同一打标核心（LabelingEngine），与 Web 端能力对等；``dsf label`` 首次调用返回
会话 id，之后带 ``--session`` 续接即带历史的迭代改写。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

import typer

from ..labeling import LabelingEngine
from ..llm import LLMError, build_completer, read_config
from ..sessions import latest_session_id
from .errors import DOMAIN_ERRORS, handle_domain_errors

# chat 输入行里附图的轻量语法：`@图片路径 指令`（@ 开头第一个词是图，其余是指令）。
_AT_IMAGE_SYNTAX = re.compile(r"^@(\S+)\s*(.*)$")


def build_engine() -> LabelingEngine:
    """从当前端点配置装配打标引擎。

    独立成函数是给测试留注入位：monkeypatch 本函数返回带假客户端的引擎，即可离线测
    CLI 全流程（goal B2「llm 层可被 mock」）。
    """
    config = read_config()
    return LabelingEngine(build_completer(config), config.model)


@handle_domain_errors
def label(
    message: Annotated[
        str,
        typer.Option(
            "--message", "-m", help="打标指令（纯图轮可省，任务说明在基础提示词里）"
        ),
    ] = "",
    prompt_name: Annotated[
        str | None,
        typer.Option(
            "--prompt", "-p", help="基础提示词名称；续接已有会话时可省（沿用会话设置）"
        ),
    ] = None,
    skill_names: Annotated[
        list[str] | None,
        typer.Option("--skill", "-s", help="启用的 skill（可多次）；缺省沿用会话设置"),
    ] = None,
    image: Annotated[
        Path | None, typer.Option("--image", "-i", help="图片文件路径")
    ] = None,
    video: Annotated[
        Path | None,
        typer.Option("--video", "-v", help="视频文件路径（与 --image 互斥）"),
    ] = None,
    video_fps: Annotated[
        float, typer.Option("--video-fps", help="视频抽帧 fps（0.1–10，默认 2.0）")
    ] = 2.0,
    video_max_frames: Annotated[
        int,
        typer.Option("--video-max-frames", help="视频抽帧帧数上限（默认 16）"),
    ] = 16,
    session_id: Annotated[
        str | None, typer.Option("--session", help="续接的会话 id；缺省新建会话")
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="按 JSON 输出（会话 id + caption），供外部 agent 解析"
        ),
    ] = False,
) -> None:
    """单发打标：发图片或视频 + 指令，输出 caption；带 --session 续接即迭代改写。"""
    engine = build_engine()
    video_bytes = video.read_bytes() if video is not None else None
    result = engine.label(
        session_id=session_id,
        prompt_name=prompt_name,
        skill_names=skill_names,
        instruction=message,
        image=image,
        video_bytes=video_bytes,
        video_name=video.name if video is not None else "video.mp4",
        video_fps=video_fps,
        video_max_frames=video_max_frames,
    )
    if as_json:
        typer.echo(
            json.dumps(
                {"session_id": result.session_id, "caption": result.caption},
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(result.caption)
        typer.secho(
            f"会话: {result.session_id}（迭代改写用 --session {result.session_id} 续接）",
            fg=typer.colors.YELLOW,
            err=True,
        )


@handle_domain_errors
def chat(
    prompt_name: Annotated[
        str | None,
        typer.Option("--prompt", "-p", help="基础提示词名称；新会话必选，续接可省"),
    ] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="要恢复的会话 id；缺省自动取最新会话"),
    ] = None,
) -> None:
    """终端多轮打标：交互输入指令（附图用 `@图片路径 指令`），Ctrl+D / Ctrl+C 退出。"""
    engine = build_engine()
    if session_id is None:
        session_id = latest_session_id()
    if session_id is not None:
        snapshot = engine.restore(session_id)
        typer.secho(
            f"— 恢复会话 {session_id}（{len(snapshot.messages)} 条历史）—",
            fg=typer.colors.YELLOW,
        )
        for item in snapshot.messages[-6:]:
            _print_history_line(item.role, item.text, item.attachment)
        typer.secho(
            f"当前基础提示词: {snapshot.settings.prompt_name or '（未设置，首轮需 -p 指定）'}",
            fg=typer.colors.YELLOW,
        )
    typer.echo("输入指令开始（附图：@图片路径 指令；退出：Ctrl+D / Ctrl+C）")
    while True:
        try:
            line = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            typer.echo()
            break
        image_path, instruction = _parse_chat_line(line)
        if not instruction and image_path is None and not line.strip():
            continue
        try:
            result = engine.label(
                session_id=session_id,
                prompt_name=prompt_name,
                instruction=instruction,
                image=image_path,
            )
        except DOMAIN_ERRORS as exc:
            # 逐轮容错：一轮失败（超时、@错了图片路径等）报错后继续，多轮上下文还在盘上，
            # 直接重发本轮即可——整场退出等于把前面的对话全作废。
            typer.secho(f"错误：{exc}", fg=typer.colors.RED, err=True)
            if isinstance(exc, LLMError) and exc.retryable:
                typer.secho(
                    "该错误通常是暂时性的（网络 / 超时），可直接重发本轮。",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            continue
        session_id = result.session_id
        prompt_name = None  # 首轮落定后由会话设置携带，不再重复传
        typer.echo(result.caption)


def _parse_chat_line(line: str) -> tuple[Path | None, str]:
    """解析 chat 输入行：`@图片路径 指令` → (图片路径, 指令)；普通行 → (None, 原文)。"""
    matched = _AT_IMAGE_SYNTAX.match(line.strip())
    if matched is None:
        return None, line
    return Path(matched.group(1)), matched.group(2)


def _print_history_line(role: str, text: str, attachment: str | None) -> None:
    """打印一条历史消息（角色前缀 + 附件标注，终端回放用）。"""
    attachment_note = f"  [@{attachment}]" if attachment else ""
    typer.secho(f"{role}: {text}{attachment_note}", fg=typer.colors.CYAN)
