"""接口测试：cli 入口层（Typer CliRunner 跑命令、断言输出与退出码；FakeCompleter 注入离线跑）。

打标类命令（label / chat）monkeypatch cli.label.build_engine 注入假引擎；管理类命令
（config / prompt / skill / session）直连数据域、天然离线。全部用 temp_data_root 隔离数据根。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
import uvicorn
from fastapi import FastAPI
from typer.testing import CliRunner

import dataset_factory.cli.label as label_module
from dataset_factory.cli import app
from dataset_factory.llm import ImagePart, LLMTimeoutError, Message, TextPart
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


class _FlakyCompleter:
    """第一轮抛超时、之后正常回复的假客户端（测 chat 的逐轮容错）。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            raise LLMTimeoutError("模型调用超时；可重试或调大 timeout。")
        return "第二轮回复"


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


def test_label_resume_iterates_with_history(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """带 --session 续接：第二轮携带第一轮历史（迭代改写）。"""
    _save_prompt("h3", "你是打标助手。")
    completer = FakeCompleter(replies=["第一轮", "第二轮"])
    from dataset_factory.labeling import LabelingEngine

    def fake_build() -> LabelingEngine:
        return LabelingEngine(completer, "test-model")

    monkeypatch.setattr(label_module, "build_engine", fake_build)
    first = runner.invoke(app, ["label", "-p", "h3", "-m", "描述图"])
    assert first.exit_code == 0
    assert first.stdout == "第一轮\n"
    (session_id,) = list_sessions()
    second = runner.invoke(
        app, ["label", "--session", session_id, "-m", "改成一句话", "--json"]
    )

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


def test_config_list_empty(temp_data_root: Path) -> None:
    """config list 空数据根：显示引导提示（不报错）。"""
    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    assert "还没有端点配置" in result.output


def test_config_add_list_use_show_roundtrip(temp_data_root: Path) -> None:
    """add（密钥留空跳过）/ list（* 标记当前使用）/ use 切换 / show 跟随——多配置命令闭环。"""
    first = runner.invoke(
        app,
        ["config", "add", "alpha", "--base-url", "https://a/v1", "--model", "m-a"],
        input="test-key-123\n",
    )
    second = runner.invoke(
        app,
        ["config", "add", "beta", "--base-url", "https://b/v1", "--model", "m-b"],
        input="\n",
    )
    listing = runner.invoke(app, ["config", "list"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert listing.exit_code == 0
    lines = listing.output.splitlines()
    assert lines[0].startswith("* alpha")
    assert "密钥已配置" in lines[0]
    assert lines[1].startswith("  beta")
    assert "密钥未配置" in lines[1]
    # 密钥只进不出：列表输出绝不含密钥明文。
    assert "test-key-123" not in listing.output

    use = runner.invoke(app, ["config", "use", "beta"])
    after = runner.invoke(app, ["config", "list"])
    show = runner.invoke(app, ["config", "show"])

    assert use.exit_code == 0
    assert after.output.splitlines()[0].startswith("  alpha")
    assert after.output.splitlines()[1].startswith("* beta")
    assert show.exit_code == 0
    assert "beta" in show.output


def test_config_add_duplicate_fails(temp_data_root: Path) -> None:
    """重名（不区分大小写）：退出码 1，stderr 给可操作消息。"""
    runner.invoke(
        app,
        ["config", "add", "alpha", "--base-url", "https://a/v1", "--model", "m"],
        input="\n",
    )
    result = runner.invoke(
        app,
        ["config", "add", "ALPHA", "--base-url", "https://b/v1", "--model", "m"],
        input="\n",
    )

    assert result.exit_code == 1
    assert "不区分大小写" in result.stderr


def test_config_use_missing_fails(temp_data_root: Path) -> None:
    """切换到不存在的配置：退出码 1。"""
    result = runner.invoke(app, ["config", "use", "ghost"])

    assert result.exit_code == 1
    assert "不存在" in result.stderr


def test_config_remove_roundtrip_and_active_guard(temp_data_root: Path) -> None:
    """remove 删非当前配置成功；删当前使用中的配置被拒（先切换再删）。"""
    runner.invoke(
        app,
        ["config", "add", "alpha", "--base-url", "https://a/v1", "--model", "m"],
        input="\n",
    )
    runner.invoke(
        app,
        ["config", "add", "beta", "--base-url", "https://b/v1", "--model", "m"],
        input="\n",
    )

    remove_beta = runner.invoke(app, ["config", "remove", "beta", "-y"])
    listing = runner.invoke(app, ["config", "list"])
    remove_alpha = runner.invoke(app, ["config", "remove", "alpha", "-y"])

    assert remove_beta.exit_code == 0
    assert listing.output.splitlines()[0].startswith("* alpha")
    assert remove_alpha.exit_code == 1
    assert "当前使用" in remove_alpha.stderr


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
    # prompt 子命令会播种内置预置：删光自建条目后库里仍有内置的「详细描述」。
    assert "h3" not in after.output
    assert "详细描述" in after.output


def test_prompt_rename_roundtrip(temp_data_root: Path) -> None:
    """prompt rename：改名后 list / show 跟新名、旧名消失。"""
    _save_prompt("old", "你是打标助手。")

    renamed = runner.invoke(app, ["prompt", "rename", "old", "new"])
    listing = runner.invoke(app, ["prompt", "list"])
    show = runner.invoke(app, ["prompt", "show", "new"])

    assert renamed.exit_code == 0
    assert "new" in listing.output
    assert "old\t" not in listing.output
    assert show.exit_code == 0
    assert "你是打标助手。" in show.output


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


def test_label_unknown_skill_exits_user_error(
    temp_data_root: Path, fake_engine: FakeCompleter
) -> None:
    """勾选不存在的 skill：退出码 1、stderr 给可操作错误（不静默吞掉）。"""
    _save_prompt("h3", "你是打标助手。")

    result = runner.invoke(app, ["label", "-p", "h3", "-s", "不存在", "-m", "描述"])

    assert result.exit_code == 1
    assert "不在 skill 库" in result.stderr


def test_chat_turn_failure_keeps_session_alive(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chat 某一轮失败（模型超时）：报错带重试提示后继续会话，下一轮照常进行。"""
    from dataset_factory.labeling import LabelingEngine

    _save_prompt("h3", "你是打标助手。")
    completer = _FlakyCompleter()

    def fake_build() -> LabelingEngine:
        return LabelingEngine(completer, "test-model")

    monkeypatch.setattr(label_module, "build_engine", fake_build)

    result = runner.invoke(app, ["chat", "-p", "h3"], input="第一轮\n第二轮\n")

    assert result.exit_code == 0
    assert "错误：模型调用超时" in result.stderr
    assert "重发本轮" in result.stderr
    assert "第二轮回复" in result.output
    assert completer.calls == 2


def test_serve_wires_uvicorn_without_access_log(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """serve：显式构造 uvicorn.Server（不覆盖应用日志、访问日志交中间件；实例挂 app.state）。"""
    captured: dict[str, object] = {}

    class FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("uvicorn.Server", FakeServer)
    root = logging.getLogger()
    saved_handlers = root.handlers[:]

    try:
        result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8123"])
        file_handlers = [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
    finally:
        # serve 会往 root 挂文件日志 handler（指向临时数据根），测试后还原避免遗留。
        root.handlers[:] = saved_handlers

    assert result.exit_code == 0
    config = cast(uvicorn.Config, captured["config"])
    assert isinstance(config.app, FastAPI)
    assert config.host == "127.0.0.1"
    assert config.port == 8123
    assert config.log_config is None
    assert config.access_log is False
    assert captured["ran"] is True
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename.endswith("server.log")


def test_serve_log_level_reconfigures_logging(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """serve --log-level：显式值生效；无法识别的值回落 INFO（不崩、可启动）。"""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level

    class FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            return None

        def run(self) -> None:
            return None

    monkeypatch.setattr("uvicorn.Server", FakeServer)

    try:
        result = runner.invoke(app, ["serve", "--log-level", "warning"])
        assert result.exit_code == 0
        assert root.level == logging.WARNING

        result_bad = runner.invoke(app, ["serve", "--log-level", "不是级别"])
        assert result_bad.exit_code == 0
        assert root.level == logging.INFO
    finally:
        # serve 内部 basicConfig(force=True) 会把 handler 绑到 CliRunner 的临时 stderr，
        # 测试后还原 root 配置，避免遗留指向已关流的 handler 污染后续测试的日志输出。
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
