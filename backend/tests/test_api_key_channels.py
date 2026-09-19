"""密钥通道的两条规则：环境通道只认非空白、按给定顺序取第一个可用密钥。

`resolve_api_key`（跑批与引擎）与 CLI「测端点」、Web「测连接」现在共用这两个入口表达
「哪条通道优先」——顺序与空白判定各写一遍就曾分叉过（空环境变量被当成有密钥），
所以钉在这里。双通道全空的报错路径由既有端到端用例覆盖，这里不重复断言文案。
"""

from __future__ import annotations

import pytest

from dataset_factory.llm import (
    ENV_API_KEY,
    SecretValue,
    env_api_key,
    first_api_key,
)


def test_blank_env_var_is_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量存在但全空白＝没设：带着空密钥去请求端点只会换来一个难查的 401。"""
    monkeypatch.setenv(ENV_API_KEY, "   ")

    assert env_api_key() is None


def test_env_var_is_trimmed_when_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量两侧空白不参与密钥内容（粘贴常带的空格不该把请求弄坏）。"""
    monkeypatch.setenv(ENV_API_KEY, "  k-with-space  ")

    key = env_api_key()

    assert key is not None
    assert key.reveal() == "k-with-space"


def test_first_api_key_follows_the_given_order() -> None:
    """按参数顺序取第一个可用密钥；全不可用返回 None，报错方式留给调用点。"""
    first = SecretValue("first")
    second = SecretValue("second")

    assert first_api_key(None, first, second) is first
    assert first_api_key(second, first) is second
    assert first_api_key(None, None) is None
