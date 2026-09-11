"""提示词库命令：``dsf prompt list / show / save / rm``——直连 prompts 数据域做管理操作。

入口层可直连数据域（分层规则的允许例外，管理操作不经打标引擎绕行）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ..prompts import Prompt, delete_prompt, list_prompts, read_prompt, save_prompt
from .errors import handle_domain_errors

app = typer.Typer(help="提示词库管理（增删改查）", no_args_is_help=True)


@app.command("list")
@handle_domain_errors
def prompt_list() -> None:
    """列出全部提示词（名称 + 描述）。"""
    prompts = list_prompts()
    if not prompts:
        typer.echo("（提示词库为空——dsf prompt save 添加）")
        return
    for item in prompts:
        description = item.description or "（无描述）"
        typer.echo(f"{item.name}\t{description}")


@app.command("show")
@handle_domain_errors
def prompt_show(
    name: Annotated[str, typer.Argument(help="提示词名称")],
) -> None:
    """显示某条提示词正文。"""
    typer.echo(read_prompt(name).body)


@app.command("save")
@handle_domain_errors
def prompt_save(
    name: Annotated[str, typer.Argument(help="提示词名称（文件名即名称）")],
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
    """保存提示词（不存在即新建、已存在即覆盖——旧版自动进 _history 滚动备份）。"""
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
    save_prompt(Prompt(name=name, description=description, body=body))
    typer.secho(f"已保存提示词 {name!r}", fg=typer.colors.GREEN)


@app.command("rm")
@handle_domain_errors
def prompt_rm(
    name: Annotated[str, typer.Argument(help="提示词名称")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过删除确认")] = False,
) -> None:
    """删除提示词（需确认；_history 里的历史版本不受影响）。"""
    if not yes and not typer.confirm(f"确认删除提示词 {name!r}？"):
        raise typer.Abort()
    delete_prompt(name)
    typer.secho(f"已删除提示词 {name!r}", fg=typer.colors.GREEN)
