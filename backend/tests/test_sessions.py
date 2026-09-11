"""单元测试：sessions 会话持久化（会话生命周期、事件 append-only 回放、请求信封、附件重名不覆盖、崩溃安全、id 校验）。

全部离线、用 temp_data_root fixture 把数据根隔离到临时目录；golden 契约用 tests/fixtures/events.jsonl（手写标准事件流）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from dataset_factory.sessions import (
    EnvelopeEvent,
    JsonValue,
    MessageEvent,
    SessionError,
    SessionEventError,
    SessionIdError,
    SessionNotFoundError,
    append_envelope,
    append_message,
    attachment_path,
    create_session,
    dump_event,
    latest_session_id,
    list_sessions,
    parse_event,
    read_events,
    save_attachment,
)

_FIXTURE_EVENTS = Path(__file__).parent / "fixtures" / "events.jsonl"


def _events_file(root: Path, session_id: str) -> Path:
    return root / "sessions" / session_id / "events.jsonl"


def _attachments_dir(root: Path, session_id: str) -> Path:
    return root / "sessions" / session_id / "attachments"


def _make_image(path: Path, data: bytes = b"img") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_create_session_makes_empty_events_file(temp_data_root: Path) -> None:
    """新建会话：目录 + 空 events.jsonl 立即存在，可被 list / latest 识别、回放为空。"""
    session_id = create_session()

    assert _events_file(temp_data_root, session_id).is_file()
    assert list_sessions() == [session_id]
    assert latest_session_id() == session_id
    assert read_events(session_id) == []


def test_list_and_latest_empty_when_no_sessions(temp_data_root: Path) -> None:
    """一个会话都没有：list 返回空列表、latest 返回 None。"""
    assert list_sessions() == []
    assert latest_session_id() is None


def test_sessions_listed_in_creation_order(temp_data_root: Path) -> None:
    """连创多个会话：list 按创建时间正序（旧→新），latest 是最后创建的那个。"""
    first = create_session()
    second = create_session()
    third = create_session()

    assert list_sessions() == [first, second, third]
    assert latest_session_id() == third


def test_list_skips_junk_dirs(temp_data_root: Path) -> None:
    """list 只认含 events.jsonl 的目录：跳过点前缀临时目录与没有事件流的杂目录。"""
    session_id = create_session()
    sessions_root = temp_data_root / "sessions"
    (sessions_root / ".tmp-junk").mkdir()
    (sessions_root / "not-a-session").mkdir()

    assert list_sessions() == [session_id]


def test_create_session_ids_unique_on_same_tick(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一时钟 tick 连创两个会话：id 不撞（撞名加 -<序号>），两个会话各自独立存在。"""
    from dataset_factory.sessions import store

    class _FixedDatetime:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 9, 11, 10, 30, 0, 123456)

    monkeypatch.setattr(store, "datetime", _FixedDatetime)

    first = create_session()
    second = create_session()

    assert second == f"{first}-1"
    assert list_sessions() == [first, second]


def test_append_message_round_trips(temp_data_root: Path) -> None:
    """追加消息后回放：得到 role / text 一致、带 ts、无附件的 MessageEvent。"""
    session_id = create_session()

    append_message(session_id, "user", "给这张图打个标")
    events = read_events(session_id)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, MessageEvent)
    assert event.role == "user"
    assert event.text == "给这张图打个标"
    assert event.attachment is None
    assert event.ts


def test_append_message_with_attachment_round_trips(temp_data_root: Path) -> None:
    """带附件名的消息回放：attachment 字段原样保留。"""
    session_id = create_session()

    append_message(session_id, "user", "描述这张图", attachment="cat.jpg")
    event = read_events(session_id)[0]

    assert isinstance(event, MessageEvent)
    assert event.attachment == "cat.jpg"


def test_append_envelope_round_trips(temp_data_root: Path) -> None:
    """追加请求信封后回放：request 结构原样取回（sessions 忠实存、不解析其内部）。"""
    session_id = create_session()
    request: dict[str, JsonValue] = {
        "model": "gpt-4o",
        "temperature": 0.2,
        "messages": [{"role": "system", "content": "你是打标助手"}],
    }

    append_envelope(session_id, request)
    event = read_events(session_id)[0]

    assert isinstance(event, EnvelopeEvent)
    assert event.request == request


def test_events_replay_in_append_order(temp_data_root: Path) -> None:
    """多次追加（消息与信封交织）：回放严格按落盘先后。"""
    session_id = create_session()

    append_message(session_id, "user", "第一轮")
    append_envelope(session_id, {"model": "m"})
    append_message(session_id, "assistant", "第一轮回复")
    events = read_events(session_id)

    assert len(events) == 3
    assert isinstance(events[0], MessageEvent)
    assert isinstance(events[1], EnvelopeEvent)
    assert isinstance(events[2], MessageEvent)
    assert events[0].text == "第一轮"
    assert events[2].text == "第一轮回复"


