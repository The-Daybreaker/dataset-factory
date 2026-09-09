"""Smoke 测试：只验证「地基通不通」——包与七个模块都能被 import，不测具体功能。

这是测试地基的验收：pytest 能跑、包与七个模块能 import，说明 uv 环境 + src 布局 + 可安装配置都对。
"""

import importlib

import pytest

MODULES = [
    "dataset_factory",
    "dataset_factory.llm",
    "dataset_factory.prompts",
    "dataset_factory.skills",
    "dataset_factory.sessions",
    "dataset_factory.labeling",
    "dataset_factory.cli",
    "dataset_factory.api",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    """每个模块都应能被成功 import。"""
    importlib.import_module(name)
