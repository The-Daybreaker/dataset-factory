"""provider 中立的补全接口 + OpenAI 兼容实现（当前唯一 provider）。

- Completer：中立契约（一组消息进、模型产出文本出），加新 provider = 新增一个实现；
- OpenAIChatClient：走官方 openai SDK 的 /v1/chat/completions（非流式 complete 与流式
  stream 两条路径），改 base_url 即接任意兼容端点；
- build_completer：从 EndpointConfig 装配客户端，显式设 timeout / max_retries。
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Protocol, cast

import openai
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from .._obs import ms_since
from .config import EndpointConfig, RequestConfig, SecretValue
from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConnectionError,
    LLMError,
    LLMNotFoundError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnexpectedError,
)
from .images import encode_image_data_url
from .messages import ImagePart, Message, StreamDelta, TextPart, VideoPart

logger = logging.getLogger(__name__)


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

    def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
        """流式发送一轮消息，逐段产出思考 / 正文增量。

        Args:
            messages: provider 中立消息序列（角色 + 有序内容块）。

        Yields:
            StreamDelta：思考或正文增量（顺序即模型产出顺序）。

        Raises:
            LLMError: 模型调用失败（含流中途失败）。
        """
        ...


class OpenAIChatClient:
    """OpenAI 兼容 /v1/chat/completions 的 Completer 实现（非流式 + 流式增量）。"""

    def __init__(
        self,
        client: openai.OpenAI,
        model: str,
        request: RequestConfig | None = None,
        api_key: SecretValue | None = None,
    ) -> None:
        """注入已装配好的 openai 客户端、模型名与请求参数（注入便于测试替换假客户端）。

        Args:
            client: 官方 openai SDK 客户端（base_url / api_key / timeout 已配好）。
            model: 模型名。
            request: 请求参数（生成参数 + 透传的端点专有参数）；缺省表示不额外传任何参数。
            api_key: 当前配置的密钥（供错误摘要脱敏；reveal 只在失败路径的掩码处发生）。
        """
        self._client = client
        self._model = model
        self._request = request if request is not None else RequestConfig()
        self._api_key = api_key

    def complete(self, messages: Sequence[Message]) -> str:
        """把中立消息转成 OpenAI 消息、发非流式请求、取回文本。

        Args:
            messages: provider 中立消息序列。

        Returns:
            模型产出文本。

        Raises:
            LLMError: SDK 调用失败（翻译成对应分类异常），或响应无 choices / 无文本内容。
        """
        payload = [_to_openai_message(message) for message in messages]
        start = perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                # 未配置的项传 SDK 的 NOT_GIVEN 哨兵（而非 None）：哨兵表示「这个参数别发」，
                # 传 None 会被序列化成 JSON null、部分端点会直接判为非法请求。
                temperature=_given_or_omit(self._request.temperature),
                top_p=_given_or_omit(self._request.top_p),
                max_tokens=_given_or_omit(self._request.max_tokens),
                extra_body=(
                    dict(self._request.extra_body)
                    if self._request.extra_body is not None
                    else None
                ),
            )
        except openai.APIError as exc:
            # 第三段边界：模型调用本身。失败也记耗时——「卡了多久才失败」是排查的关键信息；
            # 同时留下 SDK 异常类名（如 APITimeoutError），便于与用户可见消息对照。
            secret = self._api_key.reveal() if self._api_key is not None else None
            logger.warning(
                "模型调用失败（%.0fms，模型 %s，%s，端点响应：%s）",
                ms_since(start),
                self._model,
                type(exc).__name__,
                _endpoint_error_summary(exc, secret) or "（端点未返回细节）",
            )
            raise _translate_sdk_error(exc, secret=secret) from exc
        logger.info("模型调用完成（%.0fms，模型 %s）", ms_since(start), self._model)
        return _extract_text(response)

    def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
        """流式发送一轮消息：逐段产出思考增量（端点扩展 reasoning_content）与正文增量。

        增量之外的请求装配与非流式 complete 完全一致（生成参数 / 透传口同一通道）；
        流中途失败同样翻译成分类异常——调用方此时多半已把部分增量发给了界面，
        由调用方决定如何呈现「生成中断」。

        Args:
            messages: provider 中立消息序列。

        Yields:
            StreamDelta：按模型产出顺序的思考 / 正文增量。

        Raises:
            LLMError: SDK 调用失败（建流或流中途），翻译成对应分类异常。
        """
        payload = [_to_openai_message(message) for message in messages]
        start = perf_counter()
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                # 未配置的项传 SDK 的 omit 哨兵（语义同非流式）：「这个参数别发」。
                temperature=_given_or_omit(self._request.temperature),
                top_p=_given_or_omit(self._request.top_p),
                max_tokens=_given_or_omit(self._request.max_tokens),
                extra_body=(
                    dict(self._request.extra_body)
                    if self._request.extra_body is not None
                    else None
                ),
                stream=True,
            )
            for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                delta = choice.delta if choice is not None else None
                # reasoning_content 是思考型模型的端点扩展，SDK 类型未收录 → getattr 读取。
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield StreamDelta(kind="reasoning", text=str(reasoning))
                if delta is not None and delta.content:
                    yield StreamDelta(kind="content", text=delta.content)
        except openai.APIError as exc:
            secret = self._api_key.reveal() if self._api_key is not None else None
            logger.warning(
                "模型流式调用失败（%.0fms，模型 %s，%s，端点响应：%s）",
                ms_since(start),
                self._model,
                type(exc).__name__,
                _endpoint_error_summary(exc, secret) or "（端点未返回细节）",
            )
            raise _translate_sdk_error(exc, secret=secret) from exc
        logger.info("模型流式调用完成（%.0fms，模型 %s）", ms_since(start), self._model)


def build_completer(config: EndpointConfig) -> Completer:
    """从端点配置装配 OpenAI 兼容客户端（当前唯一 provider 实现）。

    超时与重试次数取自 config.request（可被 config.json 覆盖）——硬编码的 120 秒对默认
    带思考模式的推理型模型可能不够。

    Args:
        config: 端点配置（base_url / model / api_key / 请求参数）。

    Returns:
        实现 Completer 的客户端。
    """
    client = openai.OpenAI(
        base_url=config.base_url,
        api_key=config.api_key.reveal(),
        timeout=config.request.timeout_seconds,
        max_retries=config.request.max_retries,
    )
    return OpenAIChatClient(client, config.model, config.request, config.api_key)


# 连通性探测的传输参数：比正式打标更急——15 秒等不到就报超时、不重试（用户在等结果）。
_PROBE_TIMEOUT_SECONDS = 15.0
_PROBE_MAX_TOKENS = 1


@dataclass(frozen=True)
class ProbeResult:
    """端点连通性探测的结果（业务结果而非异常：失败也是正常返回值）。

    Attributes:
        ok: 探测是否成功（发出极小请求且模型应答）。
        message: 给人看的结果说明——成功固定文案；失败为 llm 分类错误消息（可操作）。
        latency_ms: 从发请求到得出结论的耗时（毫秒）。
    """

    ok: bool
    message: str
    latency_ms: float


def probe_endpoint(config: EndpointConfig) -> ProbeResult:
    """发一个极小的真实请求探测端点连通性（Web「测试连接」与 CLI ``dsf config test`` 共用）。

    传输参数固定为探测专用（15 秒超时、不重试、``max_tokens=1``），覆盖调用方传入的
    request——探测要的是「快进快出」，正式打标参数（长超时 / 重试）在这里只会拖慢反馈。
    失败不抛异常：连通性成败是业务结果，翻译成 ``ProbeResult`` 由入口层呈现。

    Args:
        config: 待探测的端点配置（base_url / model / api_key；request 被探测参数覆盖）。

    Returns:
        ProbeResult：成败 + 可操作消息 + 耗时。
    """
    test_config = replace(
        config,
        request=RequestConfig(
            timeout_seconds=_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
            max_tokens=_PROBE_MAX_TOKENS,
        ),
    )
    start = perf_counter()
    try:
        build_completer(test_config).complete(
            [Message(role="user", parts=(TextPart(text="ping"),))]
        )
    except LLMError as exc:
        return ProbeResult(ok=False, message=str(exc), latency_ms=ms_since(start))
    return ProbeResult(
        ok=True, message="连接成功，模型应答正常。", latency_ms=ms_since(start)
    )


def _endpoint_error_summary(exc: openai.APIError, secret: str | None = None) -> str:
    """提取端点错误响应体的单行摘要（进失败日志与用户可见错误消息）。

    「模型侧 400」排查需要端点的原话（如 SiliconFlow 的 ``code 20015``），只有异常类名
    等于让排查者盲猜。SDK 的 APIStatusError 带 ``body``（已解析的 JSON 或原始文本），
    拿不到时退回 ``str(exc)``（其中通常已含响应体）；压成单行并截断，防大响应冲爆日志。
    ``secret`` 是当前配置的 API 密钥明文：摘要来自**端点响应体**（端点可配任意 base_url），
    若端点在错误体里回显了请求内容（含密钥），此处定点掩码，防密钥经日志 / 界面二次泄漏。
    """
    raw: object
    if isinstance(exc, openai.APIStatusError):
        # getattr 兜底：测试里的未初始化异常实例没有 body / message 属性，运行时也不能赌。
        raw = getattr(exc, "body", None) or getattr(exc, "message", None) or str(exc)
    else:
        raw = str(exc)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = (
        raw
        if isinstance(raw, str)
        else json.dumps(raw, ensure_ascii=False, default=str)
    )
    text = " ".join(text.split())
    if secret and secret in text:
        text = text.replace(secret, "*****")
    return text[:300] + ("…" if len(text) > 300 else "")


def _given_or_omit[T](value: T | None) -> T | openai.Omit:
    """把 None 转成 SDK 的 omit 哨兵（区分「不传该参数」与「显式传 null」）。

    SDK 3.x 用 `omit` 作默认哨兵（旧版的 `NOT_GIVEN` 语义仍是「没传」）；传 None 会被
    序列化成 JSON null、部分端点直接判为非法请求，所以未配置的项一律走哨兵。

    Args:
        value: 参数值；None 表示不传。

    Returns:
        原值，或 SDK 的 omit 哨兵。
    """
    return openai.omit if value is None else value


def _to_openai_message(message: Message) -> ChatCompletionMessageParam:
    """把中立 Message 转成 openai SDK 的消息参数：按角色分派。

    system / assistant 只承载文本（拼成正文字符串）；user 可含图片与视频，逐块转成
    OpenAI 内容块列表（文本块 + image_url / video_url data URL）。
    """
    if message.role == "system":
        return ChatCompletionSystemMessageParam(
            role="system", content=_joined_text(message)
        )
    if message.role == "assistant":
        return ChatCompletionAssistantMessageParam(
            role="assistant", content=_joined_text(message)
        )
    return ChatCompletionUserMessageParam(role="user", content=_user_content(message))


def _joined_text(message: Message) -> str:
    """把消息里的文本块拼成正文（system / assistant 只用文本，图片块在此忽略）。"""
    return "\n".join(part.text for part in message.parts if isinstance(part, TextPart))


def _user_content(message: Message) -> list[ChatCompletionContentPartParam]:
    """把 user 消息的内容块逐个转成 OpenAI 内容块：文本 → text、图片 → image_url、视频 → video_url（端点扩展）。"""
    content: list[ChatCompletionContentPartParam] = []
    for part in message.parts:
        if isinstance(part, ImagePart):
            content.append(
                ChatCompletionContentPartImageParam(
                    type="image_url",
                    image_url={"url": encode_image_data_url(part.data)},
                )
            )
        elif isinstance(part, VideoPart):
            # video_url 是 OpenAI SDK 类型化参数之外的端点扩展（如 SiliconFlow）：
            # 按端点文档构造原始 dict，SDK 序列化时原样透传。
            content.append(
                cast(
                    "ChatCompletionContentPartParam",
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": f"data:{part.mime};base64,"
                            + base64.b64encode(part.data).decode("ascii"),
                            "fps": part.fps,
                            "max_frames": part.max_frames,
                        },
                    },
                )
            )
        else:
            content.append(
                ChatCompletionContentPartTextParam(type="text", text=part.text)
            )
    return content


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


def _extract_retry_after(exc: openai.APIError) -> float | None:
    """从限流响应头提取 Retry-After 的等待秒数（批量跑批退避优先遵循它）。

    Args:
        exc: SDK 的限流异常（HTTP 状态类异常带 response.headers）。

    Returns:
        Retry-After 秒数；端点没给、格式非数字（如 HTTP-date）或属性不可达时 None。
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = headers.get("retry-after") if headers is not None else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _translate_sdk_error(exc: openai.APIError, secret: str | None = None) -> LLMError:
    """把 openai SDK 的分类异常翻译成项目自己的类型化异常。

    按「先具体后一般」判类型：APITimeoutError 是 APIConnectionError 的子类、各 HTTP
    状态异常是 APIStatusError 的子类，故先判子类再判父类。用户可见消息只说「哪里错、
    怎么修」，不回显 SDK 原始消息（避免泄密钥 / 甩栈）；HTTP 状态类错误在其后附上
    **端点响应体摘要**（2026-09-14 用户定夺）——端点的一面之词交给用户自己判断
    （如「模型不存在」在部分端点是 400 而非 404，摘要能直接看到端点怎么说）。
    ``secret`` 是当前配置的密钥明文：摘要生成后对它定点掩码——端点可配任意 base_url，
    错误体若回显了请求内容（含密钥），不能让它经界面 / 日志二次泄漏（audit 2026-09-14）。
    """
    secret_summary = (
        _endpoint_error_summary(exc, secret)
        if isinstance(exc, openai.APIStatusError)
        else ""
    )
    suffix = f"（端点返回：{secret_summary}）" if secret_summary else ""
    if isinstance(exc, openai.APITimeoutError):
        return LLMTimeoutError(
            "调用模型超时；网络较慢或模型响应久，可稍后重试（大图 / 慢模型可调大 timeout）。"
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMConnectionError(
            "无法连接到模型端点；请检查网络与 base_url 是否可达。"
        )
    if isinstance(exc, openai.AuthenticationError):
        return LLMAuthError(
            "鉴权失败：API 密钥无效或过期；请用 `dsf config set` 重新设置密钥。"
            + suffix
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMAuthError("无权访问该端点或模型；请检查密钥权限。" + suffix)
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(
            "触发限流（请求过多或额度用尽）；请稍后重试或检查配额。" + suffix,
            retry_after=_extract_retry_after(exc),
        )
    if isinstance(exc, openai.NotFoundError):
        return LLMNotFoundError(
            "端点或模型不存在；请检查 base_url 与模型名是否正确。" + suffix
        )
    if isinstance(exc, openai.BadRequestError):
        return LLMBadRequestError(
            "请求被端点判为非法（消息 / 图片 / 参数不合法）；请检查输入。" + suffix
        )
    if isinstance(exc, openai.UnprocessableEntityError):
        return LLMBadRequestError("请求格式端点无法处理；请检查输入。" + suffix)
    if isinstance(exc, openai.InternalServerError):
        return LLMServerError("模型服务端错误（5xx）；请稍后重试。" + suffix)
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return LLMServerError(
                f"模型服务端错误（HTTP {exc.status_code}）；请稍后重试。{suffix}"
            )
        return LLMUnexpectedError(
            f"模型端点返回意外错误（HTTP {exc.status_code}）。{suffix}"
        )
    return LLMUnexpectedError("调用模型时发生意外错误；请重试或检查端点配置。")
