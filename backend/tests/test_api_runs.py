"""接口测试：runs 端点（受理启动 / current 进度 / stop 停止 / stream SSE 事件流）。

受理端点（202 + 后台线程）与 SSE 流走 httpx ASGITransport——需要运行中的事件循环
（test_api_tasks 同款模式）；假客户端经 monkeypatch 注入 routes_runs 的装配接缝，
用「门」（threading.Event）控制跑批节奏；终态一律轮询断言（带超时护栏，绝不裸
sleep 赌调度）。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message, Scope

from dataset_factory.api import create_app
from dataset_factory.api import routes_runs as routes_runs_module
from dataset_factory.llm import StreamDelta, create_config
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.runs import (
    BatchRunner,
    RunEvent,
    RunJournal,
    add_retry_items,
    read_retry_list,
)
from dataset_factory.strategies import create_batch, set_batch_active
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets
from dataset_factory.workdir.locks import RunLock

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
        """等本轮的门（若有）后按流式产出固定文本（A2：跑批走流式）。"""
        self.calls += 1
        gate = self._gates.pop(0) if self._gates else None
        if gate is not None:
            gate.wait(timeout=_WAIT_TIMEOUT)
        return iter([StreamDelta(kind="content", text="打标结果")])


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
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
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


async def _wait_current_idle(http: httpx.AsyncClient, wid: str) -> None:
    """轮询 current 端点直到回到 200 + null（运行结束、注册表清空；带超时护栏）。"""
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        response = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/current")
        if response.status_code == 200 and response.json() is None:
            return
        await asyncio.sleep(0.01)
    pytest.fail("current 未在超时内回到空闲（200 + null）")


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

            await _wait_current_idle(http, wid)
            assert (workdir / "s1__cat_001.txt").exists()
            assert (workdir / "s1__cat_002.txt").exists()
            run_json = (workdir / ".dsf" / "runs" / run_id / "run.json").read_text(
                encoding="utf-8"
            )
            assert '"status": "completed"' in run_json
            history = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/latest")
            assert history.status_code == 200
            summary = history.json()
            assert summary["record"]["run_id"] == run_id
            assert summary["record"]["counters"]["succeeded"] == 2
            assert summary["log_path"] == str(
                workdir / ".dsf" / "runs" / run_id / "run.log"
            )
            logs = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/{run_id}/text")
            assert logs.status_code == 200
            assert "启动" in logs.json()["text"]
            items = await http.get(
                f"/api/workdirs/{wid}/batches/s1/runs/{run_id}/text?file=items.jsonl"
            )
            assert items.status_code == 200
            assert "cat_001" in items.json()["text"]

            record_path = workdir / ".dsf" / "runs" / run_id / "run.json"
            unfinished = json.loads(run_json)
            unfinished["status"] = "running"
            unfinished["finished_at"] = None
            record_path.write_text(json.dumps(unfinished), encoding="utf-8")
            recovered = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/latest")
            assert recovered.json()["record"]["status"] == "interrupted"
            assert recovered.json()["record"]["finished_at"] is None
            assert json.loads(record_path.read_text(encoding="utf-8")) == unfinished

    asyncio.run(scenario())


def test_start_failure_remains_readable_and_allows_next_run(
    batch_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """后台抢锁失败可在断流后读取原因，释放占用后再次启动可完成。"""
    workdir, wid = batch_env
    _inject_fake_completer(monkeypatch, GatedCompleter([]))
    lock = RunLock(WorkdirStore(workdir).dsf_path)
    lock.acquire({"batch": "s1", "pid": 123})

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            url = f"/api/workdirs/{wid}/batches/s1/runs"
            accepted = await http.post(url, json={"mode": "full"})
            assert accepted.status_code == 202
            deadline = time.monotonic() + _WAIT_TIMEOUT
            while time.monotonic() < deadline:
                progress = await http.get(f"{url}/current")
                if (
                    progress.status_code == 200
                    and progress.json()["status"] == "failed"
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("后台失败状态未保留")
            assert "占用" in progress.json()["error"]
            assert (await http.get(f"{url}/current")).json()["status"] == "failed"
            assert (await http.get(f"{url}/stream")).status_code == 404
            lock.release()
            assert (await http.post(url, json={"mode": "full"})).status_code == 202
            await _wait_current_idle(http, wid)

    try:
        asyncio.run(scenario())
    finally:
        lock.release()

    assert (workdir / "s1__cat_001.txt").exists()


def test_explicit_retry_api_preserves_other_retry_entries(
    batch_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """明确重打经真实 HTTP 只执行选中条目，不消费既有的其他重试意愿。"""
    workdir, wid = batch_env
    for stem in ("cat_001", "cat_002"):
        (workdir / f"s1__{stem}.txt").write_text("旧描述", encoding="utf-8")
    add_retry_items(workdir, 1, ["cat_002"])
    fake = GatedCompleter([])
    _inject_fake_completer(monkeypatch, fake)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            response = await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs",
                json={"mode": "retry", "items": ["cat_001"]},
            )
            assert response.status_code == 202
            await _wait_current_idle(http, wid)

    asyncio.run(scenario())

    assert fake.calls == 1
    assert (workdir / "s1__cat_001.txt").read_text(encoding="utf-8") == "打标结果"
    assert (workdir / "s1__cat_002.txt").read_text(encoding="utf-8") == "旧描述"
    assert read_retry_list(workdir, 1) == ["cat_002"]


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "full", "items": ["cat_001"]},
        {"mode": "retry", "items": []},
        {"mode": "retry", "items": ["not-imported"]},
        {"mode": "retry", "items": ["cat_001"]},
    ],
)
def test_explicit_retry_api_rejects_invalid_selection(
    batch_env: tuple[Path, str], body: dict[str, object]
) -> None:
    """空列表、模式冲突、未登记及排队中素材均在受理前拒绝，名单不变。"""
    workdir, wid = batch_env
    with TestClient(create_app(frontend_dir=Path("no-dist"))) as client:
        response = client.post(f"/api/workdirs/{wid}/batches/s1/runs", json=body)

    assert response.status_code == 422
    assert read_retry_list(workdir, 1) == []


def test_run_history_empty_and_missing_batch(batch_env: tuple[Path, str]) -> None:
    """尚未跑批的空摘要与批次不存在的 404 分开表达。"""
    _, wid = batch_env
    with TestClient(create_app(frontend_dir=Path("no-dist"))) as client:
        response = client.get(f"/api/workdirs/{wid}/batches/s1/runs/latest")
        assert response.status_code == 200
        assert response.json() == {"record": None, "log_path": None, "items_path": None}
        assert (
            client.get(f"/api/workdirs/{wid}/batches/s99/runs/latest").status_code
            == 404
        )


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
            await _wait_current_idle(http, wid)  # 等收尾，不留后台线程与 pytest 抢目录

    asyncio.run(scenario())


def test_current_run_rejects_wrong_batch(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批次归属校验（审计 P2 回归）：s1 在跑时，s2 的 current / stop / stream 都 404。

    URL 是批次作用域——s2 的请求不能命中 s1 的运行（跨批次误停 / 进度张冠李戴）。
    """
    from dataset_factory.strategies import create_batch

    workdir, wid = batch_env
    create_batch(
        workdir,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    gates = _gates(1)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            await asyncio.sleep(0.2)  # 等运行持锁、卡到门口

            other = f"/api/workdirs/{wid}/batches/s2/runs"
            other_current = await http.get(f"{other}/current")
            # current 的「空闲」语义是 200 + null（404 只留给 wid / 批次不存在）；
            # 跨批次校验的要点是**不能命中 s1 的运行**——null 即「s2 没有自己的运行」。
            assert other_current.status_code == 200
            assert other_current.json() is None
            assert (await http.post(f"{other}/stop")).status_code == 404
            stream = await http.get(f"{other}/stream")
            assert stream.status_code == 404
            assert stream.json()["type"] == "run-not-active"

            # 本批次照常可见、可停（对照）。
            own = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/current")
            assert own.status_code == 200
            assert own.json()["batch"] == 1

            gates[0].set()
            await _wait_current_idle(http, wid)

    asyncio.run(scenario())


def test_hide_batch_interrupts_running_run(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停用批次中断运行（design 定案接线，审计 P2 回归）：hide 后运行以 interrupted 收尾。"""
    workdir, wid = batch_env
    gates = _gates(1)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=create_app(frontend_dir=Path("no-dist")))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            await http.post(
                f"/api/workdirs/{wid}/batches/s1/runs", json={"mode": "full"}
            )
            # 等第一条素材真正进入处理中（current_item 就位 = 卡在门口等门）。
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

            hidden = await http.post(f"/api/workdirs/{wid}/batches/s1/hide")
            assert hidden.status_code == 200  # 停用成功且已请求中断运行
            gates[0].set()  # 放行当前条目：完成后边界检查停止信号 → interrupted

            await _wait_current_idle(http, wid)
            assert (workdir / "s1__cat_001.txt").exists()
            assert not (workdir / "s1__cat_002.txt").exists()

    asyncio.run(scenario())


def test_current_run_reports_progress_then_404(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中 current 返回进度快照；结束后注册表清空 → 200 + null（空闲语义）。"""
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

            # current 镜像在「计划算完」的一刻就报 running，磁盘 run.json 紧随其后落盘
            # （两条路径无同刻保证，也不需要有——UI 的活动态读 current、latest 只供切批次
            # 时回读存量）。轮询到记录落盘，再校验它经 routes_runs 的对账分支后与 current
            # 口径一致：轮询把「记录尚未刷盘」这一合法瞬态排除，断言仍锁住对账逻辑。
            rec: dict[str, Any] | None = None
            deadline = time.monotonic() + _WAIT_TIMEOUT
            while time.monotonic() < deadline:
                history = await http.get(f"/api/workdirs/{wid}/batches/s1/runs/latest")
                assert history.status_code == 200
                if history.json()["record"] is not None:
                    rec = history.json()["record"]
                    break
                await asyncio.sleep(0.01)
            assert rec is not None, "latest 未在超时内出现运行记录"
            assert rec["run_id"] == running["run_id"]
            assert rec["status"] == "running"
            assert rec["counters"] == running["counters"]

            gates[0].set()
            gates[1].set()

            await _wait_current_idle(http, wid)
            missing = await http.get(url)
            # 空闲语义 = 200 + null（L3，2026-09-21 起）；404 只留给 wid / 批次不存在。
            assert missing.status_code == 200
            assert missing.json() is None

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

            await _wait_current_idle(http, wid)
            assert (workdir / "s1__cat_001.txt").exists()
            assert not (workdir / "s1__cat_002.txt").exists()

    asyncio.run(scenario())


def test_stream_delivers_events_until_finished(
    temp_data_root: Path,
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务端实际订阅后放行素材处理，SSE 有序传递条目事件并在终态关流。"""
    _workdir, wid = batch_env
    gates = _gates(2)
    _inject_fake_completer(monkeypatch, GatedCompleter(gates))
    frames: list[str] = []
    connected = threading.Event()
    original_subscribe = BatchRunner.subscribe

    def subscribe(
        self: BatchRunner, callback: Callable[[RunEvent], None]
    ) -> Callable[[], None]:
        unsubscribe = original_subscribe(self, callback)
        connected.set()
        return unsubscribe

    monkeypatch.setattr(BatchRunner, "subscribe", subscribe)

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
            try:
                assert await asyncio.to_thread(connected.wait, _WAIT_TIMEOUT)
            finally:
                gates[0].set()
                gates[1].set()
            await asyncio.wait_for(collector, timeout=_WAIT_TIMEOUT)

    asyncio.run(scenario())

    text = "".join(frames)
    assert "event: item-updated" in text
    assert "event: run-finished" in text
    assert '"status": "completed"' in text
    assert text.rstrip().split("\n\n")[-1].startswith("event: run-finished\n")
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


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_idle_stream_disconnect_releases_subscription(
    batch_env: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    spec_version: str,
) -> None:
    """没有新业务事件时断连也会释放订阅，不停止后台跑批。"""
    workdir, wid = batch_env
    runner = BatchRunner(workdir, 1, GatedCompleter([]), mode="full", trigger="web")
    subscribed = threading.Event()
    released = threading.Event()
    original_subscribe = BatchRunner.subscribe

    def subscribe(
        self: BatchRunner, callback: Callable[[RunEvent], None]
    ) -> Callable[[], None]:
        unsubscribe = original_subscribe(self, callback)
        subscribed.set()

        def release() -> None:
            unsubscribe()
            released.set()

        return release

    monkeypatch.setattr(BatchRunner, "subscribe", subscribe)

    async def scenario() -> None:
        app = create_app(frontend_dir=Path("no-dist"))
        app.state.run_registry[str(workdir)] = runner
        disconnected = asyncio.Event()
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec_version},
            "app": app,
        }

        async def receive() -> Message:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if disconnected.is_set():
                raise OSError("client disconnected")

        request = Request(scope, receive)
        response = routes_runs_module.stream_run(wid, "s1", request)
        stream = asyncio.create_task(response(scope, receive, send))
        try:
            assert await asyncio.to_thread(subscribed.wait, 2)
            disconnected.set()
            assert await asyncio.to_thread(released.wait, 2)
            if spec_version == "2.4":
                with pytest.raises(ClientDisconnect):
                    await asyncio.wait_for(stream, 2)
            else:
                await asyncio.wait_for(stream, 2)
            assert runner.snapshot()["status"] == "pending"
        finally:
            runner.run()
            if not stream.done():
                stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# 重试列表端点（两段式的前半段：「加入重试」只攒名单；「开始重试」= 上面的
# POST runs mode=retry。名单存取经 mutate_state 在状态锁内完成）
# --------------------------------------------------------------------------


@pytest.fixture
def retry_client(batch_env: tuple[Path, str], tmp_path: Path) -> TestClient:
    """重试列表端点的同步客户端（TestClient 不进 with、不触发 lifespan 播种）。"""
    return TestClient(create_app(frontend_dir=tmp_path))


def _mark_done(workdir: Path, stem: str) -> None:
    """造一个「已完成」条目：产物 txt 存在且非空白（视图的判定数据源）。"""
    (workdir / f"s1__{stem}.txt").write_text("已完成的描述", encoding="utf-8")


def _mark_failed(workdir: Path, stem: str, reason_code: str) -> None:
    """造一个「未完成」条目：最近一次流水是失败（原因码决定可不可重试）。"""
    journal = RunJournal(WorkdirStore(workdir).runs_dir / "run-retry-seed")
    journal.append_item(
        {
            "batch": 1,
            "item": stem,
            "status": "failed",
            "attempt": 1,
            "reason_code": reason_code,
            "message": "测试失败原因",
            "elapsed_ms": 100,
        }
    )


def _import_then_delete_asset(workdir: Path, tmp_path: Path, name: str) -> None:
    """造一个「缺失」条目：先真导入（登记在册）再把盘上的素材删掉。"""
    extra = tmp_path / f"source-{name}"
    extra.mkdir()
    (extra / name).write_bytes(f"bytes-of-{name}".encode())
    import_assets(workdir, extra)
    (workdir / name).unlink()


def test_add_retry_list_accepts_eligible_items(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """已完成 + 可重试失败入列：200 返回全量名单，视图带「已排重试」标记。"""
    workdir, wid = batch_env
    _mark_done(workdir, "cat_001")
    _mark_failed(workdir, "cat_002", "network")  # network 在可重试原因码清单里

    added = retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list",
        json={"items": ["cat_001", "cat_002"]},
    )

    assert added.status_code == 200
    assert added.json() == {"id": "s1", "seq": 1, "items": ["cat_001", "cat_002"]}

    view = retry_client.get(f"/api/workdirs/{wid}/batches/s1/items").json()
    assert [row["item"] for row in view["groups"]["retry"]] == ["cat_001", "cat_002"]
    done_row = next(row for row in view["groups"]["done"] if row["item"] == "cat_001")
    assert done_row["in_retry"] is True
    failed_row = next(
        row for row in view["groups"]["failed"] if row["item"] == "cat_002"
    )
    assert failed_row["in_retry"] is True


def test_add_retry_list_is_idempotent(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """重复加入不产生重复条目（幂等去重）。"""
    workdir, wid = batch_env
    _mark_done(workdir, "cat_001")

    first = retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list", json={"items": ["cat_001"]}
    )
    second = retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list",
        json={"items": ["cat_001", "cat_001"]},
    )

    assert first.json()["items"] == ["cat_001"]
    assert second.json()["items"] == ["cat_001"]


def test_add_retry_list_rejects_ineligible_items(
    batch_env: tuple[Path, str], retry_client: TestClient, tmp_path: Path
) -> None:
    """不可入列条目（不可重试失败 / 排队中 / 缺失 / 未知）整体拒绝 + 逐条原因，名单不动。"""
    workdir, wid = batch_env
    _mark_failed(workdir, "cat_001", "asset-unreadable")  # 不可重试类失败
    # cat_002 不动 = 排队中
    _import_then_delete_asset(workdir, tmp_path, "cat_003.jpg")  # 缺失

    response = retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list",
        json={"items": ["cat_001", "cat_002", "cat_003", "ghost"]},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "retry-item-not-eligible"
    assert body["rejections"]["cat_001"].startswith("该失败类型不可自动重试")
    assert body["rejections"]["cat_002"].startswith("排队中的条目无需重试")
    assert body["rejections"]["cat_003"] == "素材缺失——先补回素材才能重打"
    assert body["rejections"]["ghost"] == "不是本批次的条目（未导入或不存在）"

    view = retry_client.get(f"/api/workdirs/{wid}/batches/s1/items").json()
    assert view["groups"]["retry"] == []  # 整体拒绝：没有任何条目进名单


def test_add_retry_list_unknown_batch_404(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """批次不存在 → 404 problem+json（batch-not-found）。"""
    _, wid = batch_env

    response = retry_client.post(
        f"/api/workdirs/{wid}/batches/s99/retry-list", json={"items": ["cat_001"]}
    )

    assert response.status_code == 404
    assert response.json()["type"] == "batch-not-found"


def test_remove_retry_list_item(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """逐条移出：名单更新、其余条目不动。"""
    workdir, wid = batch_env
    _mark_done(workdir, "cat_001")
    _mark_done(workdir, "cat_002")
    retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list",
        json={"items": ["cat_001", "cat_002"]},
    )

    removed = retry_client.delete(f"/api/workdirs/{wid}/batches/s1/retry-list/cat_001")

    assert removed.status_code == 200
    assert removed.json()["items"] == ["cat_002"]


def test_remove_retry_list_item_idempotent(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """移出不在名单里的条目：原样返回当前名单（不报错——移出是撤销意愿）。"""
    workdir, wid = batch_env
    _mark_done(workdir, "cat_001")
    retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list", json={"items": ["cat_001"]}
    )

    response = retry_client.delete(f"/api/workdirs/{wid}/batches/s1/retry-list/ghost")

    assert response.status_code == 200
    assert response.json()["items"] == ["cat_001"]


def test_clear_retry_list_only_clears_this_batch(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """整体清空只清本批次：另一批次的名单原样保留（名单共享一份、按批次隔离）。"""
    workdir, wid = batch_env
    create_batch(
        workdir,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    _mark_done(workdir, "cat_001")
    retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list", json={"items": ["cat_001"]}
    )
    # s2 的名单直接经域函数搭景（cat_001 在 s2 是排队中——名单是意愿、不判资格）。
    add_retry_items(workdir, 2, ["cat_001", "cat_002"])

    cleared = retry_client.delete(f"/api/workdirs/{wid}/batches/s1/retry-list")

    assert cleared.status_code == 200
    assert cleared.json() == {"id": "s1", "seq": 1, "items": []}
    assert read_retry_list(workdir, 2) == ["cat_001", "cat_002"]  # s2 不受影响


def test_retry_list_delete_endpoints_unknown_batch_404(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """DELETE 两端点对不存在的批次同样 404（契约声明与 POST 一致）。"""
    _, wid = batch_env

    removed = retry_client.delete(f"/api/workdirs/{wid}/batches/s99/retry-list/cat_001")
    cleared = retry_client.delete(f"/api/workdirs/{wid}/batches/s99/retry-list")

    assert removed.status_code == 404
    assert removed.json()["type"] == "batch-not-found"
    assert cleared.status_code == 404
    assert cleared.json()["type"] == "batch-not-found"


def test_add_retry_list_skips_items_already_listed(
    batch_env: tuple[Path, str], retry_client: TestClient, tmp_path: Path
) -> None:
    """已在名单里的条目不重复判资格：入列后素材缺失，重复加入仍幂等受理。"""
    workdir, wid = batch_env
    _mark_done(workdir, "cat_001")
    retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list", json={"items": ["cat_001"]}
    )
    (workdir / "cat_001.jpg").unlink()  # 素材随后缺失（名单是意愿、不回滚）

    again = retry_client.post(
        f"/api/workdirs/{wid}/batches/s1/retry-list", json={"items": ["cat_001"]}
    )

    assert again.status_code == 200
    assert again.json()["items"] == ["cat_001"]


def test_delete_batch_clears_its_retry_records(
    batch_env: tuple[Path, str], retry_client: TestClient
) -> None:
    """删除批次连带出清该批次的重试名单（其他批次的名单不动）。"""
    workdir, wid = batch_env
    create_batch(
        workdir,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    _mark_done(workdir, "cat_001")
    add_retry_items(workdir, 1, ["cat_001", "cat_002"])
    add_retry_items(workdir, 2, ["cat_001"])

    deleted = retry_client.delete(f"/api/workdirs/{wid}/batches/s1")

    assert deleted.status_code == 204
    assert read_retry_list(workdir, 1) == []  # s1 的名单随批次出清
    assert read_retry_list(workdir, 2) == ["cat_001"]  # s2 不受影响
