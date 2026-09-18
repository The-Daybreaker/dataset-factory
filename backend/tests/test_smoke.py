"""Smoke 测试：全量导入包内每一个模块，验证「地基通不通」。

为什么改成动态发现：原来这里硬编码 8 个模块名，而 src 实际有 84 个模块——其余 76 个连
「能不能被 import」都没被测过（循环 import、模块级副作用炸掉都不会在这里露头）。
`pkgutil.walk_packages` 沿包路径枚举子模块，新增模块自动进清单，不需要有人记得来改这张表。

口径与例外：只导入 `dataset_factory` 树下的模块；导入即执行模块级代码，所以这一步同时是
「包级 import 图可用」的最小证明（分层规则由 import-linter 契约另卡）。
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import dataset_factory


def _module_names() -> list[str]:
    """列出包内全部子模块的完整点分名。

    Returns:
        升序排列的模块名清单，例如 ``["dataset_factory._fs", "dataset_factory.cli"]``。
    """
    return sorted(
        info.name
        for info in pkgutil.walk_packages(
            dataset_factory.__path__, prefix=f"{dataset_factory.__name__}."
        )
    )


MODULES = _module_names()


def test_module_count_is_discovered() -> None:
    """枚举要真的走到子包——拿到 0 或只剩顶层说明 walk 的口径坏了。"""
    assert len(MODULES) > 50, f"只枚举到 {len(MODULES)} 个模块，walk_packages 口径可疑"


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    """每个模块都应能被成功 import。"""
    importlib.import_module(name)
