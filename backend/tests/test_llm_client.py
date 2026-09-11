"""单元测试：llm 的 provider 中立接口与 OpenAI 兼容客户端（消息转换 / 响应提取 / 装配）。

全部离线、不真调 API：用假 openai 客户端（MagicMock）替换真实 SDK 客户端，验证转换与提取逻辑。
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import openai
import pytest

from dataset_factory.llm import (
    EndpointConfig,
    ImagePart,
    LLMError,
    Message,
    OpenAIChatClient,
    SecretValue,
    TextPart,
    build_completer,
)


def _response_with_content(content: object) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _client_returning(response: object, model: str = "test-model") -> OpenAIChatClient:
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = response
    return OpenAIChatClient(cast(openai.OpenAI, sdk), model)


def test_complete_returns_model_text() -> None:
    """正常路径：取出模型返回的首条 choice 文本。"""
    client = _client_returning(_response_with_content("a caption"))

    result = client.complete([Message(role="user", parts=(TextPart("hi"),))])

    assert result == "a caption"


def test_complete_converts_messages_by_role() -> None:
    """请求装配：model 与按角色转换后的 OpenAI 消息正确传给 SDK。"""
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _response_with_content("ok")
    client = OpenAIChatClient(cast(openai.OpenAI, sdk), "gpt-test")
    messages = [
        Message(role="system", parts=(TextPart("base prompt"),)),
        Message(role="user", parts=(TextPart("do this"),)),
        Message(role="assistant", parts=(TextPart("prior reply"),)),
    ]

    client.complete(messages)

    kwargs = sdk.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-test"
    assert kwargs["messages"] == [
        {"role": "system", "content": "base prompt"},
        {"role": "user", "content": [{"type": "text", "text": "do this"}]},
        {"role": "assistant", "content": "prior reply"},
    ]


def test_complete_converts_user_message_with_image() -> None:
    """user 消息含图片块：转成 OpenAI 内容块列表（text + image_url data URL）。"""
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _response_with_content("caption")
    client = OpenAIChatClient(cast(openai.OpenAI, sdk), "gpt-test")
    png = b"\x89PNG\r\n\x1a\nfake"
    messages = [Message(role="user", parts=(TextPart("describe"), ImagePart(png)))]

    client.complete(messages)

    sent = sdk.chat.completions.create.call_args.kwargs["messages"]
    user_content = sent[0]["content"]
    assert user_content[0] == {"type": "text", "text": "describe"}
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_complete_raises_on_empty_choices() -> None:
    """响应无 choices → LLMError（fail loud，不静默返回空）。"""
    empty = MagicMock()
    empty.choices = []
    client = _client_returning(empty)

    with pytest.raises(LLMError, match="choices"):
        client.complete([Message(role="user", parts=(TextPart("hi"),))])


def test_complete_raises_on_none_content() -> None:
    """首条 choice 无文本（content 为 None）→ LLMError。"""
    client = _client_returning(_response_with_content(None))

    with pytest.raises(LLMError, match="文本"):
        client.complete([Message(role="user", parts=(TextPart("hi"),))])


def test_build_completer_wires_endpoint_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_completer 用 config 的端点/密钥装配 SDK，并显式设 timeout / max_retries。"""
    captured: dict[str, object] = {}

    def _fake_openai(**kwargs: object) -> object:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(openai, "OpenAI", _fake_openai)
    config = EndpointConfig(
        base_url="https://api.example.com/v1",
        model="test-model",
        api_key=SecretValue("sk-build"),
    )

    build_completer(config)

    assert captured["base_url"] == "https://api.example.com/v1"
    assert captured["api_key"] == config.api_key.reveal()
    assert captured["timeout"] == 120.0
    assert captured["max_retries"] == 2
