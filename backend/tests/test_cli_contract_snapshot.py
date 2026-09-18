"""CLI 对外口径快照件：把「命令名 / 参数 / stdout 结构 / 退出码」钉成 golden 文件。

为什么要这一件：``cli/main.py`` 的模块 docstring 写明退出码约定是「外部 agent 按此解析」的
对外承诺，属于本轮重构的红线之一。单元测试各自断言一部分行为，没有一处能回答「全部命令的
对外形迹有没有变」。这里做一次全量采集，之后只读比对：任何一条命令的帮助文本、参数面、
退出码或输出结构发生变化，本件立刻指出是哪一条。

维护方式：确实需要演进 CLI 口径时，跑
``DSF_UPDATE_CLI_SNAPSHOTS=1 uv run pytest tests/test_cli_contract_snapshot.py``
重采黄金文件，并在提交信息里说明改了哪条命令的对外口径。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import typer.main
import typer.rich_utils
from click import Command
from click.testing import CliRunner
from typer.core import TyperGroup
from typer.main import Typer

from dataset_factory.cli.main import app

if TYPE_CHECKING:
    from click.testing import Result

GOLDEN_DIR = Path(__file__).parent / "cli-golden"
_UPDATE = os.environ.get("DSF_UPDATE_CLI_SNAPSHOTS") == "1"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RUNNER = CliRunner()
_SNAPSHOT_WIDTH = 80
# 面板边框字形归一：圆角还是直角由终端的 Unicode 能力决定（Windows 传统控制台画不出圆角，
# rich 自动降级），不是 CLI 对外的承诺；比的是命令名、参数、文案、列宽与退出码。
_BOX_GLYPHS = str.maketrans("╭╮╰╯", "┌┐└┘")


def _command_tree(
    typer_obj: Typer, prefix: tuple[str, ...]
) -> Iterator[tuple[str, ...]]:
    """按 Typer 注册面递归列出全部叶子命令路径（分组名与命令名都用对外拼写）。

    Args:
        typer_obj: 当前层的应用对象。
        prefix: 到当前层为止的命令路径。

    Yields:
        叶子命令的完整路径元组，例如 ``("workdir", "reimport")``。
    """
    for command in typer_obj.registered_commands:
        name = command.name or getattr(command.callback, "__name__", "").replace(
            "_", "-"
        )
        yield (*prefix, name)
    for group in typer_obj.registered_groups:
        yield from _command_tree(
            cast("Typer", group.typer_instance), (*prefix, cast("str", group.name))
        )


def _invoke(cli: TyperGroup, args: list[str]) -> Result:
    """跑一次命令并返回结果（不吞异常，让程序 bug 原样炸出来而不是混进快照）。

    Args:
        cli: 根命令对象。
        args: 参数列表（不含程序名）。

    Returns:
        click 的 ``Result``，含退出码与合并输出。
    """
    return _RUNNER.invoke(cast("Command", cli), args, catch_exceptions=False)


def _normalize(text: str, data_root: Path) -> str:
    """把采集到的输出折成跨平台一致的可比文本。

    做四件事：剥掉颜色转义（Windows/Linux 终端差异）、面板边框字形归一（圆角/直角同为终端能力
    差异）、把临时数据根换成占位符（每台机器路径不同）、统一行尾与行尾空白（快照比对是逐字节
    的）。

    Args:
        text: 原始输出。
        data_root: 本次运行隔离出来的数据根路径。

    Returns:
        规范化后的文本，可直接写入黄金文件。
    """
    stripped = _ANSI_RE.sub("", text).translate(_BOX_GLYPHS)
    replaced = stripped.replace(str(data_root), "<DATA_ROOT>")
    replaced = replaced.replace(str(data_root).replace("\\", "/"), "<DATA_ROOT>")
    lines = [line.rstrip() for line in replaced.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip() + "\n"


def _render(result: Result) -> str:
    """把一次调用的退出码与输出拼成一段文本。

    Args:
        result: click 测试结果对象。

    Returns:
        形如「退出码：0 / ---- 输出 ---- / 正文」的快照正文。
    """
    return f"退出码：{result.exit_code}\n---- 输出 ----\n{result.output}"


def _check(name: str, actual: str) -> None:
    """与黄金文件比对；``_UPDATE`` 模式下改为落盘新值（首采与有意演进时用）。

    Args:
        name: 黄金文件名（不含目录）。
        actual: 本次采集到的规范化输出。
    """
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDEN_DIR / name
    if _UPDATE:
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"缺少黄金文件 {name}——首采请跑 DSF_UPDATE_CLI_SNAPSHOTS=1"
    expected = path.read_text(encoding="utf-8")
    assert expected == actual, (
        f"CLI 对外口径变了：{name}\n---- 期望 ----\n{expected}\n---- 实际 ----\n{actual}"
    )


_COMMAND_PATHS = sorted(_command_tree(app, ()))


@pytest.fixture(scope="module", autouse=True)
def pinned_rich_rendering() -> Iterator[None]:
    """把 typer 的 rich 渲染参数钉死，让快照只反映命令自身口径、不反映跑它的机器。

    typer 一见 ``GITHUB_ACTIONS`` / ``FORCE_COLOR`` 就把控制台切进「终端模式」：面板边框从
    ``┌┐└┘`` 换成 ``╭╮╰╯``，宽度也从固定值换成实时探测值——同一份代码在本地和 CI 上会渲染
    出两套字节。固定成「非终端 + 80 列」后，两平台采集结果一致。

    Yields:
        渲染参数被钉住期间让出控制权，退出时自动还原。
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(typer.rich_utils, "FORCE_TERMINAL", False)
        patch.setattr(typer.rich_utils, "MAX_WIDTH", _SNAPSHOT_WIDTH)
        yield


