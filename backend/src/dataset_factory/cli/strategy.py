"""跨目录复用策略库的管理命令。"""

from dataclasses import asdict
from typing import Annotated

import typer

from ..strategies import (
    LibraryStrategy,
    copy_strategy,
    create_strategy,
    delete_strategy,
    get_strategy,
    list_strategies,
    missing_refs,
    rebind_strategy,
    update_strategy,
)
from .errors import handle_domain_errors
from .operations import confirm_action, print_result

app = typer.Typer(help="策略库管理", no_args_is_help=True)


def _view(entry: LibraryStrategy) -> dict[str, object]:
    result = asdict(entry)
    problems = missing_refs(entry)
    result.update(available=not problems, missing_refs=problems)
    return result


def _skills_argument(skills: list[str] | None, clear: bool) -> list[str] | None:
    if skills is not None and clear:
        raise typer.BadParameter("--skill 与 --clear-skills 不能同时使用。")
    return [] if clear else skills


@app.command("add")
@handle_domain_errors
def add(
    name: str,
    endpoint: Annotated[str, typer.Option("--endpoint")],
    prompt: Annotated[str, typer.Option("--prompt")],
    skills: Annotated[list[str] | None, typer.Option("--skill")] = None,
    description: Annotated[str, typer.Option("--desc")] = "",
) -> None:
    """保存一份组合清单，引用的端点、提示词和 Skill 须已存在。"""
    print_result(
        _view(
            create_strategy(
                name=name,
                endpoint=endpoint,
                prompt=prompt,
                skills=skills or [],
                description=description,
            )
        )
    )


@app.command("list")
@handle_domain_errors
def list_all() -> None:
    """列出策略和实时引用健康度，失效策略仍可见。"""
    print_result([_view(entry) for entry in list_strategies()])


@app.command("show")
@handle_domain_errors
def show(strategy_id: str) -> None:
    """按稳定 ID 查看策略。"""
    print_result(_view(get_strategy(strategy_id)))


@app.command("edit")
@handle_domain_errors
def edit(
    strategy_id: str,
    name: Annotated[str | None, typer.Option("--name")] = None,
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    prompt: Annotated[str | None, typer.Option("--prompt")] = None,
    skills: Annotated[list[str] | None, typer.Option("--skill")] = None,
    clear_skills: Annotated[bool, typer.Option("--clear-skills")] = False,
    description: Annotated[str | None, typer.Option("--desc")] = None,
) -> None:
    """只修改明确提供的字段，已有批次快照保持不变。"""
    selected_skills = _skills_argument(skills, clear_skills)
    if all(
        value is None
        for value in (name, endpoint, prompt, selected_skills, description)
    ):
        raise typer.BadParameter("请至少提供一个要修改的字段。")
    current = get_strategy(strategy_id)
    print_result(
        _view(
            update_strategy(
                strategy_id,
                name=current.name if name is None else name,
                endpoint=current.endpoint if endpoint is None else endpoint,
                prompt=current.prompt if prompt is None else prompt,
                skills=current.skills if selected_skills is None else selected_skills,
                description=current.description if description is None else description,
            )
        )
    )


@app.command("copy")
@handle_domain_errors
def copy(strategy_id: str) -> None:
    """复制一份策略，分配新 ID，保留原组合。"""
    print_result(_view(copy_strategy(strategy_id)))


@app.command("rm")
@handle_domain_errors
def remove(
    strategy_id: str,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """删除库策略，已经应用的批次仍可使用快照。"""
    entry = get_strategy(strategy_id)
    confirm_action(f"删除库策略「{entry.name}」？已应用批次保持不变。", yes)
    delete_strategy(strategy_id)
    print_result({"deleted": strategy_id})


@app.command("rebind")
@handle_domain_errors
def rebind(
    strategy_id: str,
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    prompt: Annotated[str | None, typer.Option("--prompt")] = None,
    skills: Annotated[list[str] | None, typer.Option("--skill")] = None,
    clear_skills: Annotated[bool, typer.Option("--clear-skills")] = False,
) -> None:
    """重新指定缺失引用，未指定的引用保持原样。"""
    selected_skills = _skills_argument(skills, clear_skills)
    if endpoint is None and prompt is None and selected_skills is None:
        raise typer.BadParameter("请提供要重新指定的引用。")
    print_result(
        _view(
            rebind_strategy(
                strategy_id, endpoint=endpoint, prompt=prompt, skills=selected_skills
            )
        )
    )
