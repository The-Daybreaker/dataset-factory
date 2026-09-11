"""配置命令：``dsf config set`` / ``dsf config show``——端点与密钥的 CLI 侧入口。

读写都经 llm 提供的配置接口（全项目只有 llm 接触这两个文件）；密钥交互输入不回显，
不给命令行参数位（避免密钥进 shell 历史，PRD 验收 7）。
"""

from __future__ import annotations

from typing import Annotated

import typer

from ..llm import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    describe_config,
    write_config,
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
    """设置端点配置；API 密钥随后交互输入（不回显，写入 credentials 文件）。"""
    api_key = typer.prompt("API key", hide_input=True)
    write_config(
        EndpointConfig(base_url=base_url, model=model, api_key=SecretValue(api_key))
    )
    typer.secho(
        f"已写入：base_url={base_url} model={model}（密钥存 credentials 文件）",
        fg=typer.colors.GREEN,
    )


@app.command("show")
@handle_domain_errors
def config_show() -> None:
    """查看当前配置（密钥只显示来源，绝不显示内容）。"""
    try:
        base_url, model, key_source = describe_config()
    except ConfigError as exc:
        typer.secho(f"错误：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"base_url: {base_url or '（未配置——dsf config set 设置）'}")
    typer.echo(f"model:    {model or '（未配置——dsf config set 设置）'}")
    key_label = _KEY_SOURCE_LABELS.get(
        key_source or "", "（未配置——dsf config set 设置或环境变量 DSF_API_KEY）"
    )
    typer.echo(
        f"api_key:  已配置（来源：{key_label}）"
        if key_source
        else f"api_key:  {key_label}"
    )
