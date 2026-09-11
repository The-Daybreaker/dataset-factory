"""会话持久化（数据域）——读写只收敛在本模块。

一会话一目录 ``sessions/<会话id>/``（会话 id = 创建时间戳，定宽、字典序即时间序）：
``events.jsonl`` 是 append-only 事件流（消息 + 请求信封 + 设置，每行一个 JSON 对象，只追加
不改写）；``attachments/`` 存本会话图片副本（原名 + 序号、重名不覆盖，会话自包含）。恢复 = 读
最新目录回放。崩溃 / 中断安全靠三点：append-only（已落盘的行不受后续崩溃影响）、每次追加后
fsync（「请求信封先落盘再发」的依据）、回放时宽容丢弃末尾写了一半的残缺行（其余损坏仍 fail
loud）。数据根复用共享 ``_fs``；本模块禁 import 入口层 / llm / 同层数据域（分层契约守）。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .._fs import data_root
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
    SettingsEvent,
    dump_event,
    parse_event,
)

_SESSIONS_DIRNAME = "sessions"
_EVENTS_FILENAME = "events.jsonl"
_ATTACHMENTS_DIRNAME = "attachments"
_STAMP_FORMAT = (
    "%Y%m%d-%H%M%S-%f"  # 定宽、字典序即时间序；微秒精度让同秒多次创建不撞名。
)

# id / 附件名含路径分隔符、控制字符或 Windows 不允许的字符即非法：只能是单段跨平台安全名。
_FORBIDDEN_PATH_CHARS = re.compile(r'[/\\<>:"|?*\x00-\x1f\x7f]')


def _sessions_dir() -> Path:
    """会话库根目录 = 数据根下的 sessions/。"""
    return data_root() / _SESSIONS_DIRNAME


def _session_dir(session_id: str) -> Path:
    """某会话 id 对应的目录 sessions/<会话id>/。"""
    return _sessions_dir() / session_id


def _events_path(session_id: str) -> Path:
    """某会话的事件流文件路径 sessions/<会话id>/events.jsonl。"""
    return _session_dir(session_id) / _EVENTS_FILENAME


def _validate_id(session_id: str) -> None:
    """校验会话 id；不合法即 SessionIdError（挡住路径穿越与跨平台非法名）。

    Args:
        session_id: 待校验的会话 id（将作为目录名 sessions/<id>）。

    Raises:
        SessionIdError: id 为空 / 首尾含空白 / 含非法字符 / 以点开头。
    """
    if not session_id or not session_id.strip():
        raise SessionIdError("会话 id 不能为空。")
    if session_id != session_id.strip():
        raise SessionIdError(f"会话 id {session_id!r} 首尾含空白；请去掉后再试。")
    if _FORBIDDEN_PATH_CHARS.search(session_id):
        raise SessionIdError(
            f"会话 id {session_id!r} 含非法字符（路径分隔符、控制字符或 Windows 不允许的 "
            '< > : " | ? *）；id 只能是单段跨平台安全的目录名。'
        )
    if session_id.startswith("."):
        raise SessionIdError(
            f"会话 id {session_id!r} 不能以点开头（点前缀保留给临时文件）。"
        )


def _validate_attachment_name(name: str) -> None:
    """校验附件名；不合法即 SessionError（挡住路径穿越，只允许单段文件名）。

    Args:
        name: 附件在 attachments/ 下的文件名。

    Raises:
        SessionError: 名为空 / 含非法字符 / 含目录部分（不是单段文件名）。
    """
    if not name or _FORBIDDEN_PATH_CHARS.search(name) or Path(name).name != name:
        raise SessionError(f"附件名 {name!r} 非法；只能是单段安全文件名。")


def _require_session(session_id: str) -> Path:
    """校验 id 且确认会话存在，返回其 events.jsonl 路径；不存在即 SessionNotFoundError。"""
    _validate_id(session_id)
    path = _events_path(session_id)
    if not path.is_file():
        raise SessionNotFoundError(
            f"未找到会话 {session_id!r}；用 list_sessions 查看现有会话。"
        )
    return path


def _new_session_id(sessions_root: Path) -> str:
    """生成一个不撞名的会话 id：创建时间戳，同一 tick 撞名则加 -<序号>。

    Windows 系统时钟粒度较粗（约 15ms），同一 tick 内连创两个会话会得到相同时间戳；加序号
    保证不撞（会话 id 即目录名，撞名会覆盖旧会话）。
    """
    stamp = datetime.now().strftime(_STAMP_FORMAT)
    candidate = stamp
    seq = 1
    while (sessions_root / candidate).exists():
        candidate = f"{stamp}-{seq}"
        seq += 1
    return candidate


def _unique_attachment_name(directory: Path, original: str) -> str:
    """给附件取一个不撞名的文件名：保留原名，冲突则在 stem 后加 -<序号>（重名不覆盖）。"""
    source_name = Path(original)
    stem, suffix = source_name.stem, source_name.suffix
    candidate = f"{stem}{suffix}"
    seq = 1
    while (directory / candidate).exists():
        candidate = f"{stem}-{seq}{suffix}"
        seq += 1
    return candidate


def create_session() -> str:
    """新建一个会话，返回其 id（= 创建时间戳，也是 sessions/ 下的目录名）。

    建目录并落一个空 events.jsonl——会话一创建即完整合法、能立刻被 list_sessions /
    latest_session_id 识别（attachments/ 留到首次存附件时再建）。

    Returns:
        新会话的 id。

    Raises:
        SessionError: 目录或空事件流创建失败（底层 OSError）。
    """
    sessions_root = _sessions_dir()
    session_id = _new_session_id(sessions_root)
    session_dir = sessions_root / session_id
    try:
        session_dir.mkdir(parents=True)
        (session_dir / _EVENTS_FILENAME).touch()
    except OSError as exc:
        raise SessionError(
            f"无法创建会话目录 {session_dir}：{exc.strerror or exc}"
        ) from exc
    return session_id


def list_sessions() -> list[str]:
    """列出全部会话 id，按创建时间正序（目录名 = 定宽时间戳，字典序即时间序）。

    只认 sessions/ 下含 events.jsonl 的子目录：跳过点前缀的临时目录与不含事件流的杂目录。
    库目录不存在时返回空列表（还没有任何会话，不算错）。

    Returns:
        会话 id 列表，从最旧到最新。
    """
    directory = _sessions_dir()
    if not directory.is_dir():
        return []
    return sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and (entry / _EVENTS_FILENAME).is_file()
    )


def latest_session_id() -> str | None:
    """最新（创建时间最晚）的会话 id；一个会话都没有时返回 None（恢复 = 读最新目录回放）。"""
    sessions = list_sessions()
    return sessions[-1] if sessions else None


def read_events(session_id: str) -> list[SessionEvent]:
    """回放某会话的全部事件（按落盘顺序）。

    崩溃安全：append 每行写「JSON + 换行」，正常文件总以换行结尾；崩溃可能留下末尾写了一半
    的残缺行——回放时宽容丢弃它（尚未写完的事件本就不算数），其余任何损坏行仍 fail loud。

    Args:
        session_id: 会话 id。

    Returns:
        事件列表，按落盘先后。

    Raises:
        SessionIdError: id 非法。
        SessionNotFoundError: 没有这个会话。
        SessionEventError: 事件流非 UTF-8，或含损坏 / 非法事件行。
        SessionError: 事件流不可读（底层 OSError）。
    """
    path = _require_session(session_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SessionEventError(
            f"会话 {session_id!r} 的事件流不是合法 UTF-8；文件可能已损坏。"
        ) from exc
    except OSError as exc:
        raise SessionError(
            f"无法读取会话 {session_id!r} 的事件流：{exc.strerror or exc}"
        ) from exc
    return _parse_events(raw, session_id)


def _parse_events(raw: str, session_id: str) -> list[SessionEvent]:
    """把事件流全文解析成事件列表：丢弃末尾残缺行，其余逐行解析（损坏即 fail loud）。"""
    body = raw if (not raw or raw.endswith("\n")) else raw[: raw.rfind("\n") + 1]
    events: list[SessionEvent] = []
    for line in body.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionEventError(
                f"会话 {session_id!r} 的事件流含损坏行（不是合法 JSON）：{exc}"
            ) from exc
        events.append(parse_event(obj))
    return events


def append_message(
    session_id: str, role: str, text: str, attachment: str | None = None
) -> None:
    """往会话追加一条消息事件（append-only，写后 fsync 确保落盘再返回）。

    Args:
        session_id: 会话 id。
        role: 消息角色（system / user / assistant）；sessions 只当字符串忠实存、不校验语义。
        text: 消息文本。
        attachment: 可选，已存进本会话 attachments/ 的图片文件名。

    Raises:
        SessionIdError: id 非法。
        SessionNotFoundError: 没有这个会话。
        SessionError: 写入失败，或内容含 UTF-8 无法编码的字符。
    """
    ts = datetime.now().isoformat()
    _append_event(
        session_id, MessageEvent(ts=ts, role=role, text=text, attachment=attachment)
    )


def append_envelope(session_id: str, request: Mapping[str, JsonValue]) -> None:
    """往会话追加一条请求信封事件（渲染后完整请求的 JSON 快照；先落盘再发）。

    request 由编排层渲染（含 llm 消息与模型参数），sessions 原样存、不解析其内部。

    Args:
        session_id: 会话 id。
        request: 渲染后的完整请求（可 JSON 序列化的映射）。

    Raises:
        SessionIdError: id 非法。
        SessionNotFoundError: 没有这个会话。
        SessionError: 写入失败、request 不可 JSON 序列化，或含 UTF-8 无法编码的字符。
    """
    ts = datetime.now().isoformat()
    _append_event(session_id, EnvelopeEvent(ts=ts, request=dict(request)))


def append_settings(session_id: str, settings: Mapping[str, JsonValue]) -> None:
    """往会话追加一条设置变更事件（当前值 = 回放取最后一条 settings）。

    settings 由编排层渲染（如基础提示词名与启用 skill 清单），sessions 原样存、不解析其内部。

    Args:
        session_id: 会话 id。
        settings: 本会话当前设置（可 JSON 序列化的映射）。

    Raises:
        SessionIdError: id 非法。
        SessionNotFoundError: 没有这个会话。
        SessionError: 写入失败、settings 不可 JSON 序列化，或含 UTF-8 无法编码的字符。
    """
    ts = datetime.now().isoformat()
    _append_event(session_id, SettingsEvent(ts=ts, settings=dict(settings)))


def _append_event(session_id: str, event: SessionEvent) -> None:
    """把事件序列化成一整行、追加到会话事件流末尾（O_APPEND 追加 + fsync 落盘）。"""
    path = _require_session(session_id)
    try:
        line = dump_event(event) + "\n"
    except TypeError as exc:
        raise SessionError(
            f"无法序列化会话 {session_id!r} 的事件：含不可 JSON 序列化的值（{exc}）"
        ) from exc
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except UnicodeEncodeError as exc:
        raise SessionError(
            f"无法写入会话 {session_id!r} 的事件流：内容含 UTF-8 无法编码的字符（{exc.reason}）"
        ) from exc
    except OSError as exc:
        raise SessionError(
            f"无法写入会话 {session_id!r} 的事件流：{exc.strerror or exc}"
        ) from exc


def save_attachment(session_id: str, source: Path) -> str:
    """把一张图片复制进会话的 attachments/（自包含副本），返回会话内的文件名。

    重名不覆盖：原名冲突就在 stem 后加 -<序号>（photo.jpg → photo-1.jpg → photo-2.jpg）。
    复制走「临时名 + os.replace 改名」，崩溃不留半个损坏图；attachments/ 首次存附件时才建。

    Args:
        session_id: 会话 id。
        source: 源图片文件路径。

    Returns:
        附件在会话 attachments/ 下的最终文件名（含扩展名）。

    Raises:
        SessionIdError: id 非法。
        SessionNotFoundError: 没有这个会话。
        SessionError: 源不是文件，或复制 / 改名失败（底层 OSError）。
    """
    _require_session(session_id)
    if not source.is_file():
        raise SessionError(f"附件源 {source} 不是文件；请指向一个存在的图片文件。")
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise SessionError(f"无法读取附件源 {source}：{exc.strerror or exc}") from exc
    return _store_attachment(session_id, source.name, data)


def save_attachment_bytes(session_id: str, name: str, data: bytes) -> str:
    """把图片字节直接存进会话的 attachments/，返回会话内的文件名。

    与 save_attachment（源文件复制）相对：HTTP 入口收到的图片是网络传来的字节、没有
    源文件，直接落字节；重名不覆盖与崩溃安全语义同 save_attachment。

    Args:
        session_id: 会话 id。
        name: 原始文件名（用于保留名字；冲突加序号）。
        data: 图片字节。

    Returns:
        附件在会话 attachments/ 下的最终文件名（含扩展名）。

    Raises:
        SessionIdError: id 非法。
        SessionNotFoundError: 没有这个会话。
        SessionError: 写入失败（底层 OSError）。
    """
    _require_session(session_id)
    return _store_attachment(session_id, name, data)


def _store_attachment(session_id: str, name: str, data: bytes) -> str:
    """落盘一张附件：唯一名 + 临时文件 + os.replace（两种存入方式的共用实现）。"""
    attachments_dir = _session_dir(session_id) / _ATTACHMENTS_DIRNAME
    unique = _unique_attachment_name(attachments_dir, name)
    tmp = attachments_dir / f".{unique}.tmp{os.urandom(4).hex()}"
    try:
        attachments_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, attachments_dir / unique)
    except OSError as exc:
        raise SessionError(
            f"无法把附件 {name!r} 存进会话 {session_id!r}：{exc.strerror or exc}"
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return unique


def attachment_path(session_id: str, name: str) -> Path:
    """定位会话内某附件的绝对路径（供回放重建时读图片字节）。

    Args:
        session_id: 会话 id。
        name: 附件文件名（attachments/ 下）。

    Returns:
        附件的绝对路径。

    Raises:
        SessionIdError: 会话 id 非法。
        SessionError: 附件名非法（含目录部分 / 非法字符）。
        SessionNotFoundError: 没有这个附件（或会话）。
    """
    _validate_id(session_id)
    _validate_attachment_name(name)
    path = _session_dir(session_id) / _ATTACHMENTS_DIRNAME / name
    if not path.is_file():
        raise SessionNotFoundError(f"未找到会话 {session_id!r} 的附件 {name!r}。")
    return path
