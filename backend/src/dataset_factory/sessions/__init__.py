"""sessions 数据域：会话持久化——events.jsonl 事件流 + attachments/ 图片副本（全项目只有本模块碰会话文件）。

对外接口：
- 事件模型与序列化：MessageEvent / EnvelopeEvent / SessionEvent / JsonValue / dump_event / parse_event（events.jsonl 磁盘格式的唯一事实来源）
- 会话生命周期：create_session / list_sessions / latest_session_id（恢复 = 读最新目录回放）
- 事件读写：append_message / append_envelope（append-only + fsync）/ read_events（回放）
- 附件：save_attachment（原名 + 序号重名不覆盖）/ attachment_path
- 异常：SessionError 基类 + SessionIdError / SessionNotFoundError / SessionEventError
"""

from .errors import (
    SessionError,
    SessionEventError,
    SessionIdError,
    SessionNotFoundError,
)
from .model import (
    EnvelopeEvent,
    JsonValue,
    MessageEvent,
    SessionEvent,
    dump_event,
    parse_event,
)
from .store import (
    append_envelope,
    append_message,
    attachment_path,
    create_session,
    latest_session_id,
    list_sessions,
    read_events,
    save_attachment,
)

__all__ = [
    "EnvelopeEvent",
    "JsonValue",
    "MessageEvent",
    "SessionError",
    "SessionEvent",
    "SessionEventError",
    "SessionIdError",
    "SessionNotFoundError",
    "append_envelope",
    "append_message",
    "attachment_path",
    "create_session",
    "dump_event",
    "latest_session_id",
    "list_sessions",
    "parse_event",
    "read_events",
    "save_attachment",
]
