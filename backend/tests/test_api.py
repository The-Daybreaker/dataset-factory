"""接口测试：api 入口层（FastAPI TestClient 全端点 + 错误映射 + frontend 托管）。

打标端点 monkeypatch api.routes_labeling.build_engine 注入假客户端（离线）；frontend
托管用临时目录（测试确定性，不依赖真实 frontend 是否已建）。
"""

from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dataset_factory.api.routes_endpoints as routes_endpoints
import dataset_factory.api.routes_labeling as routes_labeling
from dataset_factory.api import create_app
from dataset_factory.labeling import LabelingEngine
from dataset_factory.llm import (
    EndpointConfig,
    ImagePart,
    LLMError,
    Message,
    ProbeResult,
    StreamDelta,
    TextPart,
    VideoPart,
    read_stored_api_key,
)
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.sessions import list_sessions

from .conftest import FakeCompleter

_SKILL_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
# 最小合法 PNG（magic bytes 开头即可，FakeCompleter 不做图片校验）。
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def client(tmp_path: Path, temp_data_root: Path) -> TestClient:
    """挂临时空 frontend 目录的测试客户端（依赖 temp_data_root 隔离数据根，绝碰真实目录）。"""
    return TestClient(create_app(frontend_dir=tmp_path))


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeCompleter:
    """把 api 的引擎装配换成假客户端版（离线、记录每轮消息）。"""
    completer = FakeCompleter()
    from dataset_factory.labeling import LabelingEngine

    monkeypatch.setattr(
        routes_labeling,
        "build_engine",
        lambda: LabelingEngine(completer, "test-model"),
    )
    return completer


def _save_prompt(name: str, body: str) -> None:
    """往提示词库存一条测试提示词。"""
    save_prompt(Prompt(name=name, description="测试提示词", body=body))


