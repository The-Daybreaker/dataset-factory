"""Skill 库命令：``dsf skill import / list / enable / disable / rm``——直连 skills 数据域。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..skills import (
    delete_skill,
    import_skill,
    list_skills,
    set_enabled,
)
from .errors import handle_domain_errors

app = typer.Typer(help="Skill 包管理（agentskills.io 标准）", no_args_is_help=True)


@app.command("import")
@handle_domain_errors
def skill_import(
    source: Annotated[Path, typer.Argument(help="skill 目录路径（含 SKILL.md）")],
) -> None:
    """导入 skill 包（整目录复制进库、自包含；默认启用）。"""
    result = import_skill(source)
    size_kb = result.total_bytes / 1024
    typer.secho(
        f"已导入 skill {result.skill.name!r}（{size_kb:.1f} KiB——skill 全文将注入打标请求，"
        "体积偏大时留意 token 消耗）",
        fg=typer.colors.GREEN,
    )


@app.command("list")
@handle_domain_errors
def skill_list() -> None:
    """列出全部 skill（启用状态 + 描述）。"""
    skills = list_skills()
    if not skills:
        typer.echo("（skill 库为空——dsf skill import <目录> 导入）")
        return
    for item in skills:
        state = "启用" if item.enabled else "停用"
        description = item.description or "（无描述）"
        typer.echo(f"[{state}] {item.name}\t{description}")


@app.command("enable")
@handle_domain_errors
def skill_enable(
    name: Annotated[str, typer.Argument(help="skill 名称")],
) -> None:
    """启用 skill（打标时注入全文）。"""
    set_enabled(name, True)
    typer.secho(f"已启用 skill {name!r}", fg=typer.colors.GREEN)


@app.command("disable")
@handle_domain_errors
def skill_disable(
    name: Annotated[str, typer.Argument(help="skill 名称")],
) -> None:
    """停用 skill（保留在库中，打标时不注入）。"""
    set_enabled(name, False)
    typer.secho(
        f"已停用 skill {name!r}（包保留在库中，dsf skill enable 随时启用）",
        fg=typer.colors.GREEN,
    )


@app.command("rm")
@handle_domain_errors
def skill_rm(
    name: Annotated[str, typer.Argument(help="skill 名称")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过删除确认")] = False,
) -> None:
    """删除 skill（整目录移除；只想临时收起请用 disable）。"""
    if not yes and not typer.confirm(
        f"确认删除 skill {name!r}？（整目录移除，无法从库内恢复）"
    ):
        raise typer.Abort()
    delete_skill(name)
    typer.secho(f"已删除 skill {name!r}", fg=typer.colors.GREEN)
