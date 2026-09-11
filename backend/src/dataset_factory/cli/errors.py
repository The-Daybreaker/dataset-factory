"""CLI 的统一错误处理：核心库域异常 → stderr 可操作消息 + 退出码 1。

独立成模块是避免循环导入：main.py 组装 app 时 import 各命令模块，各命令模块又要用
本模块的装饰器——装饰器放这里，两边都只依赖它。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import wraps

import typer

from ..labeling import LabelingError
from ..llm import ConfigError, LLMError
from ..prompts import PromptError
from ..sessions import SessionError
from ..skills import SkillError

# CLI 的失败语义：域异常 → 1；意外异常不拦（带 traceback 退出，fail loud）。用法错误由
# Typer/click 默认给 2。完整退出码表见 main 模块 docstring。
DOMAIN_ERRORS = (
    LabelingError,
    PromptError,
    SkillError,
    SessionError,
    ConfigError,
    LLMError,
)


def handle_domain_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """命令装饰器：核心库的域异常 → stderr 可操作消息 + 退出码 1。

    域异常（各数据域 / 能力层 / 编排层自己的错误类型）的消息已按「哪里错、怎么修」写好，
    这里统一翻译成 CLI 的失败语义；程序 bug（意外异常）不拦——带原始 traceback 退出，
    便于定位（fail loud）。
    """

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except DOMAIN_ERRORS as exc:
            print(f"错误：{exc}", file=sys.stderr)
            raise typer.Exit(1) from exc

    return wrapper
