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
from ..llm import (
    VIDEO_EXTENSIONS,
    VIDEO_MIME_BY_SUFFIX,
    LLMError,
    build_completer,
    read_config,
)
from ..prompts import PromptNotFoundError, prompt_id_by_display_name, read_prompt
from ..sessions import SessionError, latest_session_id
from ..skills import SkillNotFoundError, get_skill, skill_id_by_display_name
from .errors import DOMAIN_ERRORS, handle_domain_errors

# chat 输入行里附件的轻量语法：`@文件路径 指令`（@ 开头第一个词是附件路径，其余是指令；
# 图片 / 视频按扩展名区分，视频扩展名集合以 llm 层的 VIDEO_EXTENSIONS 为准）。
_AT_SYNTAX = re.compile(r"^@(\S+)\s*(.*)$")


def _prompt_ref(ref: str) -> str:
    """--prompt 的引用（ID 或唯一显示名）→ 提示词 ID。

    解析不到走用户错误通道（退出码 1 + stderr 可操作提示），与 CLI 其余错误口径一致。
    """
    try:
        return read_prompt(ref).id
    except PromptNotFoundError:
        pass
    resolved = prompt_id_by_display_name(ref)
    if resolved is None:
        typer.secho(
            f"错误：提示词 {ref!r} 不存在（或显示名重名不唯一）；"
            "用 dsf prompt list 查看 ID 与现有提示词。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return resolved


def _skill_ref(ref: str) -> str:
    """--skill 的引用（ID 或唯一显示名）→ skill ID（解析不到退出码 1）。"""
    try:
        return get_skill(ref).id
    except SkillNotFoundError:
        pass
    resolved = skill_id_by_display_name(ref)
    if resolved is None:
        typer.secho(
            f"错误：skill {ref!r} 不存在（或显示名重名不唯一）；"
            "用 dsf skill list 查看 ID 与已导入清单。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return resolved


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
    prompt_ref: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            "-p",
            help="基础提示词（ID 或唯一显示名）；续接已有会话时可省（沿用会话设置）",
        ),
    ] = None,
    skill_refs: Annotated[
        list[str] | None,
        typer.Option(
            "--skill",
            "-s",
            help="启用的 skill（ID 或唯一显示名，可多次）；缺省沿用会话设置",
        ),
    ] = None,
    image: Annotated[
        Path | None, typer.Option("--image", "-i", help="图片文件路径")
    ] = None,
    video: Annotated[
        Path | None,
        typer.Option("--video", "-v", help="视频文件路径（与 --image 互斥）"),
    ] = None,
    video_fps: Annotated[
        int,
        typer.Option(
            "--video-fps",
            min=1,
            max=10,
            help="视频抽帧 fps（整型 1–10，默认 2）",
        ),
    ] = 2,
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
    if image is not None and video is not None:
        raise typer.BadParameter("图片与视频只能带一个（--image 与 --video 互斥）。")
    engine = build_engine()
    video_bytes = video.read_bytes() if video is not None else None
    video_mime = (
        VIDEO_MIME_BY_SUFFIX.get(video.suffix.lower(), "video/mp4")
        if video is not None
        else "video/mp4"
    )
    result = engine.label(
        session_id=session_id,
        prompt_id=_prompt_ref(prompt_ref) if prompt_ref else None,
        skill_ids=[_skill_ref(item) for item in (skill_refs or [])]
        if skill_refs
        else None,
        instruction=message,
        image=image,
        video_bytes=video_bytes,
        video_name=video.name if video is not None else "video.mp4",
        video_mime=video_mime,
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
    prompt_ref: Annotated[
        str | None,
        typer.Option(
            "--prompt", "-p", help="基础提示词（ID 或唯一显示名）；新会话必选，续接可省"
        ),
    ] = None,
    skill_refs: Annotated[
        list[str] | None,
        typer.Option(
            "--skill",
            "-s",
            help="启用的 skill（ID 或唯一显示名，可多次）；首轮生效并随会话延续，续接可省",
        ),
    ] = None,
    video_fps: Annotated[
        int,
        typer.Option(
            "--video-fps",
            min=1,
            max=10,
            help="@ 附带视频的抽帧 fps（整型 1–10，默认 2）",
        ),
    ] = 2,
    video_max_frames: Annotated[
        int,
        typer.Option("--video-max-frames", help="@ 附带视频的抽帧帧数上限（默认 16）"),
    ] = 16,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="要恢复的会话 id；缺省自动取最新会话"),
    ] = None,
) -> None:
    """终端多轮打标：交互输入指令（附图 / 附视频用 `@文件路径 指令`），Ctrl+D / Ctrl+C 退出。"""
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
            f"当前基础提示词 ID: {snapshot.settings.prompt_id or '（未设置，首轮需 -p 指定）'}",
            fg=typer.colors.YELLOW,
        )
    typer.echo("输入指令开始（附图 / 附视频：@文件路径 指令；退出：Ctrl+D / Ctrl+C）")
    while True:
        try:
            line = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            typer.echo()
            break
        attachment, instruction = _parse_chat_line(line)
        if not instruction and attachment is None and not line.strip():
            continue
        try:
            if attachment is not None and attachment.suffix.lower() in VIDEO_EXTENSIONS:
                result = engine.label(
                    session_id=session_id,
                    prompt_id=_prompt_ref(prompt_ref) if prompt_ref else None,
                    skill_ids=[_skill_ref(item) for item in (skill_refs or [])]
                    if skill_refs
                    else None,
                    instruction=instruction,
                    video_bytes=_read_chat_video(attachment),
                    video_name=attachment.name,
                    video_mime=VIDEO_MIME_BY_SUFFIX.get(
                        attachment.suffix.lower(), "video/mp4"
                    ),
                    video_fps=video_fps,
                    video_max_frames=video_max_frames,
                )
            else:
                result = engine.label(
                    session_id=session_id,
                    prompt_id=_prompt_ref(prompt_ref) if prompt_ref else None,
                    skill_ids=[_skill_ref(item) for item in (skill_refs or [])]
                    if skill_refs
                    else None,
                    instruction=instruction,
                    image=attachment,
                )
        except DOMAIN_ERRORS as exc:
            _report_turn_failure(exc)
            continue
        session_id = result.session_id
        prompt_ref = None  # 首轮落定后由会话设置携带，不再重复传
        skill_refs = None
        typer.echo(result.caption)


