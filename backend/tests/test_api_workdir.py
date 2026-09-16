"""接口测试：workdir 注册表端点（GET /api/workdirs + GET /{wid}，problem+json 404）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.workdir import WorkdirRegistry


@pytest.fixture
def client(tmp_path: Path, temp_data_root: Path) -> TestClient:
    """挂临时空 frontend 目录的测试客户端（依赖 temp_data_root 隔离数据根）。"""
    return TestClient(create_app(frontend_dir=tmp_path))


def test_list_empty_registry_returns_empty_list(client: TestClient) -> None:
    """空注册表 → 空数组（不是 404——下拉数据源的空态）。"""
    response = client.get("/api/workdirs")

    assert response.status_code == 200
    assert response.json() == []


def test_list_returns_registered_entries(client: TestClient, tmp_path: Path) -> None:
    """登记后列表可见：字段齐全（id / path / title / last_used_at）。"""
    target = tmp_path / "photos"
    target.mkdir()
    entry = WorkdirRegistry.register(target, title="")

    response = client.get("/api/workdirs")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == entry.id
    assert body[0]["title"] == "photos"
    assert body[0]["path"] == str(target)
    assert isinstance(body[0]["last_used_at"], float)


def test_get_by_wid_returns_entry(client: TestClient, tmp_path: Path) -> None:
    """按 wid 查详情返回同一条目。"""
    target = tmp_path / "photos"
    target.mkdir()
    entry = WorkdirRegistry.register(target, title="我的图")

    response = client.get(f"/api/workdirs/{entry.id}")

    assert response.status_code == 200
    assert response.json()["title"] == "我的图"


def test_get_unknown_wid_returns_problem_json_404(client: TestClient) -> None:
    """未知 wid 404：problem+json 四件套（workdir-not-found）。"""
    response = client.get("/api/workdirs/no-such-wid")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "workdir-not-found"
    assert body["title"] == "工作目录不存在"
    assert body["status"] == 404
    assert "detail" in body
