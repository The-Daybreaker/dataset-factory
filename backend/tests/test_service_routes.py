"""接口测试：服务运行端点（状态 / 日志尾部 / 停机请求）。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.api.routes_service import server_log_path
from dataset_factory.cli.main import add_server_file_handler


class FakeServer:
    """假 uvicorn.Server：只带 should_exit 开关，供断言停机请求置位。"""

    def __init__(self) -> None:
        """初始为不退出。"""
        self.should_exit = False


def _app_with(tmp_path: Path) -> FastAPI:
    """测试用 app：frontend_dir 指向临时目录（无产物则不挂载静态页）。"""
    return create_app(frontend_dir=tmp_path)


def test_service_status_reports_injected_info(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """serve 注入的启动信息被状态端点原样报告。"""
    app = _app_with(tmp_path)
    app.state.service_info = {
        "version": "0.1.0",
        "host": "127.0.0.1",
        "port": 8000,
        "started_at": "2026-09-13T00:00:00+00:00",
        "log_file": str(temp_data_root / "logs" / "server.log"),
    }
    client = TestClient(app)

    resp = client.get("/api/service")

    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "0.1.0"
    assert body["host"] == "127.0.0.1"
    assert body["port"] == 8000
    assert body["log_file"].endswith("server.log")


def test_service_status_without_info_is_409(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """不经 serve 拉起（无注入信息）时状态端点 409、消息可操作。"""
    client = TestClient(_app_with(tmp_path))

    resp = client.get("/api/service")

    assert resp.status_code == 409
    assert "dsf serve" in resp.json()["detail"]


def test_service_logs_returns_tail(temp_data_root: Path, tmp_path: Path) -> None:
    """日志文件存在时只返回尾部 N 行（按块回退读取，不全量载入）。"""
    log_file = server_log_path()
    log_file.parent.mkdir(parents=True)
    lines = [f"line {i}" for i in range(1, 51)]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    client = TestClient(_app_with(tmp_path))

    resp = client.get("/api/service/logs", params={"lines": 3})

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["path"].endswith("server.log")
    assert body["content"].splitlines() == ["line 48", "line 49", "line 50"]


def test_service_logs_missing_file_reports_empty(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """日志文件尚未创建时 exists=false、内容为空、路径照报（不报错）。"""
    client = TestClient(_app_with(tmp_path))

    resp = client.get("/api/service/logs")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["content"] == ""
    assert body["path"].endswith("server.log")


def test_service_logs_lines_out_of_range_is_422(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """lines 越界（>1000）由 FastAPI 参数校验拦下（422，一次报全）。"""
    client = TestClient(_app_with(tmp_path))

    resp = client.get("/api/service/logs", params={"lines": 1001})

    assert resp.status_code == 422


def test_shutdown_sets_exit_flag(temp_data_root: Path, tmp_path: Path) -> None:
    """application/json 的停机请求 → 置位 should_exit、返回 202。"""
    app = _app_with(tmp_path)
    server = FakeServer()
    app.state.uvicorn_server = server
    client = TestClient(app)

    resp = client.post("/api/service/shutdown", json={})

    assert resp.status_code == 202
    assert server.should_exit is True


def test_shutdown_rejects_non_json(temp_data_root: Path, tmp_path: Path) -> None:
    """非 application/json（如跨站表单）→ 415、退出开关不置位。"""
    app = _app_with(tmp_path)
    server = FakeServer()
    app.state.uvicorn_server = server
    client = TestClient(app)

    resp = client.post("/api/service/shutdown", data={"x": "1"})

    assert resp.status_code == 415
    assert server.should_exit is False


def test_shutdown_without_server_is_409(temp_data_root: Path, tmp_path: Path) -> None:
    """不经 serve 拉起（无注入实例）→ 409、消息可操作。"""
    client = TestClient(_app_with(tmp_path))

    resp = client.post("/api/service/shutdown", json={})

    assert resp.status_code == 409
    assert "dsf serve" in resp.json()["detail"]


def test_add_server_file_handler_writes_logs(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """serve 的文件日志 handler：挂上后日志记录进入指定文件（含请求 id 过滤接线）。"""
    log_file = tmp_path / "logs" / "server.log"
    handler = add_server_file_handler(log_file)
    logger = logging.getLogger("dataset_factory.tests.probe")
    try:
        logger.warning("写入探测 %s", "一条")
        for h in logging.getLogger().handlers:
            h.flush()
        assert log_file.exists()
        assert "写入探测 一条" in log_file.read_text(encoding="utf-8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
