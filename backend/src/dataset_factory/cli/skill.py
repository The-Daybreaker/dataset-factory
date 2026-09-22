"""Skill 库命令：``dsf skill import / list / files / read / enable / disable / rename / rm``——直连 skills 数据域。

寻址口径（2026-09-23 ID 化）：skill 参数接受 **ID 或唯一显示名**（ID 优先），
解析收敛在 _resolve_ref。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..skills import (
    delete_skill,
    import_skill,
    list_skill_files,
    list_skills,
    read_skill_file,
    rename_skill,
    set_enabled,
    skill_id_by_display_name,
)
from .errors import handle_domain_errors
from .operations import confirm_or_abort

app = typer.Typer(help="Skill 包管理（agentskills.io 标准）", no_args_is_help=True)


def _resolve_ref(ref: str) -> str:
    """把「skill ID 或唯一显示名」解析成 ID（ID 优先）。

    Raises:
        typer.Exit: 解析不到（不存在 / 显示名重名不唯一），给出可操作提示。
    """
    matches = [skill for skill in list_skills() if skill.id == ref]
    if matches:
        return ref
    by_name = skill_id_by_display_name(ref)
    if by_name is not None:
        return by_name
    typer.secho(
        f"错误：skill {ref!r} 不存在（或显示名重名不唯一）；"
        "用 dsf skill list 查看各包的 ID。",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(1)


@app.command("import")
@handle_domain_errors
def skill_import(
    source: Annotated[Path, typer.Argument(help="skill 目录路径（含 SKILL.md）")],
) -> None:
    """导入 skill 包（整目录复制进库、自包含；默认启用；同显示名可并存）。"""
    result = import_skill(source)
    size_kb = result.total_bytes / 1024
    typer.secho(
        f"已导入 skill {result.skill.name!r}（{result.skill.id}，{size_kb:.1f} KiB——"
        "skill 全文将注入打标请求，体积偏大时留意 token 消耗）",
        fg=typer.colors.GREEN,
    )


@app.command("list")
@handle_domain_errors
def skill_list() -> None:
    """列出全部 skill（ID + 启用状态 + 描述）。"""
    skills = list_skills()
    if not skills:
        typer.echo("（skill 库为空——dsf skill import <目录> 导入）")
        return
    for item in skills:
        state = "启用" if item.enabled else "停用"
        description = item.description or "（无描述）"
        typer.echo(f"[{state}] {item.name} ({item.id})\t{description}")


@app.command("enable")
@handle_domain_errors
def skill_enable(
    ref: Annotated[str, typer.Argument(help="skill ID 或唯一显示名")],
) -> None:
    """启用 skill（打标时注入全文）。"""
    sid = _resolve_ref(ref)
    set_enabled(sid, True)
    typer.secho(f"已启用 skill {sid!r}", fg=typer.colors.GREEN)


@app.command("files")
@handle_domain_errors
def skill_files(
    ref: Annotated[str, typer.Argument(help="skill ID 或唯一显示名")],
) -> None:
    """列出技能包内全部文件（角色标注；SKILL.md 与 references/ 可 read 预览）。"""
    sid = _resolve_ref(ref)
    entries = list_skill_files(sid)
    for entry in entries:
        suffix = "" if entry.previewable else "\t(不可预览)"
        typer.echo(f"{entry.path}\t{entry.role}{suffix}")


@app.command("read")
@handle_domain_errors
def skill_read(
    ref: Annotated[str, typer.Argument(help="skill ID 或唯一显示名")],
    path: Annotated[
        str, typer.Argument(help="包内相对路径（仅 SKILL.md 与 references/ 下文件）")
    ],
) -> None:
    """读技能包内一个可预览文件的文本内容（assets / scripts 不开放）。"""
    sid = _resolve_ref(ref)
    typer.echo(read_skill_file(sid, path))


@app.command("disable")
@handle_domain_errors
def skill_disable(
    ref: Annotated[str, typer.Argument(help="skill ID 或唯一显示名")],
) -> None:
    """停用 skill（保留在库中，打标时不注入）。"""
    sid = _resolve_ref(ref)
    set_enabled(sid, False)
    typer.secho(
        f"已停用 skill {sid!r}（包保留在库中，dsf skill enable 随时启用）",
        fg=typer.colors.GREEN,
    )


@app.command("rename")
@handle_domain_errors
def skill_rename(
    ref: Annotated[str, typer.Argument(help="skill ID 或唯一显示名")],
    new_name: Annotated[str, typer.Argument(help="新显示名（可改、允许重名）")],
) -> None:
    """改显示名（只写 SKILL.md frontmatter 的 name 字段；目录名是 ID、永不动）。"""
    sid = _resolve_ref(ref)
    rename_skill(sid, new_name)
    typer.secho(
        f"已把 {sid} 的显示名改为 {new_name!r}",
        fg=typer.colors.GREEN,
    )


@app.command("rm")
@handle_domain_errors
def skill_rm(
    ref: Annotated[str, typer.Argument(help="skill ID 或唯一显示名")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过删除确认")] = False,
) -> None:
    """删除 skill（整目录移除；只想临时收起请用 disable）。"""
    sid = _resolve_ref(ref)
    confirm_or_abort(f"确认删除 skill {sid!r}？（整目录移除，无法从库内恢复）", yes)
    delete_skill(sid)
    typer.secho(f"已删除 skill {sid!r}", fg=typer.colors.GREEN)
