"""接口测试：cli 入口层（Typer CliRunner 跑命令、断言输出与退出码；FakeCompleter 注入离线跑）。

打标类命令（label / chat）monkeypatch cli.label.build_engine 注入假引擎；管理类命令
（config / prompt / skill / session）直连数据域、天然离线。全部用 temp_data_root 隔离数据根。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import dataset_factory.cli.label as label_module
from dataset_factory.cli import app
from dataset_factory.llm import ImagePart, TextPart
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.sessions import list_sessions
from dataset_factory.skills import import_skill

from .conftest import FakeCompleter

_SKILL_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
runner = CliRunner()


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeCompleter:
    """把 CLI 的引擎装配换成假客户端版（离线、记录每轮消息）。"""
    completer = FakeCompleter()
    from dataset_factory.labeling import LabelingEngine

    monkeypatch.setattr(
        label_module,
        "build_engine",
        lambda: LabelingEngine(completer, "test-model"),
    )
    return completer


def _save_prompt(name: str, body: str) -> None:
    """往提示词库存一条测试提示词。"""
    save_prompt(Prompt(name=name, description="测试提示词", body=body))


def test_label_outputs_caption_and_session_hint(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """首轮 label：stdout 只有 caption（供外部 agent 解析），stderr 提示会话 id 续接。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["label", "-p", "h3", "-m", "打标"])

    assert result.exit_code == 0
    assert result.stdout == "打标结果\n"
    (session_id,) = list_sessions()
    assert session_id in (result.stderr or result.output)


def test_label_json_mode(temp_data_root: Path, fake_engine: FakeCompleter) -> None:
    """--json：stdout 输出 {session_id, caption} 结构化 JSON。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["label", "-p", "h3", "-m", "打标", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    (session_id,) = list_sessions()
    assert payload == {"session_id": session_id, "caption": "打标结果"}


def test_label_resume_iterates_with_history(temp_data_root: Path) -> None:
    """带 --session 续接：第二轮携带第一轮历史（迭代改写）。"""
    _save_prompt("h3", "你是打标助手。")
    completer = FakeCompleter(replies=["第一轮", "第二轮"])
    from dataset_factory.labeling import LabelingEngine

    original = label_module.build_engine
    label_module.build_engine = lambda: LabelingEngine(completer, "test-model")
    try:
        first = runner.invoke(app, ["label", "-p", "h3", "-m", "描述图"])
        assert first.exit_code == 0
        assert first.stdout == "第一轮\n"
        (session_id,) = list_sessions()
        second = runner.invoke(
            app, ["label", "--session", session_id, "-m", "改成一句话", "--json"]
        )
    finally:
        label_module.build_engine = original

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(second.stdout)["caption"] == "第二轮"
    assert len(completer.calls) == 2
    _, history_user, history_assistant, _ = completer.calls[1]
    assert history_user.parts == (TextPart("描述图"),)
    assert history_assistant.parts == (TextPart("第一轮"),)


def test_label_with_image_and_skill(
    temp_data_root: Path, tmp_path: Path, fake_engine: FakeCompleter
) -> None:
    """label 带图与 skill：CLI 参数正确传到引擎（user 消息含图片块与 skill 包裹文本）。"""
    _save_prompt("h3", "你是打标助手。")
    skill_name = import_skill(_SKILL_PACK).skill.name
    image = tmp_path / "cat.jpg"
    image.write_bytes(b"png!")

    result = runner.invoke(
        app,
        ["label", "-p", "h3", "-s", skill_name, "-i", str(image), "-m", "描述"],
    )

    assert result.exit_code == 0
    user = fake_engine.calls[0][1]
    assert any(isinstance(part, ImagePart) for part in user.parts)
    assert any(
        isinstance(part, TextPart) and part.text.startswith("<skill>")
        for part in user.parts
    )


def test_label_missing_prompt_exits_user_error(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """基础提示词不存在：退出码 1、stderr 给可操作错误。"""
    result = runner.invoke(app, ["label", "-p", "不存在", "-m", "打标"])

    assert result.exit_code == 1
    assert "不存在" in result.stderr


def test_label_empty_turn_exits_user_error(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """无指令无图：退出码 1（EmptyTurnError 的可操作消息）。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["label", "-p", "h3"])

    assert result.exit_code == 1
    assert "内容" in result.stderr


def test_label_missing_config_exits_user_error(
    temp_data_root: Path,
) -> None:
    """未配置端点就打标：退出码 1、stderr 提示先 dsf config set（不甩栈）。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["label", "-p", "h3", "-m", "打标"])

    assert result.exit_code == 1
    assert "dsf config set" in result.stderr


def test_chat_rounds_and_exit_on_eof(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """chat 新会话：两轮交互后 EOF 干净退出（exit 0），回复打印到 stdout。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["chat", "-p", "h3"], input="第一轮\n第二轮\n")

    assert result.exit_code == 0
    assert "打标结果" in result.output
    assert len(fake_engine.calls) == 2
    (session_id,) = list_sessions()
    assert session_id is not None


