"""集成 + 端到端测试：labeling 编排引擎（组装一轮打标、设置切换、历史回放、信封落盘、恢复）。

集成测试编排 prompts + skills + sessions + llm 四模块（fake_completer 断言「引擎拼了什么」，
不真调 API）；端到端测试整条打标流程（建提示词 → 导入 skill → 发图打标 → 恢复会话）；
二期追加纯素材路径（label_material：无会话零写盘 + 运行时护栏 + 处理时刻哈希）。
全部离线、用 temp_data_root fixture 隔离数据根。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import cast

import pytest

from dataset_factory.labeling import (
    AttachmentReadError,
    EmptyTurnError,
    LabelingEngine,
    MaterialOversizeError,
    MaterialReadError,
    PromptNotSelectedError,
    SessionSettings,
    SettingsFormatError,
    StreamFinished,
    StreamStarted,
)
from dataset_factory.labeling import engine as labeling_engine_module
from dataset_factory.llm import (
    ImagePart,
    LLMError,
    Message,
    StreamDelta,
    TextPart,
    VideoPart,
)
from dataset_factory.prompts import (
    Prompt,
    PromptNotFoundError,
    read_prompt,
    save_prompt,
)
from dataset_factory.sessions import (
    EnvelopeEvent,
    JsonValue,
    MessageEvent,
    SessionEvent,
    SessionNotFoundError,
    SettingsEvent,
    append_settings,
    create_session,
    list_sessions,
    read_events,
)
from dataset_factory.skills import (
    SkillFormatError,
    SkillNotFoundError,
    import_skill,
    set_enabled,
)

from .conftest import FakeCompleter

_SKILL_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
_MODEL = "test-model"


def _save_prompt(name: str, body: str) -> None:
    """往提示词库存一条测试提示词。"""
    save_prompt(Prompt(name=name, description="测试提示词", body=body))


def _import_skill() -> str:
    """导入 fixture 的示例 skill，返回其稳定 ID（ID 化后设置与断言一律用 ID）。"""
    return import_skill(_SKILL_PACK).skill.id


def _skill_injection_text() -> str:
    """fixture skill 的期望注入全文：SKILL.md + references/ 全部文件（标记包裹）。

    手工拼装而非调 read_skill——测试与实现不共用同一组装逻辑，才有互相校验的意义。
    """
    skill_md = (_SKILL_PACK / "SKILL.md").read_text(encoding="utf-8")
    ref = (_SKILL_PACK / "references" / "detail.md").read_text(encoding="utf-8")
    return (
        skill_md
        + "\n\n"
        + f'<skill-file path="references/detail.md">\n{ref}\n</skill-file>'
    )


def _make_image(path: Path, data: bytes = b"fake-png-bytes") -> Path:
    """写一个假图片文件（fake completer 不做图片编码校验，字节任意）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _event_types(events: Sequence[SessionEvent]) -> list[str]:
    """把事件流折叠成类型名序列，便于断言事件顺序。"""
    names: list[str] = []
    for event in events:
        if isinstance(event, SettingsEvent):
            names.append("settings")
        elif isinstance(event, EnvelopeEvent):
            names.append("envelope")
        else:
            names.append("message")
    return names


