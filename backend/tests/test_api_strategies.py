"""接口测试：策略库与批次端点（库 CRUD / copy / rebind、批次新建 / 补丁 / 停用 / 删除 / 排除名单）。

全部同步端点（无长任务），TestClient 直测；资产用 fixture 预置（端点 / 提示词 / Skill）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.llm import create_config
from dataset_factory.prompts import Prompt, delete_prompt, save_prompt
from dataset_factory.skills import import_skill

_FIXTURE_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
_SKILL_NAME = "example-caption-skill"


@pytest.fixture
def client(tmp_path: Path, temp_data_root: Path) -> TestClient:
    """挂临时空 frontend 目录的测试客户端（依赖 temp_data_root 隔离数据根）。"""
    return TestClient(create_app(frontend_dir=tmp_path))


@pytest.fixture
def assets(temp_data_root: Path) -> None:
    """预置可引用资产：端点配置 + 提示词 + Skill（golden 包）。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(Prompt(name="详细描述", description="", body="你是打标器"))
    import_skill(_FIXTURE_PACK)


@pytest.fixture
def wid(client: TestClient, tmp_path: Path) -> str:
    """一个已登记的工作目录（走真实登记端点）。"""
    target = tmp_path / "photos"
    target.mkdir()
    response = client.post("/api/workdirs", json={"path": str(target)})
    return response.json()["workdir"]["id"]


