"""打标编排引擎——全项目唯一知道「一轮打标怎么拼」的地方。

一轮打标 = 基础提示词（system 消息）+ 启用的 skill 全文（<skill> 标记包裹进当轮 user 消息）
+ 图片与指令（当轮 user 消息）+ 历史回放（按原角色重建、历史图片降为占位文本）。每轮经
sessions 落盘（用户消息 → 设置变更 → 请求信封 → 模型回复，信封先落盘再调模型，失败也有
「当时喂了什么」可查）；llm 消息模型与会话事件 JSON 的来回转换收敛在本模块（sessions 只把
设置与信封当任意 JSON 忠实存取，守分层）。历史不缓存、每轮回放 events.jsonl 重建——新进程
（如 CLI 续接）与崩溃重启后天然续上同一会话。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..llm import Completer, ImagePart, Message, Role, TextPart
from ..prompts import read_prompt
from ..sessions import (
    JsonValue,
    MessageEvent,
    SessionEvent,
    SettingsEvent,
    append_envelope,
    append_message,
    append_settings,
    attachment_path,
    create_session,
    read_events,
    save_attachment,
    save_attachment_bytes,
)
from ..skills import list_skills, read_skill
from .errors import (
    AttachmentReadError,
    EmptyTurnError,
    PromptNotSelectedError,
    SettingsFormatError,
)

# 会话设置在 settings 事件里的 JSON 键（该结构由本模块定义，sessions 不解析）。
_KEY_PROMPT = "prompt"  # pragma: allowlist secret
_KEY_SKILLS = "skills"  # pragma: allowlist secret

# skill 全文的边界标记：让模型认出这是注入的 skill 说明，也让记录能认出来源。
_SKILL_OPEN = "<skill>"
_SKILL_CLOSE = "</skill>"


@dataclass(frozen=True)
class SessionSettings:
    """一个会话的当前设置（从最后一条 settings 事件折叠而来）。

    Attributes:
        prompt_name: 当前基础提示词名称；会话从未设置过时为 None（首轮打标必须指定）。
        skill_names: 当前勾选启用的 skill 名称序列（保持勾选顺序注入）。
    """

    prompt_name: str | None
    skill_names: tuple[str, ...]


@dataclass(frozen=True)
class HistoryMessage:
    """回放出的历史消息（供入口层展示对话历史）。

    Attributes:
        role: 消息角色（``user`` / ``assistant``）。
        text: 消息文本。
        attachment: 附件在会话 attachments/ 下的文件名；无图为 None。
    """

    role: str
    text: str
    attachment: str | None


@dataclass(frozen=True)
class SessionSnapshot:
    """恢复一个会话所需的全部状态（重启后界面/续接的起点）。

    Attributes:
        session_id: 会话 id。
        settings: 会话当前设置。
        messages: 对话历史（user / assistant 消息，按时间序）。
    """

    session_id: str
    settings: SessionSettings
    messages: tuple[HistoryMessage, ...]


@dataclass(frozen=True)
class LabelResult:
    """一轮打标的结果。

    Attributes:
        session_id: 本轮所属会话的 id（首次调用新建的会话也在这里返回，供续接）。
        caption: 模型产出的打标文本。
    """

    session_id: str
    caption: str


class LabelingEngine:
    """打标编排引擎：组装一轮打标、经 sessions 落盘、调用模型拿回 caption。

    构造时注入 Completer（测试注入假实现即可离线跑）与模型名（模型名进请求信封，
    供复盘「当时用的是哪个模型」）。
    """

    def __init__(self, completer: Completer, model: str) -> None:
        """注入补全客户端与模型名。

        Args:
            completer: 实现 llm.Completer 协议的客户端（唯一与模型通信的通道）。
            model: 模型名（写入请求信封）。
        """
        self._completer = completer
        self._model = model

    def label(
        self,
        session_id: str | None = None,
        *,
        prompt_name: str | None = None,
        skill_names: Sequence[str] | None = None,
        instruction: str = "",
        image: Path | None = None,
        image_bytes: bytes | None = None,
        image_name: str = "image.png",
    ) -> LabelResult:
        """跑一轮打标：组装请求 → 先落信封 → 调模型 → 落回复，返回 caption 与会话 id。

        session_id 为 None 时新建会话（新 id 随结果返回，外部调用方保存后即可续接）。
        迭代改写 = 带同一 session_id 再调：历史自动回放携带。设置沿用与切换：prompt_name /
        skill_names 不传（None）沿用会话当前设置，传入新值（含空清单）即切换——设置真正
        变化时才追加 settings 事件。库级停用的 skill 注入时跳过（T4 语义：停用 = 不供打标
        注入），会话设置里保留原勾选记录。

        图片两种给法（二选一，同时给是编程错、直接 ValueError）：``image`` 传本地文件路径
        （CLI 用，存副本再读回）；``image_bytes`` 直接传字节（HTTP 入口用，网络来的字节没有
        源文件），``image_name`` 是它的原始文件名（保留进 attachments/，冲突加序号）。

        Args:
            session_id: 续接的会话 id；None 新建会话。
            prompt_name: 本轮使用的基础提示词名称；None 沿用当前设置。
            skill_names: 本轮启用的 skill 名称序列；None 沿用当前设置，空序列表示清空。
            instruction: 用户本轮的打标指令（可为空——纯图打标时任务说明在基础提示词里）。
            image: 本轮图片文件路径；None 表示不以此方式附图。
            image_bytes: 本轮图片字节；None 表示不以此方式附图。
            image_name: image_bytes 方式的原始文件名（仅名字用途，不参与内容判定）。

        Returns:
            LabelResult：会话 id + 模型产出的 caption。

        Raises:
            EmptyTurnError: 指令为空且未附图片（没有任何可打标的内容）。
            PromptNotSelectedError: 会话没有基础提示词且本轮未传入。
            AttachmentReadError: 附件副本读取失败。
            SettingsFormatError: 会话的设置事件结构非法。
            SessionNotFoundError: session_id 指向不存在的会话。
            PromptNotFoundError: 基础提示词在提示词库中不存在。
            LLMError: 模型调用失败（此时信封已落盘，「当时喂了什么」有据可查）。
            ValueError: image 与 image_bytes 同时提供。
        """
        if image is not None and image_bytes is not None:
            raise ValueError("image 与 image_bytes 只能二选一。")
        if not instruction.strip() and image is None and image_bytes is None:
            raise EmptyTurnError("本轮没有任何可打标的内容：请输入指令或附一张图片。")
        if session_id is None and prompt_name is None:
            # 新会话必然没有已存设置，此时连 prompt_name 都不传一定无底座；在建会话前拦下，
            # 不留只有空事件流的半成品会话。
            raise PromptNotSelectedError(
                "尚未选定基础提示词（一轮打标必须有一个作 system 底座）；请传入 prompt_name。"
            )
        if session_id is None:
            session_id = create_session()
        events = read_events(session_id)
        settings = _fold_settings(events)
        history = _replay_history(events)

        wanted_prompt = prompt_name if prompt_name is not None else settings.prompt_name
        wanted_skills = (
            tuple(skill_names) if skill_names is not None else settings.skill_names
        )
        if wanted_prompt is None:
            raise PromptNotSelectedError(
                "尚未选定基础提示词（一轮打标必须有一个作 system 底座）；请传入 prompt_name。"
            )
        if (wanted_prompt, wanted_skills) != (
            settings.prompt_name,
            settings.skill_names,
        ):
            append_settings(
                session_id,
                {_KEY_PROMPT: wanted_prompt, _KEY_SKILLS: list(wanted_skills)},
            )

        attachment: str | None = None
        sent_image_bytes: bytes | None = None
        if image is not None:
            attachment = save_attachment(session_id, image)
            sent_image_bytes = _read_attachment(session_id, attachment)
        elif image_bytes is not None:
            attachment = save_attachment_bytes(session_id, image_name, image_bytes)
            sent_image_bytes = image_bytes
        append_message(session_id, "user", instruction, attachment)

        prompt = read_prompt(wanted_prompt)
        skill_texts = _load_enabled_skill_texts(wanted_skills)
        messages, envelope_messages = _assemble(
            prompt_body=prompt.body,
            skill_texts=skill_texts,
            history=history,
            instruction=instruction,
            image_bytes=sent_image_bytes,
            attachment=attachment,
        )
        append_envelope(
            session_id, {"model": self._model, "messages": envelope_messages}
        )

        caption = self._completer.complete(messages)
        append_message(session_id, "assistant", caption)
        return LabelResult(session_id=session_id, caption=caption)

    def restore(self, session_id: str) -> SessionSnapshot:
        """恢复一个会话：当前设置 + 对话历史（入口层重启 / CLI 续接的起点）。

        Args:
            session_id: 会话 id。

        Returns:
            SessionSnapshot：设置 + 按时间序的 user / assistant 历史消息。

        Raises:
            SessionNotFoundError: 没有这个会话。
            SessionEventError: 事件流损坏。
            SettingsFormatError: 设置事件结构非法。
        """
        events = read_events(session_id)
        messages = tuple(
            HistoryMessage(
                role=event.role, text=event.text, attachment=event.attachment
            )
            for event in events
            if isinstance(event, MessageEvent) and event.role in ("user", "assistant")
        )
        return SessionSnapshot(
            session_id=session_id,
            settings=_fold_settings(events),
            messages=messages,
        )


def _read_attachment(session_id: str, name: str) -> bytes:
    """读会话附件副本的字节（发送的就是副本内容，会话自包含优先）。

    Raises:
        AttachmentReadError: 副本读取失败（底层 OSError）。
    """
    try:
        return attachment_path(session_id, name).read_bytes()
    except OSError as exc:
        raise AttachmentReadError(
            f"无法读取会话 {session_id!r} 的附件 {name!r}：{exc.strerror or exc}"
        ) from exc


def _fold_settings(events: Sequence[SessionEvent]) -> SessionSettings:
    """从事件流折叠出当前设置：取最后一条 settings 事件的值（无则全空）。"""
    prompt_name: str | None = None
    skill_names: tuple[str, ...] = ()
    for event in events:
        if isinstance(event, SettingsEvent):
            prompt_name, skill_names = _parse_settings_value(event.settings)
    return SessionSettings(prompt_name=prompt_name, skill_names=skill_names)


def _parse_settings_value(
    settings: Mapping[str, JsonValue],
) -> tuple[str | None, tuple[str, ...]]:
    """解析一条设置事件的 settings 体（结构由本模块定义，sessions 只忠实存取）。

    Raises:
        SettingsFormatError: prompt / skills 字段缺失或类型不对（正常写入不会产生）。
    """
    raw_prompt = settings.get(_KEY_PROMPT)
    raw_skills = settings.get(_KEY_SKILLS, [])
    if raw_prompt is not None and not isinstance(raw_prompt, str):
        raise SettingsFormatError(
            f"会话设置的 {_KEY_PROMPT!r} 字段应是字符串；请检查会话文件是否被改动。"
        )
    if not isinstance(raw_skills, list) or not all(
        isinstance(item, str) for item in raw_skills
    ):
        raise SettingsFormatError(
            f"会话设置的 {_KEY_SKILLS!r} 字段应是字符串数组；请检查会话文件是否被改动。"
        )
    return raw_prompt, tuple(cast(list[str], raw_skills))


def _replay_history(events: Sequence[SessionEvent]) -> tuple[Message, ...]:
    """把事件流里的历史消息重建为 llm 消息（本轮 user 消息落盘前调用，天然不含本轮）。

    历史 user 消息的图片降为占位文本（多轮重发图片字节会随轮数线性吃 token）；skill 全文
    只在当轮注入、不进历史（当轮注入的内容已体现在当时的回复里，且每轮都会重新注入当前
    启用的 skill）。system 角色的消息事件不参与历史（system 每轮从当前基础提示词重新渲染），
    其余意外角色跳过（历史 = 对话，不是任意事件回声）。
    """
    history: list[Message] = []
    for event in events:
        if not isinstance(event, MessageEvent) or event.role not in (
            "user",
            "assistant",
        ):
            continue
        parts: list[TextPart] = []
        if event.text:
            parts.append(TextPart(event.text))
        if event.attachment is not None:
            parts.append(TextPart(f"[图片: {event.attachment}]"))
        history.append(Message(role=cast(Role, event.role), parts=tuple(parts)))
    return tuple(history)


def _load_enabled_skill_texts(names: Sequence[str]) -> list[str]:
    """读出应注入的 skill 全文：会话勾选 ∩ 库级启用（停用的跳过），保持勾选顺序。"""
    if not names:
        return []
    enabled = {skill.name for skill in list_skills() if skill.enabled}
    return [read_skill(name) for name in names if name in enabled]


def _assemble(
    *,
    prompt_body: str,
    skill_texts: Sequence[str],
    history: Sequence[Message],
    instruction: str,
    image_bytes: bytes | None,
    attachment: str | None,
) -> tuple[list[Message], list[JsonValue]]:
    """组装一轮打标，同时产出两个视图。

    同一处逻辑生成、两个视图不会漂移：llm 消息（真实请求，图片是真字节）与信封消息
    （人类复盘快照，图片渲染为占位文本——base64 无人能读且撑爆事件流）。

    Args:
        prompt_body: 基础提示词正文（进 system 消息）。
        skill_texts: 要注入的 skill 全文列表（逐个 <skill> 包裹进当轮 user 消息）。
        history: 回放出的历史消息。
        instruction: 本轮用户指令。
        image_bytes: 本轮图片字节；None 表示无图。
        attachment: 本轮附件名（进信封占位文本）；None 表示无图。

    Returns:
        (llm 消息列表, 信封消息视图列表)。
    """
    current_parts: list[TextPart | ImagePart] = []
    current_text_parts: list[str] = []
    for text in skill_texts:
        wrapped = f"{_SKILL_OPEN}\n{text}\n{_SKILL_CLOSE}"
        current_parts.append(TextPart(wrapped))
        current_text_parts.append(wrapped)
    if instruction:
        current_parts.append(TextPart(instruction))
        current_text_parts.append(instruction)
    if image_bytes is not None:
        placeholder = f"[图片: {attachment}]"
        current_parts.append(ImagePart(image_bytes))
        current_text_parts.append(placeholder)

    messages: list[Message] = [
        Message(role="system", parts=(TextPart(prompt_body),)),
        *history,
        Message(role="user", parts=tuple(current_parts)),
    ]
    envelope_messages: list[JsonValue] = [
        {"role": "system", "content": prompt_body},
        *(_envelope_view(message) for message in history),
        {"role": "user", "content": "\n".join(current_text_parts)},
    ]
    return messages, envelope_messages


def _envelope_view(message: Message) -> dict[str, JsonValue]:
    """把一条历史消息渲染成信封视图（文本块拼接；历史图片已是占位文本块）。"""
    return {
        "role": message.role,
        "content": "\n".join(
            part.text for part in message.parts if isinstance(part, TextPart)
        ),
    }
