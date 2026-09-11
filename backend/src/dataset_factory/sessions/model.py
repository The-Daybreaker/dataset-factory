"""会话事件的数据模型 + JSONL 序列化 / 解析——本模块是事件磁盘格式的唯一事实来源。

events.jsonl 每行一个 JSON 事件对象，一期三类事件：

- ``message``：对话消息（role + text + 可选附件名），恢复会话时回放它重建对话历史；
- ``envelope``：请求信封（一轮实际发出的完整请求的 JSON 快照），供复盘「当时喂了什么」；
- ``settings``：会话级设置（当前基础提示词与启用 skill 清单等），设置变更时追加，
  回放取最后一条即当前值。

信封的 request 体与设置的 settings 体都是编排层（labeling）渲染好的 JSON 结构；sessions
是数据域、禁 import 能力层 llm，故只把它当作「任意 JSON 值」忠实存取，绝不解析其内部——
llm 消息模型与 JSON 之间的来回转换是编排层的职责。序列化（dump_event）与解析（parse_event）
成对，golden 契约测试用一份手写 events.jsonl 把磁盘 schema 钉死，防两侧一起漂移。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from .errors import SessionEventError

# 任意 JSON 值（递归定义）：请求信封的 request 体与设置的 settings 体是已渲染好的 JSON
# 结构，sessions 不解析其内部。
type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)

_TYPE_MESSAGE = "message"
_TYPE_ENVELOPE = "envelope"
_TYPE_SETTINGS = "settings"


@dataclass(frozen=True)
class MessageEvent:
    """一条对话消息事件（恢复回放时重建对话历史的最小单位）。

    Attributes:
        ts: 事件时间戳（ISO 8601 字符串）。
        role: 消息角色（``system`` / ``user`` / ``assistant``）；sessions 只当字符串忠实存，
            不校验语义（角色含义是 llm / 编排层的领域知识）。
        text: 消息文本内容。
        attachment: 可选，会话 attachments/ 下的图片文件名；无图时为 None。
    """

    ts: str
    role: str
    text: str
    attachment: str | None


@dataclass(frozen=True)
class EnvelopeEvent:
    """一次请求信封事件：一轮实际发出的完整请求的 JSON 快照（复盘「当时喂了什么」）。

    Attributes:
        ts: 事件时间戳（ISO 8601 字符串）。
        request: 渲染后的完整请求（messages + 模型参数）；sessions 原样存取、不解析其内部。
    """

    ts: str
    request: dict[str, JsonValue]


@dataclass(frozen=True)
class SettingsEvent:
    """一次会话级设置变更事件（当前值 = 回放取最后一条 settings）。

    Attributes:
        ts: 事件时间戳（ISO 8601 字符串）。
        settings: 会话当前设置（如基础提示词名与启用 skill 清单）；结构由编排层定义，
            sessions 原样存取、不解析其内部。
    """

    ts: str
    settings: dict[str, JsonValue]


# 事件流里一行解析后的结果：消息事件、请求信封事件或设置事件。
SessionEvent = MessageEvent | EnvelopeEvent | SettingsEvent


def dump_event(event: SessionEvent) -> str:
    """把一个事件序列化成一整行 JSON 文本（不含结尾换行，由 append 调用方补上）。

    Args:
        event: 要序列化的事件（MessageEvent / EnvelopeEvent / SettingsEvent）。

    Returns:
        单行 JSON 字符串（ensure_ascii=False，中文原样可读）。
    """
    obj: dict[str, JsonValue]
    if isinstance(event, MessageEvent):
        obj = {
            "type": _TYPE_MESSAGE,
            "ts": event.ts,
            "role": event.role,
            "text": event.text,
        }
        if event.attachment is not None:
            obj["attachment"] = event.attachment
    elif isinstance(event, EnvelopeEvent):
        obj = {
            "type": _TYPE_ENVELOPE,
            "ts": event.ts,
            "request": event.request,
        }
    else:
        obj = {
            "type": _TYPE_SETTINGS,
            "ts": event.ts,
            "settings": event.settings,
        }
    return json.dumps(obj, ensure_ascii=False)


def parse_event(obj: object) -> SessionEvent:
    """把一行 JSON（已 loads 成对象）解析回事件；结构非法即 fail loud。

    Args:
        obj: json.loads 出来的对象，应是一个映射（JSON 对象）。

    Returns:
        解析出的 MessageEvent / EnvelopeEvent / SettingsEvent。

    Raises:
        SessionEventError: 顶层非映射、缺 ts、未知 type，或该类型的必需字段缺失 / 类型不对。
    """
    if not isinstance(obj, dict):
        raise SessionEventError("事件行顶层应是 JSON 对象（键值映射）。")
    data = cast(dict[str, object], obj)
    ts = data.get("ts")
    if not isinstance(ts, str):
        raise SessionEventError("事件缺少合法的 ts（时间戳字符串）字段。")
    event_type = data.get("type")
    if event_type == _TYPE_MESSAGE:
        return _parse_message(data, ts)
    if event_type == _TYPE_ENVELOPE:
        return _parse_envelope(data, ts)
    if event_type == _TYPE_SETTINGS:
        return _parse_settings(data, ts)
    raise SessionEventError(
        f"未知的事件类型 {event_type!r}；应是 {_TYPE_MESSAGE} / {_TYPE_ENVELOPE} / {_TYPE_SETTINGS}。"
    )


def _parse_message(data: dict[str, object], ts: str) -> MessageEvent:
    """从映射里取 message 事件的字段并校验；缺 role / text 或 attachment 非串即报错。"""
    role = data.get("role")
    text = data.get("text")
    attachment = data.get("attachment")
    if not isinstance(role, str):
        raise SessionEventError("message 事件缺少合法的 role（字符串）字段。")
    if not isinstance(text, str):
        raise SessionEventError("message 事件缺少合法的 text（字符串）字段。")
    if attachment is not None and not isinstance(attachment, str):
        raise SessionEventError("message 事件的 attachment 字段应是字符串或不出现。")
    return MessageEvent(ts=ts, role=role, text=text, attachment=attachment)


def _parse_envelope(data: dict[str, object], ts: str) -> EnvelopeEvent:
    """从映射里取 envelope 事件的 request 并校验；缺失或非对象即报错。"""
    request = data.get("request")
    if not isinstance(request, dict):
        raise SessionEventError("envelope 事件缺少合法的 request（JSON 对象）字段。")
    return EnvelopeEvent(ts=ts, request=cast(dict[str, JsonValue], request))


def _parse_settings(data: dict[str, object], ts: str) -> SettingsEvent:
    """从映射里取 settings 事件的 settings 并校验；缺失或非对象即报错。"""
    settings = data.get("settings")
    if not isinstance(settings, dict):
        raise SessionEventError("settings 事件缺少合法的 settings（JSON 对象）字段。")
    return SettingsEvent(ts=ts, settings=cast(dict[str, JsonValue], settings))