def _strategy_payload(**overrides: str) -> dict[str, object]:
    """合法的建库请求体（测试按需覆盖字段）。"""
    payload: dict[str, object] = {
        "name": "详细描述A",
        "description": "",
        "endpoint": "main",
        "prompt": "详细描述",
        "skills": [_SKILL_NAME],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# 策略库
# --------------------------------------------------------------------------


def test_library_crud_roundtrip(client: TestClient, assets: None) -> None:
    """建 → 查列表 → 查详情 → 改 → 删，全链状态一致。"""
    created = client.post("/api/strategies", json=_strategy_payload())

    assert created.status_code == 201
    entry = created.json()
    assert entry["available"] is True
    assert entry["missing_refs"] == []
    assert entry["skills"] == [_SKILL_NAME]

    listed = client.get("/api/strategies").json()
    assert [item["id"] for item in listed] == [entry["id"]]

    fetched = client.get(f"/api/strategies/{entry['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "详细描述A"

    updated = client.put(
        f"/api/strategies/{entry['id']}",
        json=_strategy_payload(name="新名", description="改过"),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "新名"

    deleted = client.delete(f"/api/strategies/{entry['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/strategies").json() == []


def test_library_copy_and_rebind(client: TestClient, assets: None) -> None:
    """copy 派生新 ID 内容原样；rebind 只动提供的引用位。"""
    source = client.post("/api/strategies", json=_strategy_payload()).json()

    clone = client.post(f"/api/strategies/{source['id']}/copy")
    assert clone.status_code == 201
    clone_entry = clone.json()
    assert clone_entry["id"] != source["id"]
    assert clone_entry["endpoint"] == source["endpoint"]

    save_prompt(Prompt(name="替补", description="", body="替补正文"))
    rebound = client.post(
        f"/api/strategies/{source['id']}/rebind", json={"prompt": "替补"}
    )
    assert rebound.status_code == 200
    body = rebound.json()
    assert body["prompt"] == "替补"
    assert body["endpoint"] == "main"
    assert body["skills"] == [_SKILL_NAME]


def test_library_rebind_without_any_ref_returns_422(
    client: TestClient, assets: None
) -> None:
    """rebind 一个引用位都不给 → FastAPI 校验 422（模型验证器的错）。"""
    entry = client.post("/api/strategies", json=_strategy_payload()).json()

    response = client.post(f"/api/strategies/{entry['id']}/rebind", json={})

    assert response.status_code == 422


def test_library_create_with_missing_ref_returns_problem_json(
    client: TestClient, assets: None
) -> None:
    """引用不存在 → 400 problem+json（strategy-refs-invalid）。"""
    response = client.post(
        "/api/strategies", json=_strategy_payload(prompt="没有的提示词")
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "strategy-refs-invalid"
    assert body["status"] == 400


def test_library_health_shows_missing_refs_after_delete(
    client: TestClient, assets: None
) -> None:
    """提示词被删 → 列表里该策略 available=False 且 missing_refs 有原因。"""
    entry = client.post("/api/strategies", json=_strategy_payload()).json()
    delete_prompt("详细描述")

    body = client.get(f"/api/strategies/{entry['id']}").json()

    assert body["available"] is False
    assert body["missing_refs"] == ["基础提示词「详细描述」不存在"]


def test_library_unknown_id_returns_problem_json_404(
    client: TestClient, assets: None
) -> None:
    """未知库策略 ID → 404 problem+json（strategy-not-found）。"""
    response = client.get("/api/strategies/no-such-id")

    assert response.status_code == 404
    assert response.json()["type"] == "strategy-not-found"


# --------------------------------------------------------------------------
# 批次
# --------------------------------------------------------------------------


def test_create_batch_from_library_with_location(
    client: TestClient, assets: None, wid: str
) -> None:
    """library 新建：201 + Location 指向新批次 + 来源记进快照（读快照文件核对）。"""
    strategy = client.post("/api/strategies", json=_strategy_payload()).json()

    response = client.post(
        f"/api/workdirs/{wid}/batches", json={"type": "library", "id": strategy["id"]}
    )

    assert response.status_code == 201
    assert response.headers["Location"].endswith(f"/api/workdirs/{wid}/batches/s1")
    body = response.json()
    assert body["id"] == "s1"
    assert body["seq"] == 1
    assert body["name"] == "详细描述A"
    assert body["active"] is True
    assert body["product_count"] == 0

    listed = client.get(f"/api/workdirs/{wid}/batches").json()
    assert [item["id"] for item in listed] == ["s1"]


def test_create_batch_scratch_and_delete(
    client: TestClient, assets: None, wid: str, tmp_path: Path
) -> None:
    """scratch 新建 → 补丁改名 → 删除 204（列表清空）。"""
    created = client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "从零来",
            "endpoint": "main",
            "prompt": "详细描述",
            "skills": [_SKILL_NAME],
        },
    )

    assert created.status_code == 201
    assert created.json()["name"] == "从零来"

    patched = client.patch(f"/api/workdirs/{wid}/batches/s1", json={"name": "改名了"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "改名了"

    deleted = client.delete(f"/api/workdirs/{wid}/batches/s1")
    assert deleted.status_code == 204
    assert client.get(f"/api/workdirs/{wid}/batches").json() == []


def test_create_batch_library_missing_ref_returns_problem_json(
    client: TestClient, assets: None, wid: str
) -> None:
    """库策略引用已缺失（置灰）→ 应用被拒 400 problem+json。"""
    strategy = client.post("/api/strategies", json=_strategy_payload()).json()
    delete_prompt("详细描述")

    response = client.post(
        f"/api/workdirs/{wid}/batches", json={"type": "library", "id": strategy["id"]}
    )

    assert response.status_code == 400
    assert response.json()["type"] == "strategy-refs-invalid"


def test_create_batch_unknown_workdir_or_strategy_returns_404(
    client: TestClient, assets: None, tmp_path: Path
) -> None:
    """wid 不在注册表 / 库策略 ID 不存在 → 各自的 404 problem+json。"""
    missing_workdir = client.post(
        "/api/workdirs/no-such-wid/batches",
        json={"type": "scratch", "name": "x", "endpoint": "main", "prompt": "详细描述"},
    )
    assert missing_workdir.status_code == 404
    assert missing_workdir.json()["type"] == "workdir-not-found"

    target = tmp_path / "photos2"
    target.mkdir()
    real_wid = client.post("/api/workdirs", json={"path": str(target)}).json()[
        "workdir"
    ]["id"]
    missing_strategy = client.post(
        f"/api/workdirs/{real_wid}/batches",
        json={"type": "library", "id": "no-such-id"},
    )
    assert missing_strategy.status_code == 404
    assert missing_strategy.json()["type"] == "strategy-not-found"


def test_create_batch_invalid_type_returns_422(
    client: TestClient, assets: None, wid: str
) -> None:
    """type 非法 → 422（模型验证器一次报全）。"""
    response = client.post(f"/api/workdirs/{wid}/batches", json={"type": "magic"})

    assert response.status_code == 422


def test_patch_batch_partial_combo_returns_problem_json(
    client: TestClient, assets: None, wid: str
) -> None:
    """组合只给一部分 → 400 problem+json（strategy-refs-invalid，同时提供语义）。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "从零来",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )

    response = client.patch(
        f"/api/workdirs/{wid}/batches/s1", json={"prompt": "详细描述"}
    )

    assert response.status_code == 400
    assert response.json()["type"] == "strategy-refs-invalid"


def test_patch_batch_combo_rebuilds_snapshot(
    client: TestClient, assets: None, wid: str, tmp_path: Path
) -> None:
    """组合整体替换 → 快照重新装配（正文刷新），批次元数据不变。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "从零来",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )
    save_prompt(Prompt(name="另一条", description="", body="另一套正文"))

    response = client.patch(
        f"/api/workdirs/{wid}/batches/s1",
        json={"endpoint": "main", "prompt": "另一条", "skills": []},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "从零来"
    snapshot_file = tmp_path / "photos" / ".dsf" / "strategies" / "s1.json"
    assert snapshot_file.is_file()
    assert "另一套正文" in snapshot_file.read_text(encoding="utf-8")


def test_hide_unhide_and_delete_unknown_batch_problem_json(
    client: TestClient, assets: None, wid: str
) -> None:
    """停用 / 召回翻转 active；未知批次 404 problem+json（batch-not-found）。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "从零来",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )

    hidden = client.post(f"/api/workdirs/{wid}/batches/s1/hide")
    assert hidden.status_code == 200
    assert hidden.json()["active"] is False

    unhidden = client.post(f"/api/workdirs/{wid}/batches/s1/unhide")
    assert unhidden.status_code == 200
    assert unhidden.json()["active"] is True

    missing = client.post(f"/api/workdirs/{wid}/batches/s9/hide")
    assert missing.status_code == 404
    assert missing.json()["type"] == "batch-not-found"


def test_exclusions_add_and_remove(client: TestClient, assets: None, wid: str) -> None:
    """排除名单：POST 增（幂等去重）、DELETE 撤（请求体条目数组），均返回全量。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "从零来",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )

    added = client.post(
        f"/api/workdirs/{wid}/batches/s1/exclusions",
        json={"items": ["cat_001", "cat_001"]},
    )
    assert added.status_code == 200
    assert added.json() == {"id": "s1", "seq": 1, "items": ["cat_001"]}

    removed = client.request(
        "DELETE",
        f"/api/workdirs/{wid}/batches/s1/exclusions",
        json={"items": ["cat_001"]},
    )
    assert removed.status_code == 200
    assert removed.json()["items"] == []


def test_exclusions_unknown_batch_returns_problem_json(
    client: TestClient, assets: None, wid: str
) -> None:
    """未知批次的排除操作 → 404 problem+json（batch-not-found）。"""
    response = client.post(
        f"/api/workdirs/{wid}/batches/s9/exclusions", json={"items": ["cat_001"]}
    )

    assert response.status_code == 404
    assert response.json()["type"] == "batch-not-found"
