"""提示词库命令：``dsf prompt list / show / save / rename / rm``——直连 prompts 数据域做管理操作。

入口层可直连数据域（分层规则的允许例外，管理操作不经打标引擎绕行）。
寻址口径（2026-09-23 ID 化）：提示词参数接受 **ID 或唯一显示名**（ID 优先），
解析收敛在 _resolve_ref。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ..prompts import (
    Prompt,
    delete_prompt,
    list_prompts,
    prompt_id_by_display_name,
    read_prompt,
    rename_prompt,
    save_prompt,
    seed_builtin_presets,
)
from .errors import handle_domain_errors
from .operations import confirm_or_abort

app = typer.Typer(help="提示词库管理（增删改查）", no_args_is_help=True)


@app.callback()
@handle_domain_errors
def seed_builtin_callback() -> None:
    """任一 prompt 子命令执行前先播种内置预置提示词（标记文件在即 no-op）。"""
    seed_builtin_presets()


def _resolve_ref(ref: str) -> str:
    """把「提示词 ID 或唯一显示名」解析成 ID（ID 优先）。

    Raises:
        typer.Exit: 解析不到（不存在 / 显示名重名不唯一），给出可操作提示。
    """
    matches = [prompt for prompt in list_prompts() if prompt.id == ref]
    if matches:
        return ref
    by_name = prompt_id_by_display_name(ref)
    if by_name is not None:
        return by_name
    typer.secho(
        f"错误：提示词 {ref!r} 不存在（或显示名重名不唯一）；"
        "用 dsf prompt list 查看各条目的 ID。",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(1)


@app.command("list")
@handle_domain_errors
def prompt_list() -> None:
    """列出全部提示词（ID + 名称 + 描述）。"""
    prompts = list_prompts()
    if not prompts:
        typer.echo("（提示词库为空——dsf prompt save 添加）")
        return
    for item in prompts:
        description = item.description or "（无描述）"
        typer.echo(f"{item.id}\t{item.name}\t{description}")


@app.command("show")
@handle_domain_errors
def prompt_show(
    ref: Annotated[str, typer.Argument(help="提示词 ID 或唯一显示名")],
) -> None:
    """显示某条提示词正文。"""
    typer.echo(read_prompt(_resolve_ref(ref)).body)


@app.command("save")
@handle_domain_errors
def prompt_save(
    name: Annotated[
        str, typer.Argument(help="显示名（可改、允许重名；身份是自动分配的 ID）")
    ],
    description: Annotated[
        str, typer.Option("--description", "-d", help="用途说明（选择器里展示）")
    ] = "",
    body_file: Annotated[
        Path | None,
        typer.Option(
            "--file", "-f", help="正文取自该文件；缺省从 stdin 读（管道或交互粘贴）"
        ),
    ] = None,
) -> None:
    """保存提示词（显示名唯一命中已有条目 = 覆盖那条；重名多条或不存在 = 新建）。"""
    if body_file is not None:
        try:
            body = body_file.read_text(encoding="utf-8")
        except OSError as exc:
            # 用户错（路径不存在 / 不可读）给可操作消息，不甩原始栈。
            typer.secho(
                f"错误：无法读取正文文件 {body_file}：{exc.strerror or exc}；"
                "请确认路径存在且可读。",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1) from exc
        except UnicodeDecodeError as exc:
            typer.secho(
                f"错误：正文文件 {body_file} 不是合法 UTF-8 编码（{exc.reason}）；"
                "请改用 UTF-8 文本文件。",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1) from exc
    else:
        typer.echo("输入提示词正文（结束：Ctrl+D / Ctrl+Z+回车）：", err=True)
        body = sys.stdin.read()
    existing = prompt_id_by_display_name(name)
    pid = save_prompt(
        Prompt(id=existing or "", name=name, description=description, body=body)
    )
    typer.secho(f"已保存提示词 {name!r}（{pid}）", fg=typer.colors.GREEN)


@app.command("rename")
@handle_domain_errors
def prompt_rename(
    ref: Annotated[str, typer.Argument(help="提示词 ID 或唯一显示名")],
    new_name: Annotated[str, typer.Argument(help="新显示名（可改、允许重名）")],
) -> None:
    """改显示名（只写 frontmatter 的 name 字段；文件名是 ID、永不动，引用不受影响）。"""
    pid = _resolve_ref(ref)
    rename_prompt(pid, new_name)
    typer.secho(f"已把 {pid} 的显示名改为 {new_name!r}", fg=typer.colors.GREEN)


@app.command("rm")
@handle_domain_errors
def prompt_rm(
    ref: Annotated[str, typer.Argument(help="提示词 ID 或唯一显示名")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过删除确认")] = False,
) -> None:
    """删除提示词（需确认；_history 里的历史版本不受影响）。"""
    pid = _resolve_ref(ref)
    confirm_or_abort(f"确认删除提示词 {pid!r}？", yes)
    delete_prompt(pid)
    typer.secho(f"已删除提示词 {pid!r}", fg=typer.colors.GREEN)
