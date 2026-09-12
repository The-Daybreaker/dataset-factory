"""配置命令：``dsf config set`` / ``dsf config show``——端点与密钥的 CLI 侧入口。

读写都经 llm 提供的配置接口（全项目只有 llm 接触端点配置与密钥文件）；密钥交互输入
不回显、不给命令行参数位（避免密钥进 shell 历史，PRD 验收 7）。多配置语义：``set``
更新当前使用的配置，一套都没有时创建 default 配置并启用；多套配置的管理子命令
（列表 / 删除 / 切换）随后续任务补齐。
"""

from __future__ import annotations

from typing import Annotated

import typer

from ..llm import (
    MIGRATED_CONFIG_NAME,
    ConfigError,
    SecretValue,
    active_config_name,
    create_config,
    describe_config,
    has_config,
    set_active_config,
    update_config,
)
from .errors import handle_domain_errors

app = typer.Typer(
    help="端点配置管理（base_url / 模型名 / API 密钥）", no_args_is_help=True
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
        name = MIGRATED_CONFIG_NAME
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
        f"base_url: {desc.base_url or '（未配置——dsf config set 设置或在 Web 设置页添加）'}"
    )
    typer.echo(
        f"model:    {desc.model or '（未配置——dsf config set 设置或在 Web 设置页添加）'}"
    )
    key_label = _KEY_SOURCE_LABELS.get(
        desc.key_source or "",
        "（未配置——dsf config set 设置、设置页添加或环境变量 DSF_API_KEY）",
    )
    typer.echo(
        f"api_key:  已配置（来源：{key_label}）"
        if desc.key_source
        else f"api_key:  {key_label}"
    )