def test_read_golden_events(temp_data_root: Path) -> None:
    """契约测试：读手写标准 events.jsonl，解析出 message / envelope / message 三个事件（钉死磁盘 schema）。"""
    session_id = create_session()
    _events_file(temp_data_root, session_id).write_text(
        _FIXTURE_EVENTS.read_text(encoding="utf-8"), encoding="utf-8"
    )

    events = read_events(session_id)

    assert len(events) == 3
    first, envelope, last = events
    assert isinstance(first, MessageEvent)
    assert first.role == "user"
    assert first.attachment == "cat.jpg"
    assert isinstance(envelope, EnvelopeEvent)
    assert envelope.request["model"] == "gpt-4o"
    assert isinstance(last, MessageEvent)
    assert last.role == "assistant"


def test_append_is_append_only_not_rewrite(temp_data_root: Path) -> None:
    """append-only：追加第二条不改写第一条，磁盘上是两行完整 JSON。"""
    session_id = create_session()

    append_message(session_id, "user", "第一条")
    append_message(session_id, "user", "第二条")
    raw = _events_file(temp_data_root, session_id).read_text(encoding="utf-8")

    assert raw.count("\n") == 2
    assert "第一条" in raw
    assert "第二条" in raw


def test_trailing_partial_line_tolerated(temp_data_root: Path) -> None:
    """崩溃残留：末尾写了一半、无结尾换行的行被宽容丢弃，之前的完整事件照常回放。"""
    session_id = create_session()
    append_message(session_id, "user", "完整的一条")
    path = _events_file(temp_data_root, session_id)
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write('{"type": "message", "ts": "x", "role": "user", "text": "写到一半')

    events = read_events(session_id)

    assert len(events) == 1
    assert isinstance(events[0], MessageEvent)
    assert events[0].text == "完整的一条"


def test_corrupt_middle_line_raises(temp_data_root: Path) -> None:
    """中间行损坏（非末尾残缺）→ SessionEventError（fail loud，不当崩溃残留宽容）。"""
    session_id = create_session()
    _events_file(temp_data_root, session_id).write_text(
        '{"type": "message", "ts": "a", "role": "user", "text": "好"}\n'
        "这一行不是 JSON\n"
        '{"type": "message", "ts": "c", "role": "user", "text": "也好"}\n',
        encoding="utf-8",
    )

    with pytest.raises(SessionEventError, match="损坏行"):
        read_events(session_id)


def test_read_non_utf8_events_raises(temp_data_root: Path) -> None:
    """事件流不是合法 UTF-8（写了非法字节）→ SessionEventError（损坏 fail loud）。"""
    session_id = create_session()
    _events_file(temp_data_root, session_id).write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(SessionEventError, match="UTF-8"):
        read_events(session_id)


def test_envelope_persisted_before_return(temp_data_root: Path) -> None:
    """请求信封先落盘：append_envelope 返回后事件已在磁盘上（fsync，供「先落盘再发」）。"""
    session_id = create_session()

    append_envelope(session_id, {"model": "gpt-4o"})
    raw = _events_file(temp_data_root, session_id).read_text(encoding="utf-8")

    assert "envelope" in raw
    assert "gpt-4o" in raw


