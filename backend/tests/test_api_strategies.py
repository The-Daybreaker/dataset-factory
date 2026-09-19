"""接口测试：策略库与批次端点（库 CRUD / copy / rebind、批次新建 / 补丁 / 停用 / 删除 / 排除名单）。

全部同步端点（无长任务），TestClient 直测；资产用 fixture 预置（端点 / 提示词 / Skill）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.llm import create_config
from dataset_factory.prompts import Prompt, delete_prompt, save_prompt
from dataset_factory.runs.journal import RunJournal
from dataset_factory.skills import import_skill
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore

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


def test_library_body_chars_sums_prompt_and_enabled_skills(
    client: TestClient, assets: None
) -> None:
    """body_chars = 提示词正文 + 引用的启用 Skill 注入全文；停用 / 缺失按 0 计。"""
    created = client.post("/api/strategies", json=_strategy_payload())
    assert created.status_code == 201
    entry = created.json()
    prompt_chars = len("你是打标器")
    skill_chars = next(
        item["body_chars"]
        for item in client.get("/api/skills").json()
        if item["name"] == _SKILL_NAME
    )
    assert entry["body_chars"] == prompt_chars + skill_chars

    # 停用引用的 Skill：注入量里这部分归 0，只剩提示词正文
    client.post(f"/api/skills/{_SKILL_NAME}/disable")
    assert client.get("/api/strategies").json()[0]["body_chars"] == prompt_chars

    # 提示词被删除（引用缺失）：字数归 0，缺失由 available / missing_refs 表达
    delete_prompt("详细描述")
    listing = client.get("/api/strategies").json()[0]
    assert listing["body_chars"] == 0
    assert listing["available"] is False


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


def test_snapshot_reads_stored_content_after_library_edit(
    client: TestClient, assets: None, wid: str
) -> None:
    """快照读取返回应用时的内容及原始字节哈希，不受库正文改写影响。"""
    strategy = client.post("/api/strategies", json=_strategy_payload()).json()
    client.post(
        f"/api/workdirs/{wid}/batches", json={"type": "library", "id": strategy["id"]}
    )
    path = WorkdirStore(Path(WorkdirRegistry.get(wid).path)).strategies_dir / "s1.json"
    original = path.read_bytes()
    save_prompt(Prompt(name="详细描述", description="", body="新的库正文"))

    response = client.get(f"/api/workdirs/{wid}/batches/s1/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["prompt"]["body"] == "你是打标器"
    assert body["sha256"] == hashlib.sha256(original).hexdigest()
    assert body["recorded_sha256"] is None
    assert body["changed"] is False
    assert "api_key" not in body["endpoint"]
    assert path.read_bytes() == original


def test_snapshot_warns_after_manual_change_without_rewriting(
    client: TestClient, assets: None, wid: str
) -> None:
    """现算快照哈希与本批最近运行对比，手改只标记且不回写。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "测试",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )
    store = WorkdirStore(Path(WorkdirRegistry.get(wid).path))
    path = store.strategies_dir / "s1.json"
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    run_dir = store.runs_dir / "20260917-120000"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "batch": 1,
                "mode": "full",
                "trigger": "web",
                "strategy_hash": original_hash,
                "snapshot": "strategies/s1.json",
                "dsf_version": "0.1.0",
                "status": "completed",
                "counters": {
                    "planned": 0,
                    "attempted": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "skipped": 0,
                },
                "started_at": "2026-09-17T12:00:00Z",
                "finished_at": "2026-09-17T12:00:01Z",
            }
        ),
        encoding="utf-8",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["prompt"]["body"] = "手动修改正文"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    changed = path.read_bytes()

    response = client.get(f"/api/workdirs/{wid}/batches/s1/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["prompt"]["body"] == "手动修改正文"
    assert body["recorded_sha256"] == original_hash
    assert body["sha256"] == hashlib.sha256(changed).hexdigest()
    assert body["changed"] is True
    assert path.read_bytes() == changed


@pytest.mark.parametrize("contents", [b"not-json", b"\xff", b'{"endpoint": null}'])
def test_snapshot_invalid_file_returns_problem(
    client: TestClient, assets: None, wid: str, contents: bytes
) -> None:
    """损坏快照返回可处理的错误且不修写原文件。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "测试",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )
    path = WorkdirStore(Path(WorkdirRegistry.get(wid).path)).strategies_dir / "s1.json"
    path.write_bytes(contents)

    response = client.get(f"/api/workdirs/{wid}/batches/s1/snapshot")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert path.read_bytes() == contents


def test_snapshot_unknown_batch_returns_problem(client: TestClient, wid: str) -> None:
    """未创建的批次不可通过快照接口读取。"""
    response = client.get(f"/api/workdirs/{wid}/batches/s99/snapshot")

    assert response.status_code == 404
    assert response.json()["type"] == "batch-not-found"


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


def test_get_batch_detail_and_patch_clears_description(
    client: TestClient, assets: None, wid: str
) -> None:
    """GET 单批次详情（201 的 Location 可解析）+ PATCH 空 description = 清空。"""
    client.post(
        f"/api/workdirs/{wid}/batches",
        json={
            "type": "scratch",
            "name": "从零来",
            "description": "备注文字",
            "endpoint": "main",
            "prompt": "详细描述",
        },
    )

    detail = client.get(f"/api/workdirs/{wid}/batches/s1")
    assert detail.status_code == 200
    assert detail.json()["name"] == "从零来"

    cleared = client.patch(f"/api/workdirs/{wid}/batches/s1", json={"description": ""})
    assert cleared.status_code == 200
    assert cleared.json()["description"] == ""


def test_patch_batch_rejects_combo_fields_with_422(
    client: TestClient, assets: None, wid: str
) -> None:
    """组合不可改（批次 = 库策略的只读副本）：PATCH 带组合字段 → 422（extra=forbid）。"""
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
        f"/api/workdirs/{wid}/batches/s1",
        json={"endpoint": "main", "prompt": "另一条", "skills": []},
    )

    assert response.status_code == 422


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


def test_strategy_references_lists_applying_batches(
    client: TestClient, assets: None, wid: str, tmp_path: Path
) -> None:
    """引用清单跨工作目录按快照出身匹配；从零配置的批次与未应用策略不入清单。"""
    strategy = client.post("/api/strategies", json=_strategy_payload()).json()
    assert client.get(f"/api/strategies/{strategy['id']}/references").json() == []

    client.post(
        f"/api/workdirs/{wid}/batches", json={"type": "library", "id": strategy["id"]}
    )
    second = tmp_path / "more-photos"
    second.mkdir()
    wid2 = client.post("/api/workdirs", json={"path": str(second)}).json()["workdir"][
        "id"
    ]
    client.post(
        f"/api/workdirs/{wid2}/batches", json={"type": "library", "id": strategy["id"]}
    )
    client.post(
        f"/api/workdirs/{wid2}/batches",
        json={
            "type": "scratch",
            "name": "手搓",
            "endpoint": "main",
            "prompt": "详细描述",
            "skills": [_SKILL_NAME],
        },
    )

    references = client.get(f"/api/strategies/{strategy['id']}/references").json()
    assert len(references) == 2
    assert {entry["workdir_id"] for entry in references} == {wid, wid2}
    assert {entry["seq"] for entry in references} == {1}
    assert {entry["batch_name"] for entry in references} == {"详细描述A"}


def test_strategy_references_unknown_strategy_returns_404(client: TestClient) -> None:
    """未知库策略按 problem+json 404 处理。"""
    response = client.get("/api/strategies/s-zzz/references")
    assert response.status_code == 404
    assert response.json()["title"]


def test_batch_view_reports_latest_run_for_dropdown(
    client: TestClient, assets: None, wid: str
) -> None:
    """批次摘要带最近一次运行的状态与进度（下拉行内状态数据源）；无运行为 null。"""
    strategy = client.post("/api/strategies", json=_strategy_payload()).json()
    client.post(
        f"/api/workdirs/{wid}/batches", json={"type": "library", "id": strategy["id"]}
    )

    empty = client.get(f"/api/workdirs/{wid}/batches").json()
    assert empty[0]["run_status"] is None
    assert empty[0]["run_done"] is None
    assert empty[0]["run_total"] is None

    workdir = Path(WorkdirRegistry.get(wid).path)
    journal = RunJournal(WorkdirStore(workdir).runs_dir / "20260919-run")
    journal.write_run_json(
        {
            "run_id": "20260919-run",
            "batch": 1,
            "mode": "full",
            "trigger": "web",
            "strategy_hash": "h",
            "snapshot": "strategies/s1.json",
            "dsf_version": "0.1.0",
            "status": "completed",
            "counters": {
                "planned": 3,
                "attempted": 3,
                "succeeded": 2,
                "failed": 1,
                "skipped": 0,
            },
            "started_at": "2026-09-19T00:00:00Z",
            "finished_at": "2026-09-19T00:00:05Z",
        }
    )

    listed = client.get(f"/api/workdirs/{wid}/batches").json()
    assert listed[0]["run_status"] == "completed"
    assert listed[0]["run_done"] == 2
    assert listed[0]["run_total"] == 3
