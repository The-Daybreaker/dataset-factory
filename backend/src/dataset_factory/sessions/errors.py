"""sessions 数据域的异常类型：只抛类型化异常，消息可操作、fail loud、不甩原始栈。"""

from __future__ import annotations


class SessionError(Exception):
    """会话持久化操作错误的基类；消息只描述「哪里错、怎么修」。"""


class SessionIdError(SessionError):
    """会话 id 非法（空、含路径分隔符 / 控制字符 / Windows 非法字符、以点开头）——挡住路径穿越。"""


class SessionNotFoundError(SessionError):
    """按会话 id 找不到会话（读事件 / 追加 / 存附件 / 定位附件到不存在的会话）。"""


class SessionEventError(SessionError):
    """事件流损坏或事件格式非法（非 UTF-8、某行非合法 JSON、缺字段、未知事件类型）。"""