def test_save_attachment_copies_into_session(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """存附件：复制进会话 attachments/、返回原名、内容一致（会话自包含副本）。"""
    session_id = create_session()
    source = _make_image(tmp_path / "cat.jpg", b"jpeg-bytes")

    name = save_attachment(session_id, source)
    dest = _attachments_dir(temp_data_root, session_id) / name

    assert name == "cat.jpg"
    assert dest.read_bytes() == b"jpeg-bytes"


def test_save_attachment_duplicate_name_adds_sequence(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """重名不覆盖：同名图片存两次 → cat.jpg 与 cat-1.jpg 并存，各自内容独立。"""
    session_id = create_session()
    first = _make_image(tmp_path / "a" / "cat.jpg", b"v1")
    second = _make_image(tmp_path / "b" / "cat.jpg", b"v2")

    name1 = save_attachment(session_id, first)
    name2 = save_attachment(session_id, second)
    attachments = _attachments_dir(temp_data_root, session_id)

    assert (name1, name2) == ("cat.jpg", "cat-1.jpg")
    assert (attachments / name1).read_bytes() == b"v1"
    assert (attachments / name2).read_bytes() == b"v2"


def test_save_attachment_leaves_no_temp_file(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """原子复制收尾干净：attachments/ 里只有附件本身，没有点前缀临时文件残留。"""
    session_id = create_session()
    source = _make_image(tmp_path / "cat.jpg")

    save_attachment(session_id, source)

    assert sorted(
        p.name for p in _attachments_dir(temp_data_root, session_id).iterdir()
    ) == ["cat.jpg"]


def test_save_attachment_copy_failure_cleans_temp(
    tmp_path: Path, temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """崩溃安全：附件改名失败 → SessionError，attachments/ 里不留点前缀临时文件。"""
    session_id = create_session()
    source = _make_image(tmp_path / "cat.jpg")

    def _boom(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("dataset_factory.sessions.store.os.replace", _boom)

    with pytest.raises(SessionError, match="无法把附件"):
        save_attachment(session_id, source)

    assert list(_attachments_dir(temp_data_root, session_id).iterdir()) == []


def test_save_attachment_missing_source_raises(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """附件源不是文件 → SessionError。"""
    session_id = create_session()

    with pytest.raises(SessionError, match="不是文件"):
        save_attachment(session_id, tmp_path / "nope.jpg")


def test_save_attachment_missing_session_raises(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """往不存在的会话存附件 → SessionNotFoundError。"""
    source = _make_image(tmp_path / "cat.jpg")

    with pytest.raises(SessionNotFoundError, match="未找到会话"):
        save_attachment("20990101-000000-000000", source)


def test_attachment_path_locates_file(tmp_path: Path, temp_data_root: Path) -> None:
    """attachment_path 定位到已存附件的绝对路径，可读回字节。"""
    session_id = create_session()
    source = _make_image(tmp_path / "cat.jpg", b"jpeg-bytes")
    name = save_attachment(session_id, source)

    path = attachment_path(session_id, name)

    assert path.is_file()
    assert path.read_bytes() == b"jpeg-bytes"


def test_attachment_path_missing_raises(temp_data_root: Path) -> None:
    """定位不存在的附件 → SessionNotFoundError。"""
    session_id = create_session()

    with pytest.raises(SessionNotFoundError, match="未找到"):
        attachment_path(session_id, "nope.jpg")


def test_attachment_path_rejects_traversal(temp_data_root: Path) -> None:
    """附件名含目录穿越（../）→ SessionError（挡住逃出 attachments/）。"""
    session_id = create_session()

    with pytest.raises(SessionError, match="非法"):
        attachment_path(session_id, "../events.jsonl")


@pytest.mark.parametrize(
    "bad_id",
    ["", "   ", "a/b", "a\\b", "..", ".hidden", "bad\x00id", "lead ", "trail\t"],
)
def test_read_events_rejects_invalid_id(temp_data_root: Path, bad_id: str) -> None:
    """非法会话 id（空 / 首尾空白 / 路径分隔符 / 点开头 / 控制字符）→ SessionIdError。"""
    with pytest.raises(SessionIdError, match="会话 id"):
        read_events(bad_id)


def test_append_to_missing_session_raises(temp_data_root: Path) -> None:
    """往不存在的会话追加消息 → SessionNotFoundError。"""
    with pytest.raises(SessionNotFoundError, match="未找到会话"):
        append_message("20990101-000000-000000", "user", "在吗")


def test_append_message_unencodable_raises(temp_data_root: Path) -> None:
    """消息含 UTF-8 无法编码的字符（孤立代理项）→ SessionError，翻译掉裸 UnicodeEncodeError。"""
    session_id = create_session()

    with pytest.raises(SessionError, match="无法编码"):
        append_message(session_id, "user", "x" + chr(0xD800))


def test_append_envelope_unserializable_raises(temp_data_root: Path) -> None:
    """信封 request 含不可 JSON 序列化的值 → SessionError（边界运行时校验，不甩裸 TypeError）。"""
    session_id = create_session()
    bad = cast(dict[str, JsonValue], {"bad": object()})

    with pytest.raises(SessionError, match="不可 JSON 序列化"):
        append_envelope(session_id, bad)


def test_dump_message_omits_attachment_when_none() -> None:
    """dump_event：无附件的消息不写 attachment 键（磁盘 schema 更干净）。"""
    line = dump_event(MessageEvent(ts="t", role="user", text="x", attachment=None))

    assert "attachment" not in json.loads(line)


def test_parse_event_rejects_non_mapping() -> None:
    """parse_event 顶层不是映射（是列表）→ SessionEventError。"""
    with pytest.raises(SessionEventError, match="JSON 对象"):
        parse_event([1, 2, 3])


def test_parse_event_requires_ts() -> None:
    """parse_event 缺 ts 字段 → SessionEventError。"""
    with pytest.raises(SessionEventError, match="ts"):
        parse_event({"type": "message", "role": "user", "text": "x"})


def test_parse_event_rejects_bad_attachment() -> None:
    """parse_event 的 attachment 非字符串 → SessionEventError。"""
    with pytest.raises(SessionEventError, match="attachment"):
        parse_event(
            {
                "type": "message",
                "ts": "t",
                "role": "user",
                "text": "x",
                "attachment": 3,
            }
        )


def test_parse_envelope_requires_request() -> None:
    """parse_event 的 envelope 缺 request 字段 → SessionEventError。"""
    with pytest.raises(SessionEventError, match="request"):
        parse_event({"type": "envelope", "ts": "t"})
