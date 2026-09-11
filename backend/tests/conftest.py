"""pytest 共享夹具（fixture）放这里，随开发逐步补充。

fixture = 给测试预置好可复用环境的零件，pytest 会自动加载本文件。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from dataset_factory.llm import Message


class FakeCompleter:
    """离线假 Completer：记录每轮收到的消息、按脚本返回文本（不真调 API）。

    replies 逐轮弹出（第一轮 calls 拿 replies[0]）；耗尽后返回兜底文本，避免多轮测试
    还要数清调用次数。calls 收下每轮的完整消息列表，供测试断言「引擎拼了什么」。
    """

    def __init__(self, replies: Sequence[str] = ("打标结果",)) -> None:
        """存下逐轮回复脚本，初始化调用记录。"""
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def complete(self, messages: Sequence[Message]) -> str:
        """记录本轮消息并返回脚本回复。"""
        self.calls.append(list(messages))
        if self._replies:
            return self._replies.pop(0)
        return "打标结果"


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


@pytest.fixture
def fake_completer() -> FakeCompleter:
    """离线假模型客户端：默认回「打标结果」，需要多轮不同回复时测试里自建 FakeCompleter。"""
    return FakeCompleter()
