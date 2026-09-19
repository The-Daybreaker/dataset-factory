"""配置命令：``dsf config`` 子命令组——端点多配置的 CLI 侧入口。

读写都经 llm 提供的配置接口（全项目只有 llm 接触端点配置与密钥文件）；密钥交互输入
不回显、不给命令行参数位（避免密钥进 shell 历史，PRD 验收 7）。命令一览：

- ``set``：更新当前使用的配置（一套都没有时创建 default 并启用）；
- ``show``：查看当前使用的配置；
- ``list``：列出全部配置（* 标记当前使用）；
- ``add``：新增一套配置；
- ``remove``：删除一套配置（当前使用中的需先切换）；
- ``use``：把一套配置设为当前使用；
- ``test``：发一个极小的真实请求测试连通性（不必改配置）；
- ``params``：查看 / 整体替换一套配置的请求参数（与 Web 设置页「高级参数」同一份配置）。
"""

from __future__ import annotations

import json
from typing import Annotated, cast

import typer

from ..llm import (
    DEFAULT_CONFIG_NAME,
    ENV_API_KEY,
    SUPPORTED_API_FORMAT,
    ConfigError,
    EndpointConfig,
    SecretValue,
    active_config_name,
    create_config,
    delete_config,
    describe_config,
    env_api_key,
    first_api_key,
    has_config,
    list_configs,
    probe_endpoint,
    read_config_data,
    read_stored_api_key,
    set_active_config,
    update_config,
    validated_request_params,
)
from .errors import handle_domain_errors
from .operations import confirm_or_abort

app = typer.Typer(
    help="端点配置管理（base_url / 模型名 / API 密钥，支持多套配置）",
    no_args_is_help=True,
)

_KEY_SOURCE_LABELS = {"env": "环境变量 DSF_API_KEY", "file": "credentials 文件"}


@app.command("set")
@handle_domain_errors
def config_set(
    base_url: Annotated[
        str,
        typer.Option(
            "--base-url", help="OpenAI 兼容端点地址（如 https://api.example.com/v1）"
        ),
    ],
    model: Annotated[str, typer.Option("--model", help="模型名")],
) -> None:
    """设置端点配置：更新当前使用的配置（没有则创建 default 并启用）；API 密钥交互输入（不回显）。"""
    api_key = SecretValue(typer.prompt("API key", hide_input=True))
    active = active_config_name()
    if active is not None and has_config(active):
        name = active
        update_config(name, base_url=base_url, model=model, api_key=api_key)
    else:
        name = DEFAULT_CONFIG_NAME
        create_config(name, base_url=base_url, model=model, api_key=api_key)
        # create_config 只在指针缺失时自动激活；指针悬空（指向已被手动删除的配置）时
        # 这里显式补一次，保证 set 完一定可用。
        set_active_config(name)
    typer.secho(
        f"已写入配置 {name}：base_url={base_url} model={model}"
        "（密钥存该配置的 credentials 文件）",
        fg=typer.colors.GREEN,
    )