def test_chat_resumes_latest_session(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """chat 不带 --session：自动恢复最新会话（先 label 开一轮，chat 续上同一会话）。"""
    _save_prompt("h3", "你是打标助手。")
    first = runner.invoke(app, ["label", "-p", "h3", "-m", "首轮"])
    session_id = list_sessions()[0]
    result = runner.invoke(app, ["chat"], input="继续改写\n")

    assert first.exit_code == 0
    assert result.exit_code == 0
    assert f"恢复会话 {session_id}" in result.output
    assert list_sessions() == [session_id]
    assert len(fake_engine.calls) == 2


def test_chat_at_image_syntax(
    temp_data_root: Path, tmp_path: Path, fake_engine: FakeCompleter
) -> None:
    """chat 的 @图片路径 语法：输入行解析出图片 + 指令。"""
    _save_prompt("h3", "你是打标助手。")
    image = tmp_path / "cat.jpg"
    image.write_bytes(b"png!")

    result = runner.invoke(app, ["chat", "-p", "h3"], input=f"@{image} 描述这张图\n")

    assert result.exit_code == 0
    user = fake_engine.calls[0][1]
    assert any(isinstance(part, ImagePart) for part in user.parts)
    assert any(
        part.text == "描述这张图" for part in user.parts if isinstance(part, TextPart)
    )


def test_config_set_and_show(temp_data_root: Path) -> None:
    """config set：密钥交互输入不回显落盘；show 显示配置与密钥来源（不显内容）。"""
    result_set = runner.invoke(
        app,
        ["config", "set", "--base-url", "https://api.example.com/v1", "--model", "m1"],
        input="test-key-123\n",
    )
    result_show = runner.invoke(app, ["config", "show"])

    assert result_set.exit_code == 0
    assert result_show.exit_code == 0
    assert "https://api.example.com/v1" in result_show.output
    assert "m1" in result_show.output
    assert "credentials 文件" in result_show.output
    assert "test-key-123" not in result_show.output


def test_config_show_empty(temp_data_root: Path) -> None:
    """config show 空配置：显示未配置提示（不报错）。"""
    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0
    assert "未配置" in result.output


def test_prompt_lifecycle(temp_data_root: Path, tmp_path: Path) -> None:
    """prompt save（--file）/ list / show / rm 全生命周期。"""
    body_file = tmp_path / "body.md"
    body_file.write_text("你是打标助手。", encoding="utf-8")

    save = runner.invoke(
        app, ["prompt", "save", "h3", "-d", "视频打标", "-f", str(body_file)]
    )
    listing = runner.invoke(app, ["prompt", "list"])
    show = runner.invoke(app, ["prompt", "show", "h3"])
    remove = runner.invoke(app, ["prompt", "rm", "h3", "-y"])
    after = runner.invoke(app, ["prompt", "list"])

    assert save.exit_code == 0
    assert listing.exit_code == 0
    assert "h3" in listing.output
    assert "视频打标" in listing.output
    assert show.exit_code == 0
    assert "你是打标助手。" in show.output
    assert remove.exit_code == 0
    assert after.exit_code == 0
    assert "为空" in after.output


def test_prompt_rm_aborts_without_confirm(temp_data_root: Path) -> None:
    """prompt rm 不确认：中止（退出码非 0、提示词仍在）。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["prompt", "rm", "h3"], input="n\n")

    assert result.exit_code != 0
    listing = runner.invoke(app, ["prompt", "list"])
    assert "h3" in listing.output


def test_skill_lifecycle(temp_data_root: Path) -> None:
    """skill import / list / disable / enable / rm 全生命周期。"""
    imported = runner.invoke(app, ["skill", "import", str(_SKILL_PACK)])
    listing = runner.invoke(app, ["skill", "list"])
    disable = runner.invoke(app, ["skill", "disable", "example-caption-skill"])
    listing_disabled = runner.invoke(app, ["skill", "list"])
    enable = runner.invoke(app, ["skill", "enable", "example-caption-skill"])
    remove = runner.invoke(app, ["skill", "rm", "example-caption-skill", "-y"])
    after = runner.invoke(app, ["skill", "list"])

    assert imported.exit_code == 0
    assert "example-caption-skill" in imported.output
    assert listing.exit_code == 0
    assert "[启用] example-caption-skill" in listing.output
    assert disable.exit_code == 0
    assert "[停用] example-caption-skill" in listing_disabled.output
    assert enable.exit_code == 0
    assert remove.exit_code == 0
    assert "为空" in after.output


def test_session_list_and_show(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """session list / show：label 一轮后可列出、回放对话历史。"""
    _save_prompt("h3", "你是打标助手。")
    runner.invoke(app, ["label", "-p", "h3", "-m", "描述图"])
    (session_id,) = list_sessions()

    listing = runner.invoke(app, ["session", "list"])
    show = runner.invoke(app, ["session", "show", session_id])

    assert listing.exit_code == 0
    assert session_id in listing.output
    assert show.exit_code == 0
    assert "user: 描述图" in show.output
    assert "assistant: 打标结果" in show.output


def test_usage_error_exit_code(temp_data_root: Path) -> None:
    """用法错误（未知子命令）：退出码 2（Typer/click 默认用法错误语义）。"""
    result = runner.invoke(app, ["不存在的命令"])

    assert result.exit_code == 2