def test_label_first_turn(client: TestClient, fake_engine: FakeCompleter) -> None:
    """首轮打标：200 返回 {session_id, caption}。"""
    _save_prompt("h3", "你是打标助手。")

    response = client.post(
        "/api/label", json={"prompt_name": "h3", "instruction": "打标"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["caption"] == "打标结果"
    assert body["session_id"] == list_sessions()[0]


def test_label_with_data_url_image(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """带图打标：data URL 前缀被剥掉、字节进引擎（user 消息含 ImagePart）。"""
    _save_prompt("h3", "你是打标助手。")
    data_url = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")

    response = client.post(
        "/api/label",
        json={
            "prompt_name": "h3",
            "instruction": "描述",
            "image_base64": data_url,
            "image_name": "cat.png",
        },
    )

    assert response.status_code == 200
    user = fake_engine.calls[0][1]
    assert any(part == ImagePart(_PNG_BYTES) for part in user.parts)
    assert list_sessions()[0]  # 会话已建


def test_session_attachment_serves_media_content_type(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """会话附件端点按扩展名显式给 Content-Type（PRD-0004）：<img>/<video> 内联渲染靠它。"""
    _save_prompt("h3", "你是打标助手。")
    data_url = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")
    created = client.post(
        "/api/label",
        json={
            "prompt_name": "h3",
            "instruction": "描述",
            "image_base64": data_url,
            "image_name": "cat.png",
        },
    )
    session_id = created.json()["session_id"]
    video = client.post(
        "/api/label",
        json={
            "session_id": session_id,
            "instruction": "看视频",
            "video_base64": "ZmFrZS1tcDQtYnl0ZXM=",
            "video_name": "clip.mp4",
        },
    )
    assert video.status_code == 200

    image = client.get(f"/api/sessions/{session_id}/attachments/cat.png")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert "attachment" not in image.headers.get("content-disposition", "")
    clip = client.get(f"/api/sessions/{session_id}/attachments/clip.mp4")
    assert clip.status_code == 200
    assert clip.headers["content-type"] == "video/mp4"


def test_label_resume_iterates(client: TestClient, fake_engine: FakeCompleter) -> None:
    """带 session_id 续接：第二轮带历史（迭代改写）。"""
    _save_prompt("h3", "你是打标助手。")
    first = client.post(
        "/api/label", json={"prompt_name": "h3", "instruction": "第一轮"}
    )
    session_id = first.json()["session_id"]

    second = client.post(
        "/api/label", json={"session_id": session_id, "instruction": "改成一句话"}
    )

    assert second.status_code == 200
    assert len(fake_engine.calls) == 2
    assert len(list_sessions()) == 1


def test_label_empty_turn_is_400(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """无指令无图：400 + 可操作错误。"""
    _save_prompt("h3", "你是打标助手。")

    response = client.post("/api/label", json={"prompt_name": "h3"})

    assert response.status_code == 400
    assert "内容" in response.json()["detail"]


def test_label_bad_base64_is_400(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """base64 不合法：400（输入翻译在入口层）。"""
    _save_prompt("h3", "你是打标助手。")

    response = client.post(
        "/api/label",
        json={"prompt_name": "h3", "instruction": "x", "image_base64": "!!!not-base64"},
    )

    assert response.status_code == 400


def test_label_unknown_prompt_is_404(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """提示词不存在：404。"""
    response = client.post(
        "/api/label", json={"prompt_name": "不存在", "instruction": "x"}
    )

    assert response.status_code == 404


def test_label_missing_config_is_400(client: TestClient) -> None:
    """未配置端点：400（ConfigError 映射，提示先配置；不走假引擎——ConfigError 来自真实装配）。"""
    _save_prompt("h3", "你是打标助手。")

    response = client.post("/api/label", json={"prompt_name": "h3", "instruction": "x"})

    assert response.status_code == 400
    assert "config" in response.json()["detail"]


def test_label_validation_error_is_422(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """请求字段类型错（skill_names 传字符串）：pydantic 自动 422。"""
    response = client.post(
        "/api/label", json={"prompt_name": "h3", "skill_names": "不是列表"}
    )

    assert response.status_code == 422


def test_sessions_latest_404_when_empty(client: TestClient) -> None:
    """还没有任何会话：GET /api/sessions/latest 404。"""
    response = client.get("/api/sessions/latest")

    assert response.status_code == 404


def test_sessions_latest_and_get_by_id(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """打一轮后：latest 与按 id 查询都返回快照（设置 + 历史）。"""
    _save_prompt("h3", "你是打标助手。")
    label = client.post(
        "/api/label", json={"prompt_name": "h3", "instruction": "描述图"}
    )
    session_id = label.json()["session_id"]

    latest = client.get("/api/sessions/latest")
    by_id = client.get(f"/api/sessions/{session_id}")

    assert latest.status_code == 200
    assert latest.json()["settings"] == {"prompt_name": "h3", "skill_names": []}
    assert latest.json()["messages"][0]["text"] == "描述图"
    assert by_id.status_code == 200
    assert by_id.json()["session_id"] == session_id


def test_sessions_unknown_is_404(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """会话 id 不存在：404。"""
    response = client.get("/api/sessions/20990101-000000-000000")

    assert response.status_code == 404


def test_prompts_crud(client: TestClient) -> None:
    """提示词 CRUD：put 新建 → list/get → put 覆盖 → delete → 404。"""
    put = client.put(
        "/api/prompts/h3", json={"description": "视频打标", "body": "你是打标助手。"}
    )
    listing = client.get("/api/prompts")
    full = client.get("/api/prompts/h3")
    put_again = client.put(
        "/api/prompts/h3", json={"description": "改", "body": "新版正文"}
    )
    full_again = client.get("/api/prompts/h3")
    removed = client.delete("/api/prompts/h3")
    missing = client.get("/api/prompts/h3")

    assert put.status_code == 204
    assert listing.status_code == 200
    assert listing.json() == [{"name": "h3", "description": "视频打标"}]
    assert full.json()["body"] == "你是打标助手。"
    assert put_again.status_code == 204
    assert full_again.json()["body"] == "新版正文"
    assert removed.status_code == 204
    assert missing.status_code == 404


def test_prompts_rename(client: TestClient) -> None:
    """rename：204 且旧名 404、新名可读；撞名 409；源不存在 404。"""
    client.put("/api/prompts/old", json={"description": "d", "body": "正文"})
    client.put("/api/prompts/other", json={"description": "", "body": "x"})

    renamed = client.post("/api/prompts/old/rename", json={"new_name": "new"})
    old_gone = client.get("/api/prompts/old")
    new_full = client.get("/api/prompts/new")
    conflict = client.post("/api/prompts/new/rename", json={"new_name": "other"})
    missing = client.post("/api/prompts/ghost/rename", json={"new_name": "z"})

    assert renamed.status_code == 204
    assert old_gone.status_code == 404
    assert new_full.status_code == 200
    assert new_full.json()["body"] == "正文"
    assert conflict.status_code == 409
    assert missing.status_code == 404


def test_prompts_invalid_name_is_400(client: TestClient) -> None:
    """名称含非法字符（Windows 禁字符 :）：400。"""
    response = client.put(
        "/api/prompts/bad%3Aname", json={"description": "", "body": "x"}
    )

    assert response.status_code == 400


def test_skill_save_roundtrip_and_conflict(client: TestClient) -> None:
    """HTTP 写回同步描述、正文与列表，并拒绝迟到覆盖。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})
    url = "/api/skills/example-caption-skill/files/SKILL.md"
    original = client.get(url).json()["content"]
    payload = {
        "content": original + "\n新的正文要求\n",
        "original_content": original,
        "description": "新的描述",
    }

    saved = client.put(url, json=payload)
    readback = client.get(url)
    stale = client.put(url, json=payload)

    assert saved.status_code == 200
    assert saved.json() == readback.json()
    assert "新的正文要求" in readback.json()["content"]
    assert client.get("/api/skills").json()[0]["description"] == "新的描述"
    assert stale.status_code == 409
    assert client.get(url).json() == readback.json()


def test_skill_save_requires_original_content(client: TestClient) -> None:
    """缺少编辑基线的请求在边界被拒绝。"""
    response = client.put(
        "/api/skills/example-caption-skill/files/SKILL.md", json={"content": "text"}
    )

    assert response.status_code == 422


def test_skills_lifecycle(client: TestClient) -> None:
    """skill 全生命周期：import → list → disable/enable → rm → 404。"""
    imported = client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})
    listing = client.get("/api/skills")
    disabled = client.post("/api/skills/example-caption-skill/disable")
    listing_disabled = client.get("/api/skills")
    enabled = client.post("/api/skills/example-caption-skill/enable")
    removed = client.delete("/api/skills/example-caption-skill")
    missing = client.delete("/api/skills/example-caption-skill")

    assert imported.status_code == 200
    assert imported.json()["name"] == "example-caption-skill"
    assert imported.json()["enabled"] is True
    assert listing.json()[0]["enabled"] is True
    assert listing.json()[0]["body_chars"] > 0
    assert disabled.status_code == 204
    assert listing_disabled.json()[0]["enabled"] is False
    assert enabled.status_code == 204
    assert removed.status_code == 204
    assert missing.status_code == 404


def test_skills_list_degrades_corrupt_package(
    client: TestClient, temp_data_root: Path
) -> None:
    """列表对损坏包降级呈现：200 + 「文件损坏：…」条目，好包不受影响（A8 回归）。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})
    bad_dir = temp_data_root / "skills" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\ndescription: x\n没有闭合", encoding="utf-8")

    response = client.get("/api/skills")

    assert response.status_code == 200
    items = {item["name"]: item for item in response.json()}
    assert items["bad"]["description"].startswith("文件损坏：")
    assert items["bad"]["body_chars"] == 0
    assert items["example-caption-skill"]["body_chars"] > 0


def test_skill_rename_roundtrip_and_conflict(client: TestClient) -> None:
    """改名：204 + 列表出现新名（frontmatter 同步）；撞名 409、旧名 404。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    renamed = client.post(
        "/api/skills/example-caption-skill/rename", json={"new_name": "renamed-skill"}
    )
    assert renamed.status_code == 204
    names = {item["name"] for item in client.get("/api/skills").json()}
    assert names == {"renamed-skill"}

    conflict = client.post(
        "/api/skills/renamed-skill/rename", json={"new_name": "renamed-skill"}
    )
    assert conflict.status_code == 204  # 同名 = 无操作，不算错误

    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})
    dup = client.post(
        "/api/skills/renamed-skill/rename",
        json={"new_name": "example-caption-skill"},
    )
    assert dup.status_code == 409

    missing = client.post("/api/skills/ghost/rename", json={"new_name": "whatever"})
    assert missing.status_code == 404


def test_skills_import_conflict_is_409(client: TestClient) -> None:
    """重复导入同名 skill：409（重名不合并）。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    response = client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    assert response.status_code == 409


def test_skills_import_bad_path_is_400(client: TestClient) -> None:
    """导入路径不存在：400 且带可操作消息（用户填错路径，不是系统错）。"""
    response = client.post("/api/skills/import", json={"path": "Z:/不存在/skill"})

    assert response.status_code == 400
    assert "路径不存在" in response.json()["detail"]


def test_skills_import_single_file(client: TestClient, tmp_path: Path) -> None:
    """路径指向单个 .md 文件：按 SKILL.md 单文件导入（名称取自 frontmatter）。"""
    source = tmp_path / "my-skill.md"
    source.write_bytes(
        "---\nname: single-file-skill\ndescription: 单文件\n---\n\n# S\n".encode()
    )

    response = client.post("/api/skills/import", json={"path": str(source)})

    assert response.status_code == 200
    assert response.json()["name"] == "single-file-skill"
    listing = client.get("/api/skills/single-file-skill/files").json()
    assert [item["path"] for item in listing["files"]] == ["SKILL.md"]


def test_config_get_empty(client: TestClient) -> None:
    """空配置：name/base_url/model 为 null、api_key_configured=false。"""
    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "name": None,
        "base_url": None,
        "model": None,
        "api_key_configured": False,
        "key_source": None,
    }


def test_config_update_and_get(client: TestClient) -> None:
    """更新配置（带密钥）：落盘；GET 确认密钥只报来源、绝不回内容。"""
    update = client.put(
        "/api/config",
        json={
            "base_url": "https://api.example.com/v1",
            "model": "m1",
            "api_key": "test-key-123",  # pragma: allowlist secret —— 测试假密钥
        },
    )
    get = client.get("/api/config")

    assert update.status_code == 204
    body = get.json()
    # 空数据根上 PUT：创建 default 配置并设为当前使用。
    assert body["name"] == "default"
    assert body["base_url"] == "https://api.example.com/v1"
    assert body["model"] == "m1"
    assert body["api_key_configured"] is True
    assert body["key_source"] == "file"
    assert "test-key-123" not in get.text


def test_config_update_without_key_reuses_stored(client: TestClient) -> None:
    """更新配置不带密钥：沿用已存密钥（改 base_url 不必重输）。"""
    client.put(
        "/api/config",
        json={
            "base_url": "https://old/v1",
            "model": "m1",
            "api_key": "stored-key",  # pragma: allowlist secret —— 测试假密钥
        },
    )

    update = client.put(
        "/api/config", json={"base_url": "https://new/v1", "model": "m2"}
    )

    assert update.status_code == 204
    body = client.get("/api/config").json()
    assert body["base_url"] == "https://new/v1"
    assert body["api_key_configured"] is True


def test_config_update_without_key_when_none_is_400(client: TestClient) -> None:
    """未配置过密钥又不带密钥更新：400（提示填写 api_key）。"""
    response = client.put(
        "/api/config", json={"base_url": "https://api/v1", "model": "m1"}
    )

    assert response.status_code == 400
    assert "api_key" in response.json()["detail"]


def test_endpoints_list_empty(client: TestClient) -> None:
    """空数据根：端点配置列表为空数组。"""
    response = client.get("/api/endpoints")

    assert response.status_code == 200
    assert response.json() == []


def test_endpoints_create_and_list(client: TestClient) -> None:
    """创建一套配置：201 返回概要（密钥只报有无）；第一套自动成为当前使用。"""
    response = client.post(
        "/api/endpoints",
        json={
            "name": "siliconflow",
            "base_url": "https://api.example.com/v1",
            "model": "m1",
            "api_key": "test-key-123",  # pragma: allowlist secret —— 测试假密钥
        },
    )
    listing = client.get("/api/endpoints")
    current = client.get("/api/config")

    assert response.status_code == 201
    assert response.json() == {
        "name": "siliconflow",
        "base_url": "https://api.example.com/v1",
        "model": "m1",
        "api_format": "openai-chat-completions",
        "has_api_key": True,
        "is_active": True,
        "request_params": {
            "temperature": None,
            "top_p": None,
            "max_tokens": None,
            "enable_thinking": None,
            "extra_body": None,
            "timeout_seconds": None,
            "max_retries": None,
        },
    }
    assert [item["name"] for item in listing.json()] == ["siliconflow"]
    assert current.json()["name"] == "siliconflow"


def test_endpoints_create_duplicate_conflict_409(client: TestClient) -> None:
    """重名（不区分大小写）：409 冲突。"""
    client.post(
        "/api/endpoints",
        json={"name": "Alpha", "base_url": "https://a/v1", "model": "m"},
    )
    response = client.post(
        "/api/endpoints",
        json={"name": "alpha", "base_url": "https://b/v1", "model": "m"},
    )

    assert response.status_code == 409


def test_endpoints_create_invalid_name_400(client: TestClient) -> None:
    """非法名称（含路径分隔符）：400。"""
    response = client.post(
        "/api/endpoints",
        json={"name": "a/b", "base_url": "https://a/v1", "model": "m"},
    )

    assert response.status_code == 400


def test_endpoints_create_unsupported_format_400(client: TestClient) -> None:
    """API 格式不支持：400，消息说明当前仅支持什么。"""
    response = client.post(
        "/api/endpoints",
        json={
            "name": "x",
            "base_url": "https://a/v1",
            "model": "m",
            "api_format": "anthropic-messages",
        },
    )

    assert response.status_code == 400
    assert "暂未支持" in response.json()["detail"]


def test_endpoints_create_without_key(client: TestClient) -> None:
    """创建不带密钥：成功，has_api_key=false（请求时可由环境变量兜底）。"""
    response = client.post(
        "/api/endpoints",
        json={"name": "nokey", "base_url": "https://a/v1", "model": "m"},
    )

    assert response.status_code == 201
    assert response.json()["has_api_key"] is False


def test_endpoints_update_keeps_key_then_overwrites(client: TestClient) -> None:
    """更新：不带 api_key 沿用已存密钥（文件不动）；带新密钥则替换。"""
    client.post(
        "/api/endpoints",
        json={
            "name": "prod",
            "base_url": "https://old/v1",
            "model": "m1",
            "api_key": "stored-key",  # pragma: allowlist secret —— 测试假密钥
        },
    )

    keep = client.put(
        "/api/endpoints/prod", json={"base_url": "https://new/v1", "model": "m2"}
    )
    kept_key = read_stored_api_key("prod")

    assert keep.status_code == 200
    assert keep.json()["base_url"] == "https://new/v1"
    assert keep.json()["has_api_key"] is True
    assert kept_key is not None
    assert kept_key.reveal() == "stored-key"

    client.put(
        "/api/endpoints/prod",
        json={
            "base_url": "https://new/v1",
            "model": "m2",
            "api_key": "brand-new-key",  # pragma: allowlist secret —— 测试假密钥
        },
    )
    replaced = read_stored_api_key("prod")

    assert replaced is not None
    assert replaced.reveal() == "brand-new-key"


def test_endpoints_update_missing_404(client: TestClient) -> None:
    """更新不存在的配置：404。"""
    response = client.put(
        "/api/endpoints/ghost", json={"base_url": "https://a/v1", "model": "m"}
    )

    assert response.status_code == 404


def test_endpoints_rename_via_put(client: TestClient) -> None:
    """PUT 带 new_name：改名成功返回新名概要，active 指针跟随，旧名消失。"""
    created = client.post(
        "/api/endpoints",
        json={"name": "prod", "base_url": "https://a/v1", "model": "m"},
    )

    assert created.status_code == 201
    renamed = client.put(
        "/api/endpoints/prod",
        json={"base_url": "https://a/v1", "model": "m", "new_name": "production"},
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "production"
    assert client.get("/api/config").json()["name"] == "production"
    names = [item["name"] for item in client.get("/api/endpoints").json()]
    assert names == ["production"]


def test_endpoints_rename_conflict_409(client: TestClient) -> None:
    """新名与既有配置重名（不区分大小写）：409。

    顺序是「字段更新成功 → 改名」：字段改动已落盘、名字保持旧名（参数校验失败
    不动名字），前端据此提示换名重试。
    """
    client.post(
        "/api/endpoints",
        json={"name": "alpha", "base_url": "https://a/v1", "model": "m"},
    )
    client.post(
        "/api/endpoints",
        json={"name": "beta", "base_url": "https://b/v1", "model": "m"},
    )

    response = client.put(
        "/api/endpoints/alpha",
        json={"base_url": "https://changed/v1", "model": "m", "new_name": "BETA"},
    )

    assert response.status_code == 409
    names = {item["name"] for item in client.get("/api/endpoints").json()}
    assert names == {"alpha", "beta"}
    alpha = next(
        item for item in client.get("/api/endpoints").json() if item["name"] == "alpha"
    )
    assert alpha["base_url"] == "https://changed/v1"


def test_endpoints_rename_new_name_invalid_400(client: TestClient) -> None:
    """新名含保留字符：400，且原配置不受影响。"""
    client.post(
        "/api/endpoints",
        json={"name": "prod", "base_url": "https://a/v1", "model": "m"},
    )

    response = client.put(
        "/api/endpoints/prod",
        json={"base_url": "https://a/v1", "model": "m", "new_name": "bad:name"},
    )

    assert response.status_code == 400
    names = [item["name"] for item in client.get("/api/endpoints").json()]
    assert names == ["prod"]


def test_endpoints_create_with_request_params_echoed(client: TestClient) -> None:
    """创建带请求参数：响应概要回显（未设置的键为 null）；列表同样带出。"""
    response = client.post(
        "/api/endpoints",
        json={
            "name": "tuned",
            "base_url": "https://a/v1",
            "model": "m",
            "request_params": {
                "temperature": 0.7,
                "max_tokens": 1024,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            },
        },
    )
    listing = client.get("/api/endpoints")

    assert response.status_code == 201
    assert response.json()["request_params"] == {
        "temperature": 0.7,
        "top_p": None,
        "max_tokens": 1024,
        "enable_thinking": None,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "timeout_seconds": None,
        "max_retries": None,
    }
    assert listing.json()[0]["request_params"]["temperature"] == 0.7


def test_endpoints_update_params_replace_then_preserve(client: TestClient) -> None:
    """更新参数块：显式给 = 整体替换（未给的旧键清除）；不给 = 沿用已有参数。"""
    client.post(
        "/api/endpoints",
        json={
            "name": "prod",
            "base_url": "https://a/v1",
            "model": "m",
            "request_params": {"temperature": 0.3, "timeout_seconds": 300},
        },
    )

    replaced = client.put(
        "/api/endpoints/prod",
        json={
            "base_url": "https://a/v1",
            "model": "m",
            "request_params": {"max_retries": 5},
        },
    )

    assert replaced.status_code == 200
    assert replaced.json()["request_params"]["max_retries"] == 5
    assert replaced.json()["request_params"]["temperature"] is None
    assert replaced.json()["request_params"]["timeout_seconds"] is None

    preserved = client.put(
        "/api/endpoints/prod", json={"base_url": "https://a/v1", "model": "m"}
    )

    assert preserved.status_code == 200
    assert preserved.json()["request_params"]["max_retries"] == 5


def test_endpoints_request_params_validation_422(client: TestClient) -> None:
    """参数值越界（负温度 / 零上限 / 零超时）：422，校验错误一次报全。"""
    response = client.post(
        "/api/endpoints",
        json={
            "name": "bad",
            "base_url": "https://a/v1",
            "model": "m",
            "request_params": {
                "temperature": -1,
                "max_tokens": 0,
                "timeout_seconds": 0,
            },
        },
    )

    assert response.status_code == 422


def test_endpoints_activate_switches_current(client: TestClient) -> None:
    """切换当前使用：204；列表 is_active 跟随；GET /api/config 读到新配置。"""
    client.post(
        "/api/endpoints",
        json={"name": "alpha", "base_url": "https://a/v1", "model": "m-a"},
    )
    client.post(
        "/api/endpoints",
        json={"name": "beta", "base_url": "https://b/v1", "model": "m-b"},
    )

    response = client.post("/api/endpoints/beta/activate")
    listing = client.get("/api/endpoints")
    current = client.get("/api/config")

    assert response.status_code == 204
    by_name = {item["name"]: item for item in listing.json()}
    assert by_name["alpha"]["is_active"] is False
    assert by_name["beta"]["is_active"] is True
    assert current.json()["name"] == "beta"


def test_endpoints_activate_missing_404(client: TestClient) -> None:
    """切换到不存在的配置：404。"""
    response = client.post("/api/endpoints/ghost/activate")

    assert response.status_code == 404


def test_endpoints_delete_non_active_204(client: TestClient) -> None:
    """删除非当前使用的配置：204，列表少一项。"""
    client.post(
        "/api/endpoints",
        json={"name": "alpha", "base_url": "https://a/v1", "model": "m"},
    )
    client.post(
        "/api/endpoints",
        json={"name": "beta", "base_url": "https://b/v1", "model": "m"},
    )

    response = client.delete("/api/endpoints/beta")

    assert response.status_code == 204
    assert [item["name"] for item in client.get("/api/endpoints").json()] == ["alpha"]


def test_endpoints_delete_active_409(client: TestClient) -> None:
    """删除当前使用中的配置：409（先切换再删）。"""
    client.post(
        "/api/endpoints",
        json={"name": "alpha", "base_url": "https://a/v1", "model": "m"},
    )

    response = client.delete("/api/endpoints/alpha")

    assert response.status_code == 409


def test_endpoints_delete_missing_404(client: TestClient) -> None:
    """删除不存在的配置：404。"""
    response = client.delete("/api/endpoints/ghost")

    assert response.status_code == 404


def test_endpoints_never_leak_secret(client: TestClient) -> None:
    """密钥只进不出：创建后，端点配置与当前配置的响应文本都不含密钥明文。"""
    secret = "sk-super-secret-do-not-leak"  # pragma: allowlist secret
    client.post(
        "/api/endpoints",
        json={
            "name": "prod",
            "base_url": "https://a/v1",
            "model": "m",
            "api_key": secret,
        },
    )

    endpoints_text = client.get("/api/endpoints").text
    config_text = client.get("/api/config").text

    assert secret not in endpoints_text
    assert secret not in config_text


def test_skill_files_list_and_content(client: TestClient) -> None:
    """包内容预览：清单带角色标注；SKILL.md 与 references 文件可读出原文。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    files = client.get("/api/skills/example-caption-skill/files")
    content = client.get("/api/skills/example-caption-skill/files/SKILL.md")
    reference = client.get(
        "/api/skills/example-caption-skill/files/references/detail.md"
    )

    assert files.status_code == 200
    body = files.json()
    assert body["name"] == "example-caption-skill"
    by_path = {item["path"]: item for item in body["files"]}
    assert by_path["SKILL.md"]["role"] == "skill"
    assert by_path["SKILL.md"]["previewable"] is True
    assert by_path["references/detail.md"]["previewable"] is True

    assert content.status_code == 200
    assert content.json()["path"] == "SKILL.md"
    assert "name:" in content.json()["content"]
    assert reference.status_code == 200
    assert reference.json()["content"].startswith("# Detail")


def test_skill_files_assets_not_previewable_400(
    client: TestClient, tmp_path: Path
) -> None:
    """assets / scripts 不参与预览：400（灰显的契约面）。"""
    source = tmp_path / "skill-pack-with-assets"
    shutil.copytree(_SKILL_PACK, source)
    (source / "assets").mkdir()
    (source / "assets" / "cover.png").write_bytes(b"\x89PNG\r\n")
    client.post("/api/skills/import", json={"path": str(source)})

    response = client.get("/api/skills/example-caption-skill/files/assets/cover.png")

    assert response.status_code == 400


def test_skill_files_missing_file_404(client: TestClient) -> None:
    """包内无此文件：404。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    response = client.get("/api/skills/example-caption-skill/files/references/nope.md")

    assert response.status_code == 404


def test_skill_files_missing_skill_404(client: TestClient) -> None:
    """skill 不存在：404。"""
    response = client.get("/api/skills/ghost/files")

    assert response.status_code == 404


def test_frontend_served_when_dir_has_index(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """frontend 目录有 index.html：GET / 返回页面，API 路由不受挂载影响（自建 client 也要 temp_data_root 隔离数据根）。"""
    (tmp_path / "index.html").write_text("<h1>Dataset Factory</h1>", encoding="utf-8")
    local_client = TestClient(create_app(frontend_dir=tmp_path))

    page = local_client.get("/")
    api = local_client.get("/api/prompts")

    assert page.status_code == 200
    assert "Dataset Factory" in page.text
    assert api.status_code == 200


def test_endpoints_test_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试连接：配置正确 → ok=true、耗时非负、探测用的是请求里的 base_url。"""
    captured: dict[str, object] = {}

    def fake_probe(config: EndpointConfig) -> ProbeResult:
        captured["base_url"] = config.base_url
        return ProbeResult(ok=True, message="连接成功，模型应答正常。", latency_ms=5.0)

    monkeypatch.setattr(routes_endpoints, "probe_endpoint", fake_probe)

    response = client.post(
        "/api/endpoints/test",
        json={
            "base_url": "https://example.com/v1",
            "model": "test-model",
            "api_key": "sk-test",  # pragma: allowlist secret
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0
    assert captured["base_url"] == "https://example.com/v1"


def test_endpoints_test_llm_error_becomes_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """测试连接：探测失败 → ok=false + 分类消息（HTTP 仍 200，成败看 ok）。"""

    def fake_probe(config: EndpointConfig) -> ProbeResult:
        return ProbeResult(
            ok=False, message="调用模型超时；网络较慢或模型响应久。", latency_ms=1.0
        )

    monkeypatch.setattr(routes_endpoints, "probe_endpoint", fake_probe)

    response = client.post(
        "/api/endpoints/test",
        json={
            "base_url": "https://example.com/v1",
            "model": "test-model",
            "api_key": "sk-test",  # pragma: allowlist secret
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "超时" in body["message"]


def test_endpoints_test_without_key_reports(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """测试连接：无密钥可回落（表单没填、配置名下也没有）→ ok=false + 可操作提示。"""

    def fake_probe(config: EndpointConfig) -> ProbeResult:
        raise AssertionError("不应发起请求")

    monkeypatch.setattr(routes_endpoints, "probe_endpoint", fake_probe)

    response = client.post(
        "/api/endpoints/test",
        json={"base_url": "https://example.com/v1", "model": "test-model"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "密钥" in body["message"]


def test_endpoints_test_falls_back_to_stored_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """测试连接：表单密钥留空 → 用该配置名下已存密钥（不强迫重输）。"""
    created = client.post(
        "/api/endpoints",
        json={
            "name": "stored",
            "base_url": "https://example.com/v1",
            "model": "test-model",
            "api_key": "sk-stored",  # pragma: allowlist secret
        },
    )
    assert created.status_code == 201
    captured: dict[str, object] = {}

    def fake_probe(config: EndpointConfig) -> ProbeResult:
        captured["key"] = config.api_key.reveal()
        return ProbeResult(ok=True, message="连接成功，模型应答正常。", latency_ms=1.0)

    monkeypatch.setattr(routes_endpoints, "probe_endpoint", fake_probe)

    response = client.post(
        "/api/endpoints/test",
        json={
            "base_url": "https://example.com/v1",
            "model": "test-model",
            "name": "stored",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["key"] == "sk-stored"


_SKILL_UPLOAD_FILES = [
    (
        "files",
        (
            "SKILL.md",
            b"---\nname: upload-skill\ndescription: uploaded\n---\n\n# U\n",
            "text/markdown",
        ),
    ),
    ("files", ("references/guide.md", b"# guide", "text/markdown")),
]


def test_skills_import_upload_ok(client: TestClient) -> None:
    """上传导入：文件集含 SKILL.md → 入库默认启用、可在列表中看到。"""
    response = client.post("/api/skills/import-upload", files=_SKILL_UPLOAD_FILES)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "upload-skill"
    assert body["enabled"] is True
    names = [item["name"] for item in client.get("/api/skills").json()]
    assert "upload-skill" in names


def test_skills_import_upload_tolerates_crlf_skill_md(client: TestClient) -> None:
    """上传导入：CRLF 行尾的 SKILL.md 不被误判缺 frontmatter。

    实锤 2026-09-13：真实浏览器场景导入 WorkBuddy 生态的 CRLF 包报 400，而包本身合法。
    """
    crlf_files = [
        (
            "files",
            (
                "SKILL.md",
                b"---\r\nname: crlf-skill\r\ndescription: windows line endings\r\n---\r\n\r\n# CRLF\r\n",
                "text/markdown",
            ),
        ),
        ("files", ("references/guide.md", b"# guide\r\n", "text/markdown")),
    ]

    response = client.post("/api/skills/import-upload", files=crlf_files)

    assert response.status_code == 200
    assert response.json()["name"] == "crlf-skill"


def test_skills_import_upload_missing_skill_md_is_400(client: TestClient) -> None:
    """上传导入：缺 SKILL.md → 400、提示选含 SKILL.md 的文件夹。"""
    response = client.post(
        "/api/skills/import-upload",
        files=[("files", ("references/x.md", b"x", "text/markdown"))],
    )

    assert response.status_code == 400
    assert "SKILL.md" in response.json()["detail"]


def test_skills_import_upload_rejects_traversal(client: TestClient) -> None:
    """上传导入：文件名含 .. 穿越 → 400 拒绝，不入库。"""
    response = client.post(
        "/api/skills/import-upload",
        files=[
            (
                "files",
                (
                    "SKILL.md",
                    b"---\nname: evil\ndescription: e\n---\n",
                    "text/markdown",
                ),
            ),
            ("files", ("../evil.md", b"x", "text/markdown")),
        ],
    )

    assert response.status_code == 400
    names = [item["name"] for item in client.get("/api/skills").json()]
    assert "evil" not in names


def test_skills_import_upload_strips_picker_root_folder(client: TestClient) -> None:
    """上传导入：浏览器文件夹选择器给的 `<所选文件夹>/SKILL.md` 形状 → 剥掉顶层后正常入库。"""
    response = client.post(
        "/api/skills/import-upload",
        files=[
            (
                "files",
                (
                    "fresh/SKILL.md",
                    b"---\nname: picked-skill\ndescription: picked\n---\n\n# P\n",
                    "text/markdown",
                ),
            ),
            ("files", ("fresh/references/guide.md", b"# guide", "text/markdown")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["name"] == "picked-skill"
    listing = client.get("/api/skills/picked-skill/files").json()
    assert sorted(item["path"] for item in listing["files"]) == [
        "SKILL.md",
        "references/guide.md",
    ]


def test_skills_import_upload_ambiguous_roots_is_400(client: TestClient) -> None:
    """上传导入：两个顶层目录且根上无 SKILL.md → 400，不猜用户意图。"""
    response = client.post(
        "/api/skills/import-upload",
        files=[
            (
                "files",
                (
                    "a/SKILL.md",
                    b"---\nname: a\ndescription: a\n---\n",
                    "text/markdown",
                ),
            ),
            (
                "files",
                (
                    "b/SKILL.md",
                    b"---\nname: b\ndescription: b\n---\n",
                    "text/markdown",
                ),
            ),
        ],
    )

    assert response.status_code == 400
    assert "SKILL.md" in response.json()["detail"]


def test_label_stream_sse(client: TestClient, fake_engine: FakeCompleter) -> None:
    """流式打标端点：SSE 事件序列 start → delta… → done，帧格式 event + data。"""
    _save_prompt("p1", "你是打标助手。")

    response = client.post(
        "/api/label/stream",
        json={"prompt_name": "p1", "instruction": "描述它"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame.strip()]
    events = [frame.splitlines()[0].removeprefix("event: ") for frame in frames]
    assert events == ["start", "delta", "delta", "done"]
    done_data = json.loads(frames[-1].splitlines()[1].removeprefix("data: "))
    assert done_data["caption"] == "打标结果"
    assert "session_id" in done_data


def test_session_snapshot_exposes_persisted_reasoning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """思考过程随会话快照透出：界面回看历史消息的「思考过程」折叠区只有这一个数据来源。"""
    from dataset_factory.labeling import LabelingEngine

    class ThinkingCompleter(FakeCompleter):
        """流式路线上多一段思考增量，其余沿用父类的逐轮正文脚本。"""

        def stream(self, messages: Sequence[Message]) -> Iterator[StreamDelta]:
            yield StreamDelta(kind="reasoning", text="先确认主体。")
            yield from super().stream(messages)

    monkeypatch.setattr(
        routes_labeling,
        "build_engine",
        lambda: LabelingEngine(ThinkingCompleter(), "test-model"),
    )
    _save_prompt("p1", "你是打标助手。")
    client.post(
        "/api/label/stream", json={"prompt_name": "p1", "instruction": "描述它"}
    )

    messages = client.get("/api/sessions/latest").json()["messages"]

    assert [item["text"] for item in messages] == ["描述它", "打标结果"]
    assert messages[1]["reasoning"] == "先确认主体。"
    assert messages[0]["reasoning"] is None


def test_label_stream_mid_stream_error_emits_error_frame(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """流中途模型失败 → SSE error 帧（TC-24④ 的自动化兜底；audit 2026-09-14 补）。"""

    class _MidStreamErrorCompleter:
        def stream(self, messages: object) -> object:
            yield StreamDelta(kind="content", text="部分")
            raise LLMError("模型流中途失败（测试）")

        def complete(self, messages: object) -> str:
            raise AssertionError("流式路径不应调用 complete")

    engine = LabelingEngine(_MidStreamErrorCompleter(), "m")  # type: ignore[arg-type]
    monkeypatch.setattr(routes_labeling, "build_engine", lambda: engine)

    _save_prompt("p1", "你是打标助手。")
    response = client.post(
        "/api/label/stream", json={"prompt_name": "p1", "instruction": "x"}
    )

    assert response.status_code == 200
    frames = [f for f in response.text.split("\n\n") if f.strip()]
    events = [f.splitlines()[0].removeprefix("event: ") for f in frames]
    assert events == ["start", "delta", "error"]
    assert "模型流中途失败" in frames[-1]


def test_skills_import_upload_conflict_409(client: TestClient) -> None:
    """上传导入：重名不合并 → 第二次 409。"""
    first = client.post("/api/skills/import-upload", files=_SKILL_UPLOAD_FILES)
    second = client.post("/api/skills/import-upload", files=_SKILL_UPLOAD_FILES)

    assert first.status_code == 200
    assert second.status_code == 409


def test_label_with_video_uses_video_params(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """视频打标：video_base64 → VideoPart（fps / 帧上限随请求），走同一打标核心。"""
    _save_prompt("p1", "你是打标助手。")
    payload = base64.b64encode(b"fake-mp4").decode("ascii")

    response = client.post(
        "/api/label",
        json={
            "prompt_name": "p1",
            "instruction": "描述动作",
            "video_base64": payload,
            "video_name": "clip.mp4",
            "video_fps": 3,
            "video_max_frames": 8,
        },
    )

    assert response.status_code == 200
    user = fake_engine.calls[0][-1]
    assert user.parts == (
        TextPart("描述动作"),
        VideoPart(b"fake-mp4", fps=3, max_frames=8),
    )


def test_label_with_fractional_fps_is_422(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """视频 fps 非整数 → 422（端点对浮点 fps 判 20015，契约直接收窄为整型）。"""
    payload = base64.b64encode(b"fake-mp4").decode("ascii")

    response = client.post(
        "/api/label",
        json={
            "prompt_name": "p1",
            "instruction": "x",
            "video_base64": payload,
            "video_fps": 1.5,
        },
    )

    assert response.status_code == 422


def test_label_image_and_video_together_is_400(
    client: TestClient, fake_engine: FakeCompleter
) -> None:
    """同轮同时带图片与视频 → 400（一期单素材/次）。"""
    payload = base64.b64encode(b"x").decode("ascii")

    response = client.post(
        "/api/label",
        json={
            "prompt_name": "p1",
            "instruction": "x",
            "image_base64": payload,
            "video_base64": payload,
        },
    )

    assert response.status_code == 400


class TestSessionOwnershipApi:
    """会话归属的 API 面（三期 v3）：盖章、按桶查 latest、改挂、删策略级联、进行中拒绝。"""

    def test_label_stamps_ownership_and_latest_by_bucket(
        self, client: TestClient, fake_engine: FakeCompleter
    ) -> None:
        """带 strategy_id 发送：会话盖上归属章；按桶查 latest 命中它、别的桶 404。"""
        _save_prompt("h3", "你是打标助手。")

        first = client.post(
            "/api/label",
            json={"prompt_name": "h3", "instruction": "一轮", "strategy_id": "s-x"},
        )
        assert first.status_code == 200
        session_id = first.json()["session_id"]

        bucket = client.get("/api/sessions/latest", params={"strategy_id": "s-x"})
        assert bucket.status_code == 200
        assert bucket.json()["session_id"] == session_id
        assert bucket.json()["strategy_id"] == "s-x"
        assert (
            client.get(
                "/api/sessions/latest", params={"strategy_id": "s-other"}
            ).status_code
            == 404
        )
        # 无归属的查询（全局 latest）也照常可达（存量垫层入口）。
        assert client.get("/api/sessions/latest").status_code == 200

    def test_assign_strategy_moves_session(
        self, client: TestClient, fake_engine: FakeCompleter
    ) -> None:
        """改挂端点：把 __new__ 会话挂到新策略 id，旧桶随即查空。"""
        _save_prompt("h3", "你是打标助手。")
        session_id = client.post(
            "/api/label",
            json={
                "prompt_name": "h3",
                "instruction": "草稿轮",
                "strategy_id": "__new__",
            },
        ).json()["session_id"]

        moved = client.post(
            f"/api/sessions/{session_id}/strategy", json={"strategy_id": "s-new"}
        )

        assert moved.status_code == 200
        assert moved.json()["strategy_id"] == "s-new"
        assert (
            client.get(
                "/api/sessions/latest", params={"strategy_id": "__new__"}
            ).status_code
            == 404
        )
        assert (
            client.get("/api/sessions/latest", params={"strategy_id": "s-new"}).json()[
                "session_id"
            ]
            == session_id
        )
        missing = client.post(
            "/api/sessions/20990101-000000-000000/strategy",
            json={"strategy_id": "s-x"},
        )
        assert missing.status_code == 404

    def test_delete_strategy_cascades_bucket_sessions(
        self, client: TestClient, fake_engine: FakeCompleter
    ) -> None:
        """删策略级联删桶内会话：滚掉失败半截后的桶内容随策略一起消失。"""
        from dataset_factory.llm import create_config
        from dataset_factory.sessions import (
            create_session as create,
        )
        from dataset_factory.sessions import (
            latest_session_id_for,
        )

        create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
        _save_prompt("h3", "你是打标助手。")
        created = client.post(
            "/api/strategies",
            json={
                "name": "删除我",
                "description": "",
                "endpoint": "main",
                "prompt": "h3",
                "skills": [],
            },
        )
        assert created.status_code == 201, created.text
        strategy_id = created.json()["id"]
        session_id = client.post(
            "/api/label",
            json={
                "prompt_name": "h3",
                "instruction": "一轮",
                "strategy_id": strategy_id,
            },
        ).json()["session_id"]
        # 桶里再造一份失败半截（不属于滚动保留的常规路径，模拟存量）。
        stale = create(strategy_id=strategy_id)

        response = client.delete(f"/api/strategies/{strategy_id}")

        assert response.status_code == 204
        assert latest_session_id_for(strategy_id) is None
        assert session_id not in list_sessions()
        assert stale not in list_sessions()

    def test_delete_session_rejected_while_turn_active(
        self,
        client: TestClient,
        fake_engine: FakeCompleter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """会话有进行中轮次时删除被 409 拒绝；轮次结束后可删。"""
        from dataset_factory.labeling import active_session_ids

        _save_prompt("h3", "你是打标助手。")
        session_id = client.post(
            "/api/label",
            json={"prompt_name": "h3", "instruction": "一轮", "strategy_id": "s-busy"},
        ).json()["session_id"]

        # 直接登记一个进行中轮次，模拟流式生成中。
        import dataset_factory.labeling.engine as engine_module

        monkeypatch.setattr(engine_module, "_ACTIVE_TURNS", {session_id})
        assert session_id in active_session_ids()
        busy = client.delete(f"/api/sessions/{session_id}")
        assert busy.status_code == 409

        monkeypatch.setattr(engine_module, "_ACTIVE_TURNS", set[str]())
        client.delete(f"/api/sessions/{session_id}")
        assert session_id not in list_sessions()