def _report_turn_failure(exc: BaseException) -> None:
    """逐轮容错的统一报错：本轮失败打印可操作消息后由调用方继续下一轮（历史已在盘上）。"""
    typer.secho(f"错误：{exc}", fg=typer.colors.RED, err=True)
    if isinstance(exc, LLMError) and exc.retryable:
        typer.secho(
            "该错误通常是暂时性的（网络 / 超时），可直接重发本轮。",
            fg=typer.colors.YELLOW,
            err=True,
        )


def _parse_chat_line(line: str) -> tuple[Path | None, str]:
    """解析 chat 输入行：`@文件路径 指令` → (附件路径, 指令)；普通行 → (None, 原文)。"""
    matched = _AT_SYNTAX.match(line.strip())
    if matched is None:
        return None, line
    return Path(matched.group(1)), matched.group(2)


def _read_chat_video(attachment: Path) -> bytes:
    """读 @ 附带的视频文件字节（视频扩展名判定以 llm 层 VIDEO_EXTENSIONS 为准）。

    视频字节必须在入口层读好交给引擎（引擎只收字节）；读不出来是用户错（路径不存在 /
    无权限），翻译成与「附件源不是文件」同域同口径的 SessionError，走 chat 的逐轮容错
    ——报错后继续下一轮，会话历史还在盘上，不整场退出。
    """
    try:
        return attachment.read_bytes()
    except OSError as exc:
        raise SessionError(
            f"无法读取视频 {attachment}（{exc.strerror or exc}）；请检查路径后重发本轮。"
        ) from exc


def _print_history_line(role: str, text: str, attachment: str | None) -> None:
    """打印一条历史消息（角色前缀 + 附件标注，终端回放用）。"""
    attachment_note = f"  [@{attachment}]" if attachment else ""
    typer.secho(f"{role}: {text}{attachment_note}", fg=typer.colors.CYAN)
