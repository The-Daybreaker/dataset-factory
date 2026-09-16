"""接口测试：runs 端点（受理启动 / current 进度 / stop 停止 / stream SSE 事件流）。

受理端点（202 + 后台线程）与 SSE 流走 httpx ASGITransport——需要运行中的事件循环
（test_api_tasks 同款模式）；假客户端经 monkeypatch 注入 routes_runs 的装配接缝，
用「门」（threading.Event）控制跑批节奏；终态一律轮询断言（带超时护栏，绝不裸
sleep 赌调度）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from dataset_factory.api import create_app
from dataset_factory.api import routes_runs as routes_runs_module
from dataset_factory.llm import create_config
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.strategies import create_batch, set_batch_active
from dataset_factory.workdir import WorkdirRegistry, import_assets

_WAIT_TIMEOUT = 10.0


class GatedCompleter:
    """每轮 complete 先等门再回「打标结果」——测试用门控制跑批节奏（不真调 API）。"""

    def __init__(self, gates: list[threading.Event]) -> None:
        """以逐次等待的门序列初始化（耗尽后不再等待、直接成功）。"""
        self._gates = list(gates)
        self.calls = 0

    def complete(self, messages: object) -> str:
        """等本轮的门（若有）后返回固定文本。"""
        self.calls += 1
        gate = self._gates.pop(0) if self._gates else None
        if gate is not None:
            gate.wait(timeout=_WAIT_TIMEOUT)
        return "打标结果"

    def stream(self, messages: object) -> Iterator[object]:
        """批量跑批不该走流式路径——走到即测试失败。"""
        raise AssertionError("批量跑批不走流式路径")


@pytest.fixture
def batch_env(temp_data_root: Path, tmp_path: Path) -> tuple[Path, str]:
    """预置可跑批环境：端点 + 提示词 + 两张已登记素材 + 一个批次；返回 (工作目录, wid)。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(Prompt(name="详细描述", description="d", body="你是打标助手。"))
    workdir = tmp_path / "photos"
    workdir.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "cat_001.jpg").write_bytes(b"image-bytes-1")
    (source / "cat_002.jpg").write_bytes(b"image-bytes-2")
    import_assets(workdir, source)
    create_batch(
        workdir,
        name="一号批",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    entry = WorkdirRegistry.register(workdir, title="")
    return workdir, entry.id


def _inject_fake_completer(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    """把假客户端注入 routes_runs 的装配接缝（接缝在哪、桩在哪——memory 63②）。"""

    def _fake_assembly(_block: dict[str, Any]) -> object:
        return fake

    monkeypatch.setattr(routes_runs_module, "completer_for_snapshot", _fake_assembly)


def _gates(count: int) -> list[threading.Event]:
    """造 N 个未置位的门。"""
    return [threading.Event() for _ in range(count)]


async def _wait_current_404(http: httpx.AsyncClient, wid: str) -> None:
    """轮询 current 端点直到 404（运行结束、注册表清空；带超时护栏）。"""
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        response = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/current")
        if response.status_code == 404:
            return
        await asyncio.sleep(0.01)
    pytest.fail("current 未在超时内回到 404")


def test_start_run_accepts_and_completes(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受理 → 后台跑批完成：202 + run_id + Retry-After、产物落盘、current 回 404。"""
    workdir, wid = batch_env
    _inject_fake_completer(monkeypatch, GatedCompleter([]))  # 不设门：直接成功

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            accepted = await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            assert accepted.status_code == 202
            run_id = accepted.json()["run_id"]
            assert accepted.headers["retry-after"]

            await _wait_current_404(http, wid)
            assert (workdir / "s1__cat_001.txt").exists()
            assert (workdir / "s1__cat_002.txt").exists()
            run_json = (workdir / ".dsf" / "runs" / run_id / "run.json").read_text(
                encoding="utf-8"
            )
            assert '"status": "completed"' in run_json

    asyncio.run(scenario())


def test_start_run_rejects_second_run_with_409(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同工作目录已有运行（注册表同步登记）→ 409 problem+json（run-occupied + 占用者）。"""
    _, wid = batch_env
    gates = _gates(1)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            first = await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            assert first.status_code == 202

            # 注册表登记发生在受理线程内（POST 返回前），第二个请求必然命中。
            second = await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            assert second.status_code == 409
            body = second.json()
            assert body["type"] == "run-occupied"
            assert body["occupier"]["batch"] == "s1"

            gates[0].set()  # 放行让第一个运行收尾

    asyncio.run(scenario())


def test_current_run_reports_progress_then_404(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中 current 返回进度快照；结束后注册表清空 → 404（run-not-active）。"""
    _workdir, wid = batch_env
    gates = _gates(2)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            url = f"/api/workdirs/{wid}/batches/s1/runs/current"
            await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )

            running: dict[str, Any] | None = None
            deadline = time.monotonic() + _WAIT_TIMEOUT
            while time.monotonic() < deadline:
                probe = await http.get(url)
                body = probe.json() if probe.status_code == 200 else None
                # planned 在计划构建完成后才落镜像且运行全程稳定——用它排除
                # 「状态已 running、计划还没定」的窗口。
                if (
                    body is not None
                    and body["status"] == "running"
                    and body["counters"]["planned"] == 2
                ):
                    running = body
                    break
                await asyncio.sleep(0.01)
            assert running is not None, "current 未在超时内出现 running 快照"
            assert running["mode"] == "full"
            assert running["counters"]["planned"] == 2
            assert running["error"] is None

            gates[0].set()
            gates[1].set()

            await _wait_current_404(http, wid)
            missing = await http.get(url)
            assert missing.status_code == 404
            assert missing.json()["type"] == "run-not-active"

    asyncio.run(scenario())


def test_stop_run_interrupts(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop 置位协作取消：以 interrupted 收尾、剩余条目不打；无运行时 404。"""
    workdir, wid = batch_env
    gates = _gates(1)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            stop_url = f"/api/workdirs/{wid}/batches/s1/runs/stop"
            early = await http.post(stop_url)
            assert early.status_code == 404

            await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            # 等第一条素材真正进入处理中（current_item 就位 = 卡在门口等门），
            # 再请求停止并放行——「先停后放」消除与第二条的时序竞态。
            deadline = time.monotonic() + _WAIT_TIMEOUT
            while time.monotonic() < deadline:
                probe = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/current")
                if (
                    probe.status_code == 200
                    and probe.json()["current_item"] == "cat_001"
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("运行未在超时内进入第一条素材")

            stopped = await http.post(stop_url)
            assert stopped.status_code == 204
            gates[0].set()  # 放行第一条：完成后边界检查停止信号 → interrupted

            await _wait_current_404(http, wid)
            assert (workdir / "s1__cat_001.txt").exists()
            assert not (workdir / "s1__cat_002.txt").exists()

    asyncio.run(scenario())


def test_stream_delivers_events_until_finished(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE：订阅后按帧收业务事件，run-finished 后流关闭（帧结构同一期 /label/stream）。"""
    _workdir, wid = batch_env
    gates = _gates(2)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))
    frames: list[str] = []

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            await asyncio.sleep(0.2)  # 等运行持锁、进到第一条素材的门口

            async def collect() -> None:
                # SIM117：async with 两个上下文合并为一条（客户端 + 流式响应）。
                async with (
                    httpx.AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as stream_client,
                    stream_client.stream(
                        "GET", f"/api/workdirs/{wid}/batches/s1/runs/stream"
                    ) as response,
                ):
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith(
                        "text/event-stream"
                    )
                    async for chunk in response.aiter_text():
                        frames.append(chunk)

            collector = asyncio.ensure_future(collect())
            gates[0].set()
            gates[1].set()
            await asyncio.wait_for(collector, timeout=_WAIT_TIMEOUT)

    asyncio.run(scenario())

    text = "".join(frames)
    assert "event: item-updated" in text
    assert "event: run-finished" in text
    assert '"status": "completed"' in text
    # 帧结构：每帧 event + data 两行、空行分隔。
    for block in [block for block in text.split("\n\n") if block]:
        lines = block.splitlines()
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")


def test_start_run_inactive_batch_returns_409(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
) -> None:
    """停用批次受理 → 409 problem+json（batch-inactive），同步报错、不开后台任务。"""
    _, wid = batch_env
    set_batch_active(Path(WorkdirRegistry.get(wid).path), 1, False)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            response = await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            assert response.status_code == 409
            assert response.json()["type"] == "batch-inactive"

    asyncio.run(scenario())
