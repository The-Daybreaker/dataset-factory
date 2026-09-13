"""集成 + 端到端测试：labeling 编排引擎（组装一轮打标、设置切换、历史回放、信封落盘、恢复）。

集成测试编排 prompts + skills + sessions + llm 四模块（fake_completer 断言「引擎拼了什么」，
不真调 API）；端到端测试整条打标流程（建提示词 → 导入 skill → 发图打标 → 恢复会话）。
全部离线、用 temp_data_root fixture 隔离数据根。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from dataset_factory.labeling import (
    AttachmentReadError,
    EmptyTurnError,
    LabelingEngine,
    PromptNotSelectedError,
    SessionSettings,
    SettingsFormatError,
    StreamFinished,
    StreamStarted,
)
from dataset_factory.llm import (
    ImagePart,
    LLMError,
    Message,
    StreamDelta,
    TextPart,
    VideoPart,
)
from dataset_factory.prompts import Prompt, PromptNotFoundError, save_prompt
from dataset_factory.sessions import (
    EnvelopeEvent,
    MessageEvent,
    SessionEvent,
    SessionNotFoundError,
    SettingsEvent,
    append_settings,
    create_session,
    list_sessions,
    read_events,
)
from dataset_factory.skills import SkillNotFoundError, import_skill, set_enabled

from .conftest import FakeCompleter

_SKILL_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
_MODEL = "test-model"


def _save_prompt(name: str, body: str) -> None:
    """往提示词库存一条测试提示词。"""
    save_prompt(Prompt(name=name, description="测试提示词", body=body))


def _import_skill() -> str:
    """导入 fixture 的示例 skill，返回其名称。"""
    return import_skill(_SKILL_PACK).skill.name


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
    skill_full = (_SKILL_PACK / "SKILL.md").read_text(encoding="utf-8")
    image = _make_image(tmp_path / "cat.jpg", b"png!")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(
        prompt_name="h3",
        skill_names=[skill_name],
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

    engine.label(prompt_name="h3", instruction="纯文本打标")

    user = fake_completer.calls[0][1]
    assert user.parts == (TextPart("纯文本打标"),)


def test_image_only_turn_allows_empty_instruction(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """纯图轮（指令为空、任务说明在基础提示词里）：user 消息只含图片块。"""
    _save_prompt("h3", "你是打标助手。")
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    engine.label(prompt_name="h3", instruction="", image=image)

    assert fake_completer.calls[0][1].parts == (ImagePart(b"fake-png-bytes"),)


def test_video_turn_sends_video_part(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """视频轮：VideoPart 进当轮 user 消息（fps / 帧上限随附件一并下发）。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    engine.label(
        prompt_name="h3",
        instruction="描述这个动作",
        video_bytes=b"fake-mp4-bytes",
        video_name="clip.mp4",
        video_fps=3.0,
        video_max_frames=8,
    )

    assert fake_completer.calls[0][1].parts == (
        TextPart("描述这个动作"),
        VideoPart(b"fake-mp4-bytes", fps=3.0, max_frames=8),
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
            prompt_name="h3",
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

    events = list(engine.label_stream(prompt_name="h3", instruction="描述它"))

    assert isinstance(events[0], StreamStarted)
    deltas = [event for event in events if isinstance(event, StreamDelta)]
    assert "".join(delta.text for delta in deltas) == "打标结果"
    finished = events[-1]
    assert isinstance(finished, StreamFinished)
    assert finished.result.caption == "打标结果"
    snapshot = engine.restore(finished.result.session_id)
    assert snapshot.messages[-1].role == "assistant"
    assert snapshot.messages[-1].text == "打标结果"


def test_events_recorded_in_order(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """事件落盘顺序：settings → message(user) → envelope → message(assistant)。"""
    _save_prompt("h3", "你是打标助手。")
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(prompt_name="h3", instruction="打标", image=image)

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
    skill_full = (_SKILL_PACK / "SKILL.md").read_text(encoding="utf-8")
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    result = engine.label(
        prompt_name="h3",
        skill_names=[skill_name],
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


def test_second_turn_replays_history_with_placeholder_image(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """第二轮迭代改写：历史按原角色回放、历史图片降为占位文本、当轮重新注入 skill。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = _import_skill()
    skill_full = (_SKILL_PACK / "SKILL.md").read_text(encoding="utf-8")
    image = _make_image(tmp_path / "cat.jpg")
    completer = FakeCompleter(replies=["第一轮回复", "第二轮回复"])
    engine = LabelingEngine(completer, _MODEL)

    first = engine.label(
        prompt_name="h3",
        skill_names=[skill_name],
        instruction="描述这张图",
        image=image,
    )
    second = engine.label(session_id=first.session_id, instruction="改成一句话")

    assert second.caption == "第二轮回复"
    assert second.session_id == first.session_id
    assert len(completer.calls) == 2
    system, history_user, history_assistant, current_user = completer.calls[1]
    assert system.parts == (TextPart("你是打标助手。"),)
    assert history_user.parts == (TextPart("描述这张图"), TextPart("[图片: cat.jpg]"))
    assert history_assistant.parts == (TextPart("第一轮回复"),)
    assert current_user.parts == (
        TextPart(f"<skill>\n{skill_full}\n</skill>"),
        TextPart("改成一句话"),
    )


def test_settings_change_mid_session_appends_event(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """中途切换基础提示词与 skill 清单：追加新 settings 事件，下轮用新值组装。"""
    _save_prompt("旧提示词", "旧底座。")
    _save_prompt("新提示词", "新底座。")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(prompt_name="旧提示词", instruction="第一轮")
    second = engine.label(
        session_id=first.session_id,
        prompt_name="新提示词",
        skill_names=[],
        instruction="第二轮",
    )

    settings_events = [
        event
        for event in read_events(second.session_id)
        if isinstance(event, SettingsEvent)
    ]
    assert [event.settings for event in settings_events] == [
        {"prompt": "旧提示词", "skills": []},
        {"prompt": "新提示词", "skills": []},
    ]
    assert fake_completer.calls[1][0].parts == (TextPart("新底座。"),)


def test_settings_unchanged_appends_no_event(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """设置未变（显式传相同值）：不追加多余的 settings 事件。"""
    _save_prompt("h3", "你是打标助手。")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(prompt_name="h3", instruction="第一轮")
    engine.label(session_id=first.session_id, prompt_name="h3", instruction="第二轮")

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

    result = engine.label(
        prompt_name="h3", skill_names=[skill_name], instruction="打标"
    )

    assert fake_completer.calls[0][1].parts == (TextPart("打标"),)
    settings_event = next(
        event
        for event in read_events(result.session_id)
        if isinstance(event, SettingsEvent)
    )
    assert settings_event.settings == {"prompt": "h3", "skills": [skill_name]}


def test_missing_prompt_fails_loud(
    temp_data_root: Path, fake_completer: FakeCompleter
) -> None:
    """基础提示词在库中不存在：PromptNotFoundError 冒泡（可操作报错，fail loud）。"""
    engine = LabelingEngine(fake_completer, _MODEL)

    with pytest.raises(PromptNotFoundError):
        engine.label(prompt_name="不存在的提示词", instruction="打标")


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
        engine.label(prompt_name="h3", instruction="   ")


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
        engine.label(prompt_name="h3", instruction="打标")

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
        engine.label(prompt_name="h3", instruction="打标", image=image)


def test_restore_returns_settings_and_history(
    temp_data_root: Path, tmp_path: Path, fake_completer: FakeCompleter
) -> None:
    """恢复会话：当前设置 + 按时间序的 user / assistant 历史消息（含附件名）。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = _import_skill()
    image = _make_image(tmp_path / "cat.jpg")
    engine = LabelingEngine(fake_completer, _MODEL)

    first = engine.label(
        prompt_name="h3",
        skill_names=[skill_name],
        instruction="描述这张图",
        image=image,
    )
    engine.label(session_id=first.session_id, instruction="改成一句话")

    snapshot = engine.restore(first.session_id)

    assert snapshot.session_id == first.session_id
    assert snapshot.settings == SessionSettings(
        prompt_name="h3", skill_names=(skill_name,)
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
        prompt_name="h3-video",
        skill_names=[skill_name],
        instruction="给这张图打个标",
        image=image,
    )
    second = engine.label(session_id=first.session_id, instruction="改写成两句话")
    snapshot = engine.restore(second.session_id)

    assert first.caption == "打标结果"
    assert second.caption == "打标结果"
    assert snapshot.settings == SessionSettings(
        prompt_name="h3-video", skill_names=(skill_name,)
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
    first = engine.label(prompt_name="h3", instruction="第一轮")

    with pytest.raises(PromptNotFoundError):
        engine.label(
            session_id=first.session_id, prompt_name="不存在", instruction="改"
        )

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
        engine.label(prompt_name="h3", skill_names=["拼错了"], instruction="打标")

    assert list_sessions() == []
