"""批次管理与重试、导出排除名单的命令入口。"""

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from ..llm import list_configs
from ..prompts import list_prompts
from ..runs import (
    RetryItemNotEligibleError,
    add_retry_items,
    clear_retry_list,
    read_retry_list,
    remove_retry_items,
    retry_rejections,
)
from ..runs.control import current_run, request_stop
from ..runs.items import ITEM_GROUPS, build_item_view
from ..runs.runner import remove_batch
from ..skills import list_skills
from ..strategies import (
    add_exclusions,
    apply_library_strategy,
    create_batch,
    get_batch,
    list_batches,
    list_strategies,
    parse_seq,
    product_count,
    read_snapshot,
    remove_exclusions,
    set_batch_active,
    update_batch,
)
from .errors import handle_domain_errors
from .operations import confirm_action, print_result, registered_root
from .workdir import registered_id

app = typer.Typer(help="批次管理", no_args_is_help=True)
retry_app = typer.Typer(help="重试列表", no_args_is_help=True)
app.add_typer(retry_app, name="retry")


def _choose(label: str, available: list[str]) -> str:
    if not available:
        raise typer.BadParameter(f"没有可用的{label}，请先配置。")
    typer.echo(f"{label}：" + "、".join(available), err=True)
    chosen = typer.prompt(label, err=True)
    if chosen not in available:
        raise typer.BadParameter(f"{label}不存在：{chosen}")
    return chosen


