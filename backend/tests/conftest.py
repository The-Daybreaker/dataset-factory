"""pytest 共享夹具（fixture）放这里，随开发逐步补充。

fixture = 给测试预置好可复用环境的零件，pytest 会自动加载本文件。
后续会加：mock 的 llm 客户端（不真调 API）等。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的临时数据根：把 DATASET_FACTORY_HOME 指向 tmp，测试绝不碰真实 ~/.dataset_factory。

    返回数据根路径；测试在其下放 config.json / credentials 再调 llm 读取接口。
    同时清空 DSF_API_KEY，避免外部环境变量干扰密钥双通道测试。
    """
    root = tmp_path / "dsf_home"
    root.mkdir()
    monkeypatch.setenv("DATASET_FACTORY_HOME", str(root))
    monkeypatch.delenv("DSF_API_KEY", raising=False)
    return root