@app.command("show")
@handle_domain_errors
def config_show() -> None:
    """查看当前使用的配置（密钥只显示来源，绝不显示内容）。"""
    try:
        desc = describe_config()
    except ConfigError as exc:
        typer.secho(f"错误：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"配置名:   {desc.name or '（未配置）'}")
    typer.echo(
        f"base_url: {desc.base_url or '（未配置——dsf config add 添加或在 Web 设置页添加）'}"
    )
    typer.echo(
        f"model:    {desc.model or '（未配置——dsf config add 添加或在 Web 设置页添加）'}"
    )
    key_label = _KEY_SOURCE_LABELS.get(
        desc.key_source or "",
        "（未配置——dsf config add 添加、设置页添加或环境变量 DSF_API_KEY）",
    )
    typer.echo(
        f"api_key:  已配置（来源：{key_label}）"
        if desc.key_source
        else f"api_key:  {key_label}"
    )


@app.command("list")
@handle_domain_errors
def config_list() -> None:
    """列出全部端点配置（行首 * 标记当前使用；密钥只报有无）。"""
    configs = list_configs()
    if not configs:
        typer.echo("（还没有端点配置——dsf config add 添加）")
        return
    for item in configs:
        marker = "*" if item.is_active else " "
        key_label = "密钥已配置" if item.has_api_key else "密钥未配置"
        typer.echo(f"{marker} {item.name}\t{item.model}\t{item.base_url}\t{key_label}")


@app.command("add")
@handle_domain_errors
def config_add(
    name: Annotated[str, typer.Argument(help="配置名（即数据目录名）")],
    base_url: Annotated[
        str,
        typer.Option(
            "--base-url", help="OpenAI 兼容端点地址（如 https://api.example.com/v1）"
        ),
    ],
    model: Annotated[str, typer.Option("--model", help="模型名")],
) -> None:
    """新增一套端点配置；API 密钥交互输入（留空跳过，之后可用 set 补配）。"""
    raw_key = typer.prompt(
        "API key（留空跳过）", hide_input=True, default="", show_default=False
    )
    api_key = SecretValue(raw_key.strip()) if raw_key.strip() else None
    final = create_config(name, base_url=base_url, model=model, api_key=api_key)
    suffix = "，已设为当前使用" if active_config_name() == final else ""
    if api_key is None:
        typer.secho(
            f"已添加配置 {final}{suffix}（未配密钥——打标前用 dsf config set 补配，"
            f"或设环境变量 DSF_API_KEY）",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"已添加配置 {final}{suffix}（密钥存该配置的 credentials 文件）",
            fg=typer.colors.GREEN,
        )


@app.command("remove")
@handle_domain_errors
def config_remove(
    name: Annotated[str, typer.Argument(help="配置名")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过删除确认")] = False,
) -> None:
    """删除一套端点配置（连同其密钥；当前使用中的配置需先切换再删）。"""
    confirm_or_abort(f"确认删除端点配置 {name!r}（含其密钥文件）？", yes)
    delete_config(name)
    typer.secho(f"已删除端点配置 {name!r}", fg=typer.colors.GREEN)


@app.command("use")
@handle_domain_errors
def config_use(
    name: Annotated[str, typer.Argument(help="配置名")],
) -> None:
    """把一套配置设为当前使用（对新请求立即生效）。"""
    set_active_config(name)
    typer.secho(f"当前使用的配置已切换为 {name}", fg=typer.colors.GREEN)


@app.command("test")
@handle_domain_errors
def config_test(
    name: Annotated[
        str | None, typer.Argument(help="配置名（缺省 = 当前使用的配置）")
    ] = None,
) -> None:
    """测试端点连通性：发一个极小的真实请求（15 秒超时、max_tokens=1），不必先改配置。"""
    resolved = name if name is not None else active_config_name()
    if resolved is None:
        typer.secho(
            "错误：没有可测试的端点配置——dsf config add 添加，或带配置名参数指定。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if not has_config(resolved):
        typer.secho(
            f"错误：端点配置 {resolved!r} 不存在；用 dsf config list 查看现有配置。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    data = read_config_data(resolved)
    key = first_api_key(env_api_key(), read_stored_api_key(resolved))
    if key is None:
        typer.secho(
            f"错误：配置 {resolved!r} 没有已存密钥，也未设环境变量 {ENV_API_KEY}；"
            "请先 dsf config set 补配密钥再测试。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    result = probe_endpoint(
        EndpointConfig(
            base_url=cast(str, data["base_url"]),
            model=cast(str, data["model"]),
            api_key=key,
        )
    )
    if result.ok:
        typer.secho(
            f"连接成功（{result.latency_ms:.0f} ms）：{resolved} → {cast(str, data['model'])}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"连接失败（{result.latency_ms:.0f} ms）：{result.message}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)


@app.command("params")
@handle_domain_errors
def config_params(
    name: Annotated[
        str | None, typer.Argument(help="配置名（缺省 = 当前使用的配置）")
    ] = None,
    set_json: Annotated[
        str | None,
        typer.Option(
            "--set",
            help=(
                "以 JSON 对象整体替换该配置的请求参数（如 '{\"temperature\": 0.7}'，"
                "'{}' = 清空全部）。只认 temperature / top_p / max_tokens / extra_body /"
                " timeout_seconds / max_retries 六个键，其余键丢弃（厂商专有参数放 extra_body）"
            ),
        ),
    ] = None,
) -> None:
    """查看或设置端点配置的请求参数（生成 + 传输；与 Web 设置页「高级参数」同一份配置）。"""
    resolved = name if name is not None else active_config_name()
    if resolved is None:
        typer.secho(
            "错误：没有可用的端点配置——dsf config add 添加，或带配置名参数指定。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if not has_config(resolved):
        typer.secho(
            f"错误：端点配置 {resolved!r} 不存在；用 dsf config list 查看现有配置。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if set_json is None:
        _echo_request_params(resolved)
        return
    params = _parse_params_json(set_json)
    data = read_config_data(resolved)
    # api_format / 密钥原样沿用（update_config 的 None 密钥 = 不动 credentials）；
    # 只把请求参数块换成 --set 给的整份（与 Web 高级参数区的更新语义一致）。
    update_config(
        resolved,
        base_url=cast(str, data["base_url"]),
        model=cast(str, data["model"]),
        api_format=cast("str | None", data.get("api_format")) or SUPPORTED_API_FORMAT,
        request_params=params,
    )
    updated = validated_request_params(read_config_data(resolved), resolved)
    if updated:
        typer.secho(f"已更新配置 {resolved} 的请求参数：", fg=typer.colors.GREEN)
        typer.echo(json.dumps(updated, ensure_ascii=False, indent=2))
    else:
        typer.secho(
            f"已清空配置 {resolved} 的请求参数（全部用内置默认）。",
            fg=typer.colors.GREEN,
        )


def _echo_request_params(name: str) -> None:
    """打印一套配置当前生效的请求参数（查看用；未设置时给设置指引）。"""
    params = validated_request_params(read_config_data(name), name)
    if not params:
        typer.echo(
            f"配置 {name} 未设置请求参数（全部用内置默认）；"
            f"用 dsf config params {name} --set '<JSON>' 设置。"
        )
        return
    typer.echo(f"配置 {name} 的请求参数：")
    typer.echo(json.dumps(params, ensure_ascii=False, indent=2))


def _parse_params_json(raw: str) -> dict[str, object]:
    """解析 --set 的 JSON 文本：必须是对象（键值对），否则按用法错误退出（退出码 2）。"""
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            f"--set 的值不是合法 JSON：{exc.msg}；"
            "请传 JSON 对象，如 '{\"temperature\": 0.7}'。"
        ) from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter(
            "--set 的值必须是 JSON 对象（键值对），如 '{\"temperature\": 0.7}'。"
        )
    return cast(dict[str, object], parsed)
