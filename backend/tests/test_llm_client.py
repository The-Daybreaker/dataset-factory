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
    LLMAuthError,
    LLMBadRequestError,
    LLMConnectionError,
    LLMError,
    LLMNotFoundError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnexpectedError,
    Message,
    OpenAIChatClient,
    RequestConfig,
    SecretValue,
    TextPart,
    VideoPart,
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


def _client_raising(exc: BaseException, model: str = "test-model") -> OpenAIChatClient:
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = exc
    return OpenAIChatClient(cast(openai.OpenAI, sdk), model)


def _bare_sdk_error[T: BaseException](cls: type[T]) -> T:
    """造未初始化的 SDK 异常实例：只测 isinstance 分派，绕开 __init__ 对 httpx 参数的依赖。"""
    return cls.__new__(cls)


def test_complete_passes_configured_request_params() -> None:
    """配好的生成参数与透传参数真的进到 SDK 调用里（否则「能配」只是摆设）。"""
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _response_with_content("ok")
    client = OpenAIChatClient(
        cast(openai.OpenAI, sdk),
        "gpt-test",
        RequestConfig(
            temperature=0.7,
            top_p=0.8,
            max_tokens=128,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        ),
    )

    client.complete((Message(role="user", parts=(TextPart("hi"),)),))

    kwargs = sdk.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.7
    assert kwargs["top_p"] == 0.8
    assert kwargs["max_tokens"] == 128
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_complete_omits_unconfigured_params() -> None:
    """未配置的生成参数走 SDK 的 omit 哨兵（而非 None——传 null 会让部分端点判为非法请求）。"""
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _response_with_content("ok")
    client = OpenAIChatClient(cast(openai.OpenAI, sdk), "gpt-test")

    client.complete((Message(role="user", parts=(TextPart("hi"),)),))

    kwargs = sdk.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] is openai.omit
    assert kwargs["top_p"] is openai.omit
    assert kwargs["max_tokens"] is openai.omit
    assert kwargs["extra_body"] is None


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


def test_complete_converts_user_message_with_video() -> None:
    """user 消息含视频块：转成端点扩展 video_url 内容块（data URL + fps / max_frames）。"""
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _response_with_content("caption")
    client = OpenAIChatClient(cast(openai.OpenAI, sdk), "gpt-test")
    mp4 = b"\x00\x00\x00 ftypisomfake"
    messages = [
        Message(
            role="user",
            parts=(TextPart("describe"), VideoPart(mp4, fps=3.0, max_frames=24)),
        )
    ]

    client.complete(messages)

    sent = sdk.chat.completions.create.call_args.kwargs["messages"]
    user_content = sent[0]["content"]
    assert user_content[0] == {"type": "text", "text": "describe"}
    assert user_content[1]["type"] == "video_url"
    video_url = user_content[1]["video_url"]
    assert video_url["url"].startswith("data:video/mp4;base64,")
    assert video_url["fps"] == 3.0
    assert video_url["max_frames"] == 24


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


@pytest.mark.parametrize(
    ("sdk_exc", "expected", "retryable"),
    [
        (openai.APITimeoutError, LLMTimeoutError, True),
        (openai.APIConnectionError, LLMConnectionError, True),
        (openai.APIError, LLMUnexpectedError, False),
        (openai.AuthenticationError, LLMAuthError, False),
        (openai.PermissionDeniedError, LLMAuthError, False),
        (openai.RateLimitError, LLMRateLimitError, True),
        (openai.NotFoundError, LLMNotFoundError, False),
        (openai.BadRequestError, LLMBadRequestError, False),
        (openai.UnprocessableEntityError, LLMBadRequestError, False),
        (openai.InternalServerError, LLMServerError, True),
    ],
)
def test_complete_maps_sdk_error_to_typed(
    sdk_exc: type[BaseException], expected: type[LLMError], retryable: bool
) -> None:
    """SDK 分类异常翻译成对应的项目类型化异常，retryable 标记正确。"""
    client = _client_raising(_bare_sdk_error(sdk_exc))

    with pytest.raises(expected) as excinfo:
        client.complete([Message(role="user", parts=(TextPart("hi"),))])

    assert excinfo.value.retryable is retryable


def test_complete_maps_unhandled_4xx_to_unexpected() -> None:
    """未单独归类的 4xx（如 409）→ LLMUnexpectedError、不可重试。"""
    exc = _bare_sdk_error(openai.ConflictError)
    exc.status_code = 409
    client = _client_raising(exc)

    with pytest.raises(LLMUnexpectedError) as excinfo:
        client.complete([Message(role="user", parts=(TextPart("hi"),))])

    assert excinfo.value.retryable is False


def test_complete_maps_generic_5xx_to_server_error() -> None:
    """未单独归类的 5xx → LLMServerError、可重试。"""
    exc = _bare_sdk_error(openai.APIStatusError)
    exc.status_code = 503
    client = _client_raising(exc)

    with pytest.raises(LLMServerError) as excinfo:
        client.complete([Message(role="user", parts=(TextPart("hi"),))])

    assert excinfo.value.retryable is True


def test_auth_error_message_is_clean_and_actionable() -> None:
    """鉴权错误消息是干净可操作人话（钉死内容，防将来回显 SDK 原文而泄密）。"""
    client = _client_raising(_bare_sdk_error(openai.AuthenticationError))

    with pytest.raises(LLMAuthError) as excinfo:
        client.complete([Message(role="user", parts=(TextPart("hi"),))])

    assert str(excinfo.value) == (
        "鉴权失败：API 密钥无效或过期；请用 `dsf config set` 重新设置密钥。"
    )