@pytest.fixture(scope="module")
def cli() -> TyperGroup:
    """整模块共用的 click 命令对象（Typer 应用只在这里转一次 click 树）。"""
    return cast("TyperGroup", typer.main.get_command(app))


def test_cli_command_tree_snapshot() -> None:
    """命令清单本身要固定——新增、改名、删命令都会在这里先被撞出来。"""
    body = "\n".join(" ".join(path) for path in _COMMAND_PATHS)
    _check("command-tree.txt", f"共 {len(_COMMAND_PATHS)} 条命令\n{body}\n")


@pytest.mark.parametrize("path", _COMMAND_PATHS, ids=lambda p: ".".join(p))
def test_command_help_snapshot(cli: TyperGroup, path: tuple[str, ...]) -> None:
    """每条命令的 ``--help`` 全文要与黄金一致（参数名、默认值、说明文案都在里面）。"""
    result = _invoke(cli, [*list(path), "--help"])
    _check(f"help-{'.'.join(path)}.txt", _normalize(_render(result), Path.cwd()))


def test_readonly_commands_output_snapshot(
    cli: TyperGroup, temp_data_root: Path
) -> None:
    """空数据根下各只读命令的输出与退出码快照（外部 agent 解析的就是这一段）。"""
    cases = (
        ["workdir", "list"],
        ["prompt", "list"],
        ["skill", "list"],
        ["session", "list"],
        ["strategy", "list"],
        ["config", "show"],
        ["batch", "list", "--workdir", "missing"],
    )
    collected = [
        f"$ dsf {' '.join(path)}\n{_normalize(_render(_invoke(cli, path)), temp_data_root)}"
        for path in cases
    ]
    _check("outputs-empty-dataroot.txt", "\n".join(collected))


def test_error_and_usage_exit_codes_snapshot(
    cli: TyperGroup, temp_data_root: Path
) -> None:
    """用法错误与运行失败的退出码约定（0 / 1 / 2）是对外承诺，逐条钉住。"""
    cases = (
        ["no-such-command"],
        ["prompt", "show"],
        ["prompt", "show", "不存在的提示词"],
        ["workdir", "show", "missing-id"],
        ["batch", "status", "--workdir", "missing", "--batch", "s1"],
        ["config", "remove"],
    )
    collected = [
        f"$ dsf {' '.join(path)}\n{_normalize(_render(_invoke(cli, path)), temp_data_root)}"
        for path in cases
    ]
    _check("outputs-error-cases.txt", "\n".join(collected))
