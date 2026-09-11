"""provider 中立的补全接口 + OpenAI 兼容实现（当前唯一 provider）。

- Completer：中立契约（一组消息进、模型产出文本出），加新 provider = 新增一个实现；
- OpenAIChatClient：走官方 openai SDK 的 /v1/chat/completions（非流式），改 base_url
  即接任意兼容端点；
- build_completer：从 EndpointConfig 装配客户端，显式设 timeout / max_retries。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import openai
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from .config import EndpointConfig
from .errors import LLMError
from .messages import Message

# 大图 / 慢模型把 timeout 放长；max_retries 复用 SDK 内建对超时 / 5xx / 429 的指数退避。
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_MAX_RETRIES = 2


class Completer(Protocol):
    """provider 中立补全接口：一组消息进、模型产出的文本出。"""

    def complete(self, messages: Sequence[Message]) -> str:
        """发送一轮消息、返回模型产出的文本。

        Args:
            messages: provider 中立消息序列（角色 + 有序内容块）。

        Returns:
            模型产出的文本（即 caption）。

        Raises:
            LLMError: 模型未返回可用文本。
        """
        ...


class OpenAIChatClient:
    """OpenAI 兼容 /v1/chat/completions 的 Completer 实现（非流式）。"""

    def __init__(self, client: openai.OpenAI, model: str) -> None:
        """注入已装配好的 openai 客户端与模型名（注入便于测试替换假客户端）。

        Args:
            client: 官方 openai SDK 客户端（base_url / api_key / timeout 已配好）。
            model: 模型名。
        """
        self._client = client
        self._model = model

    def complete(self, messages: Sequence[Message]) -> str:
        """把中立消息转成 OpenAI 消息、发非流式请求、取回文本。

        Args:
            messages: provider 中立消息序列。

        Returns:
            模型产出文本。

        Raises:
            LLMError: 响应无 choices 或首条 choice 无文本内容。
        """
        payload = [_to_openai_message(message) for message in messages]
        response = self._client.chat.completions.create(
            model=self._model, messages=payload
        )
        return _extract_text(response)


def build_completer(config: EndpointConfig) -> Completer:
    """从端点配置装配 OpenAI 兼容客户端（当前唯一 provider 实现）。

    Args:
        config: 端点三要素（base_url / model / api_key）。

    Returns:
        实现 Completer 的客户端。
    """
    client = openai.OpenAI(
        base_url=config.base_url,
        api_key=config.api_key.reveal(),
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        max_retries=_DEFAULT_MAX_RETRIES,
    )
    return OpenAIChatClient(client, config.model)


def _to_openai_message(message: Message) -> ChatCompletionMessageParam:
    """把中立 Message 转成 openai SDK 的消息参数：按角色分派、文本内容块拼成正文。"""
    text = "\n".join(part.text for part in message.parts)
    if message.role == "system":
        return ChatCompletionSystemMessageParam(role="system", content=text)
    if message.role == "assistant":
        return ChatCompletionAssistantMessageParam(role="assistant", content=text)
    return ChatCompletionUserMessageParam(role="user", content=text)


def _extract_text(response: ChatCompletion) -> str:
    """从 ChatCompletion 取首条 choice 的文本内容。

    Raises:
        LLMError: 响应不含 choices，或首条 choice 的 content 为 None。
    """
    if not response.choices:
        raise LLMError("模型响应不含任何 choices；请检查端点或模型是否可用。")
    content = response.choices[0].message.content
    if content is None:
        raise LLMError("模型未返回文本内容（content 为空）；请重试或检查模型。")
    return content