def test_first_turn_assembles_system_skill_instruction_image(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """首轮组装：system=提示词正文；user = <skill> 包裹全文 + 指令 + 图片字节。"""
    _save_prompt("h3", "你是打标助手，输出一句话描述。")
    skill_name = _import_skill()
    skill_full = _skill_injection_text()
    image = _make_image(tmp_path / "cat.jpg", b"png!")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(
        prompt_id="h3",
        skill_ids=[skill_name],
        instruction="描述这张图",
        image=image,
    )

    assert result.caption == "打标结果"
    assert len(fake_completer.calls) == 1
    system, user = fake_completer.calls[0]
    assert system == Message(
        role="system", parts=(TextPart("你是打标助手，输出一句话描述。"),)
    )
    assert user.role == "user"
    assert user.parts == (
        TextPart(f"<skill>\n{skill_full}\n</skill>"),
        TextPart("描述这张图"),
        ImagePart(b"png!"),
    )


def test_first_turn_without_skills_and_image(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """无 skill 无图的纯文本轮：user 消息只有指令文本块。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    engine.label(prompt_id="h3", instruction="纯文本打标")

    user = fake_completer.calls[0][1]
    assert user.parts == (TextPart("纯文本打标"),)


def test_image_only_turn_allows_empty_instruction(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """纯图轮（指令为空、任务说明在基础提示词里）：user 消息只含图片块。"""
    _save_prompt("h3", "你是打标助手。")
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    engine.label(prompt_id="h3", instruction="", image=image)

    assert fake_completer.calls[0][1].parts == (ImagePart(b"fake-png-bytes"),)


def test_video_turn_sends_video_part(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """视频轮：VideoPart 进当轮 user 消息（fps / 帧上限随附件一并下发）。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    engine.label(
        prompt_id="h3",
        instruction="描述这个动作",
        video_bytes=b"fake-mp4-bytes",
        video_name="clip.mp4",
        video_fps=3,
        video_max_frames=8,
    )

    assert fake_completer.calls[0][1].parts == (
        TextPart("描述这个动作"),
        VideoPart(b"fake-mp4-bytes", fps=3, max_frames=8),
    )


def test_video_and_image_together_raises(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """视频与图片同轮提供 → ValueError（一期单素材/次）。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)
    image = _make_image(tmp_path / "cat.jpg")

    with pytest.raises(ValueError, match="二选一"):
        engine.label(
            prompt_id="h3",
            instruction="x",
            image=image,
            video_bytes=b"mp4",
        )


def test_label_stream_yields_events_and_persists(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """流式打标：事件 = Started → 增量… → Finished；终稿落盘、可恢复。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    events = list(engine.label_stream(prompt_id="h3", instruction="描述它"))

    assert isinstance(events[0], StreamStarted)
    deltas = [event for event in events if isinstance(event, StreamDelta)]
    assert "".join(delta.text for delta in deltas) == "打标结果"
    finished = events[-1]
    assert isinstance(finished, StreamFinished)
    assert finished.result.caption == "打标结果"
    snapshot = engine.restore(finished.result.session_id)
    assert snapshot.messages[-1].role == "assistant"
    assert snapshot.messages[-1].text == "打标结果"


def test_label_stream_persists_reasoning_with_caption(
    temp_data_root: Path,
) -> None:
    """流式打标：思考增量随终稿一起落盘、恢复会话可回看；下一轮装配不含思考文本。"""

    class _ThinkingCompleter(FakeCompleter):
        """按脚本产出思考与正文增量的假 Completer（记录收到的消息供装配断言）。

        继承 conftest 的假 Completer 而不另写一个：`Completer` 协议要求 `complete`
        也在（非流式路径本测试不走，但类型面要成立）。
        """

        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            self.calls.append(list(messages))
            yield StreamDelta(kind="reasoning", text="用户要一段描述。")
            yield StreamDelta(kind="reasoning", text="按基础提示词组织。")
            yield StreamDelta(kind="content", text="一只白")
            yield StreamDelta(kind="content", text="瓷茶杯。")

    completer = _ThinkingCompleter()
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(completer, _MODEL)

    events = list(engine.label_stream(prompt_id="h3", instruction="描述它"))

    finished = events[-1]
    assert isinstance(finished, StreamFinished)
    snapshot = engine.restore(finished.result.session_id)
    assistant = snapshot.messages[-1]
    assert (assistant.role, assistant.text) == ("assistant", "一只白瓷茶杯。")
    assert assistant.reasoning == "用户要一段描述。按基础提示词组织。"


def test_label_stream_blank_caption_persists_reasoning_partial(
    temp_data_root: Path,
) -> None:
    """流式正常走完但正文零字（思考型端点把产出花在思考上）→ 空白描述报错。

    已收到的思考落盘标 partial——刷新 / 重启可回看，不再只活在浏览器内存里
    （2026-09-23 用户实测曝光的落盘缺口）。
    """

    class _ReasoningOnlyCompleter(FakeCompleter):
        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            self.calls.append(list(messages))
            yield StreamDelta(kind="reasoning", text="这张图是一只猫。")
            yield StreamDelta(kind="reasoning", text="帽子是红色的，")

    completer = _ReasoningOnlyCompleter()
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(completer, _MODEL)

    started: list[StreamStarted] = []

    def _drain() -> None:
        for event in engine.label_stream(prompt_id="h3", instruction="描述它"):
            if isinstance(event, StreamStarted):
                started.append(event)

    with pytest.raises(LLMError, match="空白描述"):
        _drain()
    assert len(started) == 1

    snapshot = engine.restore(started[0].session_id)
    partial = snapshot.messages[-1]
    assert (partial.role, partial.text, partial.partial) == ("assistant", "", True)
    assert partial.reasoning == "这张图是一只猫。帽子是红色的，"


def test_label_stream_fully_empty_raises_without_partial(
    temp_data_root: Path,
) -> None:
    """连思考都没有的完全空白：只报错，不落空壳 partial。"""

    class _SilentCompleter(FakeCompleter):
        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            self.calls.append(list(messages))
            return iter(())

    completer = _SilentCompleter()
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(completer, _MODEL)

    started: list[StreamStarted] = []

    def _drain() -> None:
        for event in engine.label_stream(prompt_id="h3", instruction="描述它"):
            if isinstance(event, StreamStarted):
                started.append(event)

    with pytest.raises(LLMError, match="空白描述"):
        _drain()
    assert len(started) == 1

    snapshot = engine.restore(started[0].session_id)
    assert snapshot.messages[-1].role == "user"


def test_next_round_assembly_excludes_reasoning_text(
    temp_data_root: Path,
) -> None:
    """下一轮装配只取正文：思考不进请求（2026-09-20 用户定夺的回放口径不变）。"""

    class _ThinkingCompleter(FakeCompleter):
        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            self.calls.append(list(messages))
            yield StreamDelta(kind="reasoning", text="按基础提示词组织。")
            yield StreamDelta(kind="content", text="一只白瓷茶杯。")

    completer = _ThinkingCompleter()
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(completer, _MODEL)

    finished: StreamFinished | None = None
    for event in engine.label_stream(prompt_id="h3", instruction="描述它"):
        if isinstance(event, StreamFinished):
            finished = event
    assert finished is not None

    list(
        engine.label_stream(
            session_id=finished.result.session_id,
            prompt_id="h3",
            instruction="再改改",
        )
    )
    replayed = [
        part.text
        for message in completer.calls[1]
        if message.role == "assistant"
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert "一只白瓷茶杯。" in "".join(replayed)
    assert "按基础提示词组织" not in "".join(replayed)


def test_events_recorded_in_order(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """事件落盘顺序：settings → message(user) → envelope → message(assistant)。"""
    _save_prompt("h3", "你是打标助手。")
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(prompt_id="h3", instruction="打标", image=image)

    events = read_events(result.session_id)
    assert _event_types(events) == ["settings", "message", "envelope", "message"]
    assert isinstance(events[1], MessageEvent)
    assert (events[1].role, events[1].text, events[1].attachment) == (
        "user",
        "打标",
        "cat.jpg",
    )
    assert isinstance(events[3], MessageEvent)
    assert (events[3].role, events[3].text) == ("assistant", "打标结果")


def test_envelope_records_model_and_text_view(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """信封内容：模型名 + 纯文本消息视图（图片是占位文本，不是 base64）。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = _import_skill()
    skill_full = _skill_injection_text()
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(
        prompt_id="h3",
        skill_ids=[skill_name],
        instruction="打标",
        image=image,
    )

    envelope = next(
        event
        for event in read_events(result.session_id)
        if isinstance(event, EnvelopeEvent)
    )
    assert envelope.request == {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "你是打标助手。"},
            {
                "role": "user",
                "content": f"<skill>\n{skill_full}\n</skill>\n打标\n[图片: cat.jpg]",
            },
        ],
    }


def test_second_turn_replays_history_with_attachment_bytes(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """第二轮迭代改写：历史附件以真字节跟随重发、按原角色回放、当轮重新注入 skill。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = _import_skill()
    skill_full = _skill_injection_text()
    image = _make_image(tmp_path / "cat.jpg")
    completer = FakeCompleter(replies=["第一轮回复", "第二轮回复"])
    engine = LabelingEngine(completer, _MODEL)

    first = engine.label(
        prompt_id="h3",
        skill_ids=[skill_name],
        instruction="描述这张图",
        image=image,
    )
    second = engine.label(session_id=first.session_id, instruction="改成一句话")

    assert second.caption == "第二轮回复"
    assert second.session_id == first.session_id
    assert len(completer.calls) == 2
    system, history_user, history_assistant, current_user = completer.calls[1]
    assert system.parts == (TextPart("你是打标助手。"),)
    # 历史附件真字节跟随（2026-09-23 用户定，方案 A）：迭代改写时模型仍看得见原图。
    assert history_user.parts == (
        TextPart("描述这张图"),
        ImagePart(image.read_bytes(), label="[图片: cat.jpg]"),
    )
    assert history_assistant.parts == (TextPart("第一轮回复"),)
    assert current_user.parts == (
        TextPart(f"<skill>\n{skill_full}\n</skill>"),
        TextPart("改成一句话"),
    )
    # 信封视图仍脱敏：历史媒体渲染为占位文本（复盘可读、不灌 base64）。
    envelopes = [
        event.request
        for event in read_events(second.session_id)
        if isinstance(event, EnvelopeEvent)
    ]
    history_view = cast("list[JsonValue]", envelopes[1]["messages"])
    assert history_view[1] == {
        "role": "user",
        "content": "描述这张图\n[图片: cat.jpg]",
    }


def test_second_turn_replays_history_video_with_suffix_mime(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """历史视频附件按扩展名定 MIME、默认抽帧参数重发（当轮参数未随事件落盘）。"""
    _save_prompt("h3", "你是打标助手。")
    video = tmp_path / "clip.webm"
    video.write_bytes(b"fake webm bytes")
    completer = FakeCompleter(replies=["第一轮回复", "第二轮回复"])
    engine = LabelingEngine(completer, _MODEL)

    first = engine.label(
        prompt_id="h3",
        instruction="描述这段视频",
        video_bytes=video.read_bytes(),
        video_name="clip.webm",
    )
    engine.label(session_id=first.session_id, instruction="再简短些")

    _, history_user, _, _ = completer.calls[1]
    assert len(history_user.parts) == 2
    history_video = history_user.parts[1]
    assert isinstance(history_video, VideoPart)
    assert history_video.data == b"fake webm bytes"
    assert history_video.mime == "video/webm"
    assert history_video.label == "[视频: clip.webm]"


def test_settings_change_mid_session_appends_event(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """中途切换基础提示词与 skill 清单：追加新 settings 事件，下轮用新值组装。"""
    _save_prompt("旧提示词", "旧底座。")
    _save_prompt("新提示词", "新底座。")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(prompt_id="旧提示词", instruction="第一轮")
    second = engine.label(
        session_id=first.session_id,
        prompt_id="新提示词",
        skill_ids=[],
        instruction="第二轮",
    )

    settings_events = [
        event
        for event in read_events(second.session_id)
        if isinstance(event, SettingsEvent)
    ]
    assert [event.settings for event in settings_events] == [
        {"prompt": read_prompt("旧提示词").id, "skills": []},
        {"prompt": read_prompt("新提示词").id, "skills": []},
    ]
    assert fake_completer.calls[1][0].parts == (TextPart("新底座。"),)


def test_settings_unchanged_appends_no_event(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """设置未变（显式传相同值）：不追加多余的 settings 事件。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(prompt_id="h3", instruction="第一轮")
    engine.label(session_id=first.session_id, prompt_id="h3", instruction="第二轮")

    settings_events = [
        event
        for event in read_events(first.session_id)
        if isinstance(event, SettingsEvent)
    ]
    assert len(settings_events) == 1


def test_disabled_skill_skipped_but_kept_in_settings(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """库级停用的 skill：注入时跳过（user 消息无 skill 块），但会话设置里保留勾选记录。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = _import_skill()
    set_enabled(skill_name, enabled=False)
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(prompt_id="h3", skill_ids=[skill_name], instruction="打标")

    assert fake_completer.calls[0][1].parts == (TextPart("打标"),)
    settings_event = next(
        event
        for event in read_events(result.session_id)
        if isinstance(event, SettingsEvent)
    )
    assert settings_event.settings == {
        "prompt": read_prompt("h3").id,
        "skills": [skill_name],
    }


def test_missing_prompt_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """基础提示词在库中不存在：PromptNotFoundError 冒泡（可操作报错，fail loud）。"""
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(PromptNotFoundError):
        engine.label(prompt_id="不存在的提示词", instruction="打标")


def test_new_session_without_prompt_rejected_before_create(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """新会话不传基础提示词：报错且不留半成品会话（拦截在建会话之前）。"""
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(PromptNotSelectedError):
        engine.label(instruction="打标")

    assert list_sessions() == []


def test_existing_session_without_any_prompt_rejected(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """既有会话从未设置过提示词、本轮也不传：报 PromptNotSelectedError。"""
    session_id = create_session()
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(PromptNotSelectedError):
        engine.label(session_id=session_id, instruction="打标")


def test_empty_turn_rejected(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """指令为空且无图：EmptyTurnError（没有任何可打标的内容）。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(EmptyTurnError):
        engine.label(prompt_id="h3", instruction="   ")


def test_unknown_session_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """续接不存在的会话：SessionNotFoundError 冒泡。"""
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(SessionNotFoundError):
        engine.label(session_id="20990101-000000-000000", instruction="打标")


def test_llm_failure_keeps_envelope_for_review(
    temp_data_root: Path,
) -> None:
    """模型调用失败：信封已落盘、assistant 回复未落（「当时喂了什么」有据可查）。"""

    class _FailingCompleter:
        def complete(self, messages: Sequence[Message]) -> str:
            raise LLMError("模型不可用")

        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            raise LLMError("模型不可用")

    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(_FailingCompleter(), _MODEL)

    with pytest.raises(LLMError):
        engine.label(prompt_id="h3", instruction="打标")

    (session_id,) = list_sessions()
    events = read_events(session_id)
    assert _event_types(events) == ["settings", "message", "envelope"]


def test_broken_settings_event_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """设置事件结构非法（如 prompt 非字符串）：SettingsFormatError（fail loud）。"""
    _save_prompt("h3", "你是打标助手。")
    session_id = create_session()
    append_settings(session_id, {"prompt": 123, "skills": []})
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(SettingsFormatError):
        engine.label(session_id=session_id, instruction="打标")


def test_broken_settings_skills_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """设置事件的 skills 字段不是字符串数组：SettingsFormatError（fail loud）。"""
    _save_prompt("h3", "你是打标助手。")
    session_id = create_session()
    append_settings(session_id, {"prompt": "h3", "skills": "不是列表"})
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(SettingsFormatError):
        engine.label(session_id=session_id, instruction="打标")


def test_attachment_read_failure_translated(
    temp_data_root: Path,
    tmp_path: Path,
    fake_completer: FakeCompleter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """附件副本读取失败：OSError 翻译成 AttachmentReadError。"""
    import dataset_factory.labeling.engine as engine_module

    def _missing_path(session_id: str, name: str) -> Path:
        return Path("Z:/不存在/cat.jpg")

    _save_prompt("h3", "你是打标助手。")
    image = _make_image(tmp_path / "cat.jpg")
    monkeypatch.setattr(engine_module, "attachment_path", _missing_path)
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(AttachmentReadError):
        engine.label(prompt_id="h3", instruction="打标", image=image)


def test_restore_returns_settings_and_history(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """恢复会话：当前设置 + 按时间序的 user / assistant 历史消息（含附件名）。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = _import_skill()
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(
        prompt_id="h3",
        skill_ids=[skill_name],
        instruction="描述这张图",
        image=image,
    )
    engine.label(session_id=first.session_id, instruction="改成一句话")

    snapshot = engine.restore(first.session_id)

    assert snapshot.session_id == first.session_id
    assert snapshot.settings == SessionSettings(
        prompt_id=read_prompt("h3").id, skill_ids=(skill_name,)
    )
    assert [(m.role, m.text, m.attachment) for m in snapshot.messages] == [
        ("user", "描述这张图", "cat.jpg"),
        ("assistant", "打标结果", None),
        ("user", "改成一句话", None),
        ("assistant", "打标结果", None),
    ]


def test_restore_unknown_session_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """恢复不存在的会话：SessionNotFoundError 冒泡。"""
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(SessionNotFoundError):
        engine.restore("20990101-000000-000000")


def test_end_to_end_labeling_flow(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """端到端：建提示词 → 导入 skill → 发图打标 → 迭代改写 → 恢复会话，全链路走通。"""
    _save_prompt("h3-video", "你是 H3 视频打标助手，按官方格式输出 caption。")
    skill_name = _import_skill()
    image = _make_image(tmp_path / "素材图.jpg", b"real-image-bytes")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(
        prompt_id="h3-video",
        skill_ids=[skill_name],
        instruction="给这张图打个标",
        image=image,
    )
    second = engine.label(session_id=first.session_id, instruction="改写成两句话")
    snapshot = engine.restore(second.session_id)

    assert first.caption == "打标结果"
    assert second.caption == "打标结果"
    assert snapshot.settings == SessionSettings(
        prompt_id=read_prompt("h3-video").id, skill_ids=(skill_name,)
    )
    assert [m.role for m in snapshot.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert snapshot.messages[0].attachment == "素材图.jpg"
    events = read_events(second.session_id)
    assert _event_types(events) == [
        "settings",
        "message",
        "envelope",
        "message",
        "message",
        "envelope",
        "message",
    ]


def test_resume_with_missing_prompt_leaves_no_trace(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """续接时传不存在的提示词名：报错且会话零痕迹（不落坏设置、不留孤儿消息），下轮照常。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)
    first = engine.label(prompt_id="h3", instruction="第一轮")

    with pytest.raises(PromptNotFoundError):
        engine.label(session_id=first.session_id, prompt_id="不存在", instruction="改")

    events = read_events(first.session_id)
    assert _event_types(events) == ["settings", "message", "envelope", "message"]
    resumed = engine.label(session_id=first.session_id, instruction="再改一次")
    assert resumed.caption == "打标结果"
    assert fake_completer.calls[-1][0].parts == (TextPart("你是打标助手。"),)


def test_unknown_skill_name_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """勾选了库里不存在的 skill 名：SkillNotFoundError（fail loud；对比：库级停用才是静默跳过）。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(SkillNotFoundError):
        engine.label(prompt_id="h3", skill_ids=["拼错了"], instruction="打标")

    assert list_sessions() == []


def test_corrupt_skill_package_not_selected_does_not_break_round(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """库里有一个损坏包但本轮没勾选它：打标轮不受影响（列表宽容降级，A8 回归）。"""
    _save_prompt("h3", "你是打标助手。")
    bad_dir = temp_data_root / "skills" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\ndescription: x\n没有闭合", encoding="utf-8")
    engine = LabelingEngine(fake_completer, _MODEL)

    engine.label(prompt_id="h3", instruction="纯文本打标")

    assert fake_completer.calls[0][1].parts == (TextPart("纯文本打标"),)


def test_corrupt_skill_package_selected_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """勾选了损坏包：本轮明确拒绝（可读错误），不带病使用（A8 回归）。"""
    _save_prompt("h3", "你是打标助手。")
    bad_dir = temp_data_root / "skills" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\ndescription: x\n没有闭合", encoding="utf-8")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(SkillFormatError, match="未闭合"):
        engine.label(prompt_id="h3", skill_ids=["bad"], instruction="打标")

    assert list_sessions() == []


# ---------------------------------------------------------------------------
# 纯素材输入路径（label_material，二期 T35）：无会话零写盘 + 运行时护栏 + 处理时刻哈希
# ---------------------------------------------------------------------------


def test_material_image_labels_without_session(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """纯素材打标图片：拼装同会话路径、返回 caption 与素材哈希、全程不建会话。

    零写盘是无人值守路径的根基约定：没有事件流、信封与附件副本。
    """
    image = _make_image(tmp_path / "cat_001.jpg", b"png!")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label_material(
        image,
        prompt_body="你是打标助手，输出一句话描述。",
        skill_texts=["快照里的 skill 全文"],
    )

    assert result.caption == "打标结果"
    assert result.asset_hash == hashlib.sha256(b"png!").hexdigest()
    assert len(fake_completer.calls) == 1
    system, user = fake_completer.calls[0]
    assert system == Message(
        role="system", parts=(TextPart("你是打标助手，输出一句话描述。"),)
    )
    assert user.parts == (
        TextPart("<skill>\n快照里的 skill 全文\n</skill>"),
        ImagePart(b"png!"),
    )
    assert list_sessions() == []


def test_material_video_part_uses_suffix_mime_and_params(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """纯素材打标视频：MIME 按扩展名映射（.mov → video/quicktime）。

    fps / 帧上限随调用下发，哈希同样返回。
    """
    video = tmp_path / "clip_001.mov"
    video.write_bytes(b"fake-mov-bytes")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label_material(
        video,
        prompt_body="你是打标助手。",
        video_fps=3,
        video_max_frames=8,
    )

    assert result.asset_hash == hashlib.sha256(b"fake-mov-bytes").hexdigest()
    assert fake_completer.calls[0][1].parts == (
        VideoPart(b"fake-mov-bytes", mime="video/quicktime", fps=3, max_frames=8),
    )


def test_material_missing_file_raises_read_error(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """素材不存在（缺失条目的运行时表现）：MaterialReadError，且不调模型。"""
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(MaterialReadError, match=re.escape("cat_001.jpg")):
        engine.label_material(tmp_path / "cat_001.jpg", prompt_body="你是打标助手。")

    assert fake_completer.calls == []


def test_material_image_oversize_raises(
    temp_data_root: Path,
    tmp_path: Path,
    fake_completer: FakeCompleter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图片超出运行时护栏（导入后被换成超大文件的情形）：读取前拦截 MaterialOversizeError。"""
    monkeypatch.setattr(labeling_engine_module, "MAX_IMAGE_BYTES", 8)
    image = _make_image(tmp_path / "big.jpg", b"0123456789")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(MaterialOversizeError, match="上限"):
        engine.label_material(image, prompt_body="你是打标助手。")

    assert fake_completer.calls == []


def test_material_video_oversize_raises(
    temp_data_root: Path,
    tmp_path: Path,
    fake_completer: FakeCompleter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """视频超出运行时护栏（100 MiB 上限的运行时再验）：读取前拦截 MaterialOversizeError。"""
    monkeypatch.setattr(labeling_engine_module, "MAX_VIDEO_BYTES", 4)
    video = tmp_path / "big.mp4"
    video.write_bytes(b"0123456789")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(MaterialOversizeError, match="上限"):
        engine.label_material(video, prompt_body="你是打标助手。")

    assert fake_completer.calls == []


def test_material_blank_prompt_body_raises(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """策略快照的基础提示词为空白：ValueError 拒绝（一轮打标必须有 system 底座）。"""
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(ValueError, match="prompt_body"):
        engine.label_material(image, prompt_body="   ")

    assert fake_completer.calls == []


def test_material_unsupported_extension_raises(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """扩展名不在窄清单（素材被改名等运行时条件）：ValueError 拒绝，不读不调。"""
    stray = tmp_path / "notes.txt"
    stray.write_text("不是素材", encoding="utf-8")
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(ValueError, match="白名单"):
        engine.label_material(stray, prompt_body="你是打标助手。")

    assert fake_completer.calls == []


def test_material_llm_error_propagates_without_writes(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """模型调用失败：异常冒泡给调用方（按运行流水处置），且同样零写盘、无会话残留。"""
    image = _make_image(tmp_path / "cat.jpg")

    class FailingCompleter:
        """只会失败的假客户端（模拟端点错误）。"""

        def complete(self, messages: Sequence[Message]) -> str:
            """总是抛 LLMError。"""
            raise LLMError("端点故障")

        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            """纯素材路径不使用流式；为满足协议而给出。"""
            raise LLMError("端点故障")
            yield StreamDelta(kind="content", text="")  # pragma: no cover

    engine = LabelingEngine(FailingCompleter(), _MODEL)

    with pytest.raises(LLMError):
        engine.label_material(image, prompt_body="你是打标助手。")

    assert list_sessions() == []