@app.command("add")
@handle_domain_errors
def add(
    path: Path,
    library: Annotated[str | None, typer.Option("--from-library")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    description: Annotated[str, typer.Option("--desc")] = "",
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    prompt: Annotated[str | None, typer.Option("--prompt")] = None,
    skills: Annotated[list[str] | None, typer.Option("--skill")] = None,
) -> None:
    """从库复制策略或从零配置新批次；脚本须显式给出名称、端点与提示词。"""
    root = registered_root(registered_id(path))
    if library is not None:
        if endpoint is not None or prompt is not None or skills is not None:
            raise typer.BadParameter("从库应用时不能同时提供组合参数。")
        entries = list_strategies()
        matches = [entry for entry in entries if entry.id == library]
        if not matches:
            matches = [entry for entry in entries if entry.name == library]
        if len(matches) != 1:
            raise typer.BadParameter("库策略不存在或显示名重名，请使用明确 ID。")
        result = apply_library_strategy(
            root, matches[0].id, name=name, description=description or None
        )
    else:
        batch_name = name
        batch_endpoint = endpoint
        batch_prompt = prompt
        if batch_name is None or batch_endpoint is None or batch_prompt is None:
            if not sys.stdin.isatty():
                raise typer.BadParameter(
                    "非交互新建须提供 --name、--endpoint、--prompt，或 --from-library。"
                )
            batch_name = batch_name or typer.prompt("批次名称", err=True)
            batch_endpoint = batch_endpoint or _choose(
                "端点", [entry.name for entry in list_configs()]
            )
            batch_prompt = batch_prompt or _choose(
                "提示词", [entry.name for entry in list_prompts()]
            )
            if skills is None:
                skills = [
                    entry.name
                    for entry in list_skills()
                    if entry.enabled
                    and typer.confirm(f"启用 Skill {entry.name}？", err=True)
                ]
        if not batch_name.strip():
            raise typer.BadParameter("批次名称不能为空。")
        result = create_batch(
            root,
            name=batch_name,
            description=description,
            endpoint=batch_endpoint,
            prompt=batch_prompt,
            skills=skills or [],
        )
    print_result(asdict(result))


def batch_location(path: Path, batch: str) -> tuple[Path, int]:
    """解析已登记目录与批次，读写名单前统一确认批次存在。"""
    root = registered_root(registered_id(path))
    seq = parse_seq(batch)
    get_batch(root, seq)
    return root, seq


@app.command("list")
@handle_domain_errors
def list_all(path: Path) -> None:
    """列出目录全部批次，包括已隐藏批次。"""
    root = registered_root(registered_id(path))
    print_result([asdict(entry) for entry in list_batches(root)])


@app.command("show")
@handle_domain_errors
def show(path: Path, batch: str) -> None:
    """查看批次元数据、产物数量与自包含策略快照。"""
    root, seq = batch_location(path, batch)
    print_result(
        {
            "batch": asdict(get_batch(root, seq)),
            "product_count": product_count(root, seq),
            "snapshot": read_snapshot(root, seq).to_json(),
        }
    )


@app.command("edit")
@handle_domain_errors
def edit(
    path: Path,
    batch: str,
    name: Annotated[str | None, typer.Option("--name")] = None,
    description: Annotated[str | None, typer.Option("--desc")] = None,
) -> None:
    """修改批次显示名称与描述。"""
    if name is None and description is None:
        raise typer.BadParameter("请提供 --name 或 --desc。")
    root, seq = batch_location(path, batch)
    print_result(asdict(update_batch(root, seq, name=name, description=description)))


@app.command("hide")
@handle_domain_errors
def hide(
    path: Path,
    batch: str,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """隐藏批次；正在运行时在当前条目安全收尾后中断。"""
    root, seq = batch_location(path, batch)
    confirm_action("隐藏该批次并中断其当前运行，保留已完成产物，继续？", yes)
    print_result(asdict(set_batch_active(root, seq, False)))


@app.command("unhide")
@handle_domain_errors
def unhide(path: Path, batch: str) -> None:
    """重新显示批次，之后可继续跑批。"""
    root, seq = batch_location(path, batch)
    print_result(asdict(set_batch_active(root, seq, True)))


@app.command("items")
@handle_domain_errors
def items(
    path: Path,
    batch: str,
    group: Annotated[str | None, typer.Option("--group")] = None,
    query: Annotated[str, typer.Option("--query", "-q")] = "",
) -> None:
    """查看六组条目，支持按分组与文件名过滤。"""
    if group is not None and group not in ITEM_GROUPS:
        raise typer.BadParameter("分组须为 " + ", ".join(ITEM_GROUPS))
    root, seq = batch_location(path, batch)
    view = build_item_view(root, seq, query=query)
    print_result(
        asdict(view) if group is None else [asdict(row) for row in view.groups[group]]
    )


@app.command("status")
@handle_domain_errors
def status(path: Path, batch: str) -> None:
    """读取指定批次的实时运行状态，覆盖 CLI 与 Web 发起的运行。"""
    root, seq = batch_location(path, batch)
    print_result(current_run(root, seq))


@app.command("stop")
@handle_domain_errors
def stop(path: Path, batch: str) -> None:
    """请求当前批次协作停止，等待当前条目安全收尾。"""
    root, seq = batch_location(path, batch)
    print_result({"run_id": request_stop(root, seq), "stop_requested": True})


@app.command("rm")
@handle_domain_errors
def remove(
    path: Path,
    batch: str,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """删除批次与配对产物，保留素材和运行历史。"""
    root, seq = batch_location(path, batch)
    count = product_count(root, seq)
    confirm_action(
        f"删除批次 {batch}、其 {count} 份产物与快照？素材和运行历史保留。", yes
    )
    print_result({"deleted": batch, "product_count": remove_batch(root, seq)})


@retry_app.command("add")
@handle_domain_errors
def retry_add(path: Path, batch: str, items: list[str]) -> None:
    """把已完成或可重试失败条目加入名单；不合资格时整体拒绝。"""
    root, seq = batch_location(path, batch)
    rejected = retry_rejections(root, seq, items)
    if rejected:
        raise RetryItemNotEligibleError(
            "部分条目不可加入重试列表：" + str(rejected), rejections=rejected
        )
    print_result(add_retry_items(root, seq, items))


@retry_app.command("remove")
@handle_domain_errors
def retry_remove(path: Path, batch: str, items: list[str]) -> None:
    """从重试列表移出指定条目。"""
    root, seq = batch_location(path, batch)
    print_result(remove_retry_items(root, seq, items))


@retry_app.command("list")
@handle_domain_errors
def retry_list(path: Path, batch: str) -> None:
    """显示持久保存的重试列表。"""
    root, seq = batch_location(path, batch)
    print_result(read_retry_list(root, seq))


@retry_app.command("clear")
@handle_domain_errors
def retry_clear(path: Path, batch: str) -> None:
    """清空本批次重试列表，其他批次保持原样。"""
    root, seq = batch_location(path, batch)
    clear_retry_list(root, seq)
    print_result([])


@app.command("exclude")
@handle_domain_errors
def exclude(
    path: Path,
    batch: str,
    items: list[str],
    undo: Annotated[bool, typer.Option("--undo")] = False,
) -> None:
    """排除指定条目的打包资格，或撤销此前排除。"""
    root, seq = batch_location(path, batch)
    operation = remove_exclusions if undo else add_exclusions
    print_result(operation(root, seq, items))
