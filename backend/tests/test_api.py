"""接口测试：api 入口层（FastAPI TestClient 全端点 + 错误映射 + frontend 托管）。

打标端点 monkeypatch api.routes_labeling.build_engine 注入假客户端（离线）；frontend
托管用临时目录（测试确定性，不依赖真实 frontend 是否已建）。
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dataset_factory.api.routes_labeling as routes_labeling
from dataset_factory.api import create_app
from dataset_factory.llm import ImagePart, read_stored_api_key
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


def test_prompts_invalid_name_is_400(client: TestClient) -> None:
    """名称含非法字符（Windows 禁字符 :）：400。"""
    response = client.put(
        "/api/prompts/bad%3Aname", json={"description": "", "body": "x"}
    )

    assert response.status_code == 400


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
    assert disabled.status_code == 204
    assert listing_disabled.json()[0]["enabled"] is False
    assert enabled.status_code == 204
    assert removed.status_code == 204
    assert missing.status_code == 404


def test_skills_import_conflict_is_409(client: TestClient) -> None:
    """重复导入同名 skill：409（重名不合并）。"""
    client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    response = client.post("/api/skills/import", json={"path": str(_SKILL_PACK)})

    assert response.status_code == 409


def test_skills_import_bad_path_is_400(client: TestClient) -> None:
    """导入路径不存在：400（用户填错路径，不是系统错）。"""
    response = client.post("/api/skills/import", json={"path": "Z:/不存在/skill"})

    assert response.status_code == 400


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
