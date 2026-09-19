"""跑批端点：POST runs（受理启动）、GET runs/current、POST runs/stop、GET runs/stream（SSE）。

执行模型：POST 同步做预检（批次存在且活跃 / 快照可读 / 客户端可装配）后受理——
构造 BatchRunner 登记进应用级注册表（按工作目录 realpath 键控）、开后台线程跑、
立即返回 202 + run_id。真正的运行锁在后台线程里抢（「锁归调度线程」纪律不变；
跨进程并发由磁盘锁兜底——线程里抢锁失败的失败原因经 SSE / current 呈现）。

- current：运行中或最近失败的进度快照；无运行且无失败快照 → 404。
- stop：找到运行中 runner 置位协作取消信号即返回（停止是异步的——当前条目跑完
  本轮尝试后在条目边界停下）；没有运行 → 404。
- stream：订阅该 runner 的业务事件转 SSE 帧（与一期 /label/stream 帧同构），
  run-finished 后关流；没有运行 → 404（前端断线约定：先全量拉条目视图刷新界面
  再续流收增量，见 design「后端端点清单」横切约定）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..runs import (
    BatchInactiveError,
    BatchRunner,
    RetryItemNotEligibleError,
    RunEvent,
    RunFinishedEvent,
    RunNotActiveError,
    add_retry_items,
    clear_retry_list,
    completer_for_snapshot,
    remove_retry_items,
    retry_rejections,
)
from ..runs.control import current_run as read_current_run
from ..runs.control import request_stop
from ..runs.journal import RunCounters, RunRecord, load_latest_run, read_run_text
from ..strategies import get_batch, parse_seq, read_snapshot
from ..tasks import RETRY_AFTER_SECONDS
from ..workdir import RunOccupiedError
from .deps import workdir_root
from .problems import problem
from .schemas import (
    Problem,
    RetryListRequest,
    RetryListView,
    RunAccepted,
    RunStartRequest,
    RunStatusView,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workdirs/{wid}/batches/{sN}/runs", tags=["跑批"])


class RunHistoryView(BaseModel):
    """最近运行的磁盘摘要及可定位的日志绝对路径。"""

    record: RunRecord | None
    log_path: str | None
    items_path: str | None


class RunTextView(BaseModel):
    """指定运行的只读文件内容与绝对路径。"""

    path: str
    text: str


@router.get(
    "/latest", response_model=RunHistoryView, responses={404: {"model": Problem}}
)
def latest_run(wid: str, sN: str, request: Request) -> RunHistoryView:
    """按批次回读最近一次磁盘记录；尚无运行时返回空摘要。"""
    workdir = workdir_root(wid)
    seq = parse_seq(sN)
    get_batch(workdir, seq)
    runs_dir = workdir / ".dsf" / "runs"
    record = load_latest_run(runs_dir, seq)
    if record is not None and record.status == "running":
        try:
            progress = current_run(wid, sN, request)
        except RunNotActiveError:
            # 活性检查期间可能刚好收尾，先回读终态再判断是否异常中断。
            record = load_latest_run(runs_dir, seq)
            if record is not None and record.status == "running":
                record = record.model_copy(update={"status": "interrupted"})
        else:
            if progress.run_id == record.run_id:
                record = record.model_copy(
                    update={
                        "status": progress.status,
                        "counters": RunCounters.model_validate(progress.counters),
                    }
                )
            else:
                record = record.model_copy(update={"status": "interrupted"})
    directory = runs_dir / record.run_id if record else None
    return RunHistoryView(
        record=record,
        log_path=str(directory / "run.log") if directory else None,
        items_path=str(directory / "items.jsonl") if directory else None,
    )


@router.get(
    "/{run_id}/text", response_model=RunTextView, responses={404: {"model": Problem}}
)
def run_text(
    wid: str, sN: str, run_id: str, file: Literal["run.log", "items.jsonl"] = "run.log"
) -> RunTextView:
    """指定运行查看日志，打开后不因新运行出现而切换文件。"""
    workdir = workdir_root(wid)
    seq = parse_seq(sN)
    get_batch(workdir, seq)
    runs_dir = workdir / ".dsf" / "runs"
    text = read_run_text(runs_dir, run_id, seq, file)
    return RunTextView(path=str(runs_dir / run_id / file), text=text)


def _registry(request: Request) -> tuple[dict[str, BatchRunner], threading.Lock]:
    """取应用级运行注册表与其守卫锁（create_app 装配，随应用实例隔离）。"""
    return (
        cast("dict[str, BatchRunner]", request.app.state.run_registry),
        cast(threading.Lock, request.app.state.run_registry_guard),
    )


def _active_runner(request: Request, workdir: Path, seq: int) -> BatchRunner:
    """查该工作目录的当前运行并校验批次归属；无运行 / 批次不符 → 404。

    批次校验不可省：URL 是批次作用域（/batches/{sN}/runs/...），而注册表按工作
    目录键控（同一时刻只有一个运行）——s2 的 stop / current 不能命中 s1 的运行，
    那是「跨批次误停 / 进度张冠李戴」。
    """
    registry, guard = _registry(request)
    with guard:
        runner = registry.get(str(workdir))
    if runner is None or runner.snapshot()["batch"] != seq:
        raise RunNotActiveError("该批次当前没有进行中的跑批——启动一次跑批后再试。")
    return runner


def _require_active(runner: BatchRunner) -> BatchRunner:
    """对 stop / stream 的额外校验：运行已到终态（尚未被线程移出注册表的窗口）按 404 处理。

    此时 run-finished 已发过，订阅它只会挂死等不到帧。
    """
    if runner.snapshot()["status"] in {"completed", "interrupted", "failed"}:
        raise RunNotActiveError("该批次当前没有进行中的跑批——启动一次跑批后再试。")
    return runner


@router.post(
    "",
    status_code=202,
    response_model=RunAccepted,
    responses={
        404: problem("wid / 批次 / 快照不存在（problem+json）"),
        409: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "批次已停用或工作目录已有跑批在运行"
            "（problem+json: batch-inactive / run-occupied）",
        },
    },
)
def start_run(
    wid: str, sN: str, body: RunStartRequest, request: Request
) -> JSONResponse:
    """受理一次跑批：同步预检 + 登记注册表 + 后台线程执行，202 立即返回。

    预检在受理之前完成（批次存在 / 活跃 / 快照可读 / 客户端可装配），把能在
    请求内确定的错误同步报给客户端；真正的运行锁在后台线程里抢——跨进程占用
    （如 CLI 正在跑同一工作目录）在受理后才暴露，失败原因经 SSE / current 呈现
    （进度快照 status=failed + 一条 failed 的 run-finished 事件）。
    """
    workdir = workdir_root(wid)
    seq = parse_seq(sN)
    entry = get_batch(workdir, seq)
    if not entry.active:
        raise BatchInactiveError(
            f"批次 s{seq} 已停用（隐藏）——请先「显示」该批次再跑批。"
        )
    snapshot = read_snapshot(workdir, seq)
    if body.items is not None:
        rejections = retry_rejections(workdir, seq, body.items)
        if rejections:
            raise RetryItemNotEligibleError(
                "选中条目中存在不可重打的素材。", rejections=rejections
            )
    completer = completer_for_snapshot(snapshot.endpoint)

    registry, guard = _registry(request)
    key = str(workdir)
    runner = BatchRunner(
        workdir, seq, completer, mode=body.mode, trigger="web", retry_items=body.items
    )
    with guard:
        existing = registry.get(key)
        if existing is not None and existing.snapshot()["status"] != "failed":
            raise RunOccupiedError(
                "该工作目录已有跑批在运行——等它结束或停止后再试。",
                occupier={"pid": os.getpid(), "batch": f"s{seq}"},
            )
        registry[key] = runner

    run_id = runner.snapshot()["run_id"]

    def thread_body() -> None:
        try:
            runner.run()
        except Exception:
            # 失败已记进度快照并发事件（BLE001 对「记日志后收场」的宽捕获不报警），
            # 线程干净收场——受理已 202，错误只能在 SSE / current 呈现。
            logger.warning("跑批 %s 失败（已通知订阅者）", run_id, exc_info=True)
        finally:
            with guard:
                if (
                    registry.get(key) is runner
                    and runner.snapshot()["status"] != "failed"
                ):
                    del registry[key]

    threading.Thread(target=thread_body, name=f"dsf-run-{run_id}", daemon=True).start()
    return JSONResponse(
        status_code=202,
        content=RunAccepted(run_id=run_id).model_dump(),
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


@router.get(
    "/current",
    response_model=RunStatusView,
    responses={
        404: problem("该批次当前没有进行中的跑批（problem+json: run-not-active）"),
    },
)
def current_run(wid: str, sN: str, request: Request) -> RunStatusView:
    """当前运行进度快照（轮询用；SSE 断线重连后的全量刷新同款数据）。"""
    seq = parse_seq(sN)  # sN 不合法按批次不存在处理（与 batch 端点同口径）
    workdir = workdir_root(wid)
    try:
        runner = _active_runner(request, workdir, seq)
    except RunNotActiveError:
        return RunStatusView(**read_current_run(workdir, seq))
    else:
        if runner.snapshot()["status"] == "failed":
            try:
                return RunStatusView(**read_current_run(workdir, seq))
            except RunNotActiveError:
                pass
        return RunStatusView(**runner.snapshot())


@router.post(
    "/stop",
    status_code=204,
    responses={
        404: problem("该批次当前没有进行中的跑批（problem+json: run-not-active）"),
    },
)
def stop_run(wid: str, sN: str, request: Request) -> Response:
    """请求停止当前跑批（协作取消）：置位信号即返回，当前条目在安全点停下。"""
    seq = parse_seq(sN)
    workdir = workdir_root(wid)
    try:
        runner = _active_runner(request, workdir, seq)
    except RunNotActiveError:
        request_stop(workdir, seq)
    else:
        if runner.snapshot()["status"] == "failed":
            request_stop(workdir, seq)
        else:
            _require_active(runner).stop()
    return Response(status_code=204)


@router.get(
    "/stream",
    responses={
        404: problem("该批次当前没有进行中的跑批（problem+json: run-not-active）"),
    },
)
def stream_run(wid: str, sN: str, request: Request) -> StreamingResponse:
    """SSE 业务事件流：run-started → item-updated… → run-finished（收到终态即关流）。

    订阅之前已发生的事件不补发（前端断线约定：重连先全量拉条目视图刷新界面）；
    事件经线程安全队列从跑批线程转发到流。
    """
    seq = parse_seq(sN)
    runner = _require_active(_active_runner(request, workdir_root(wid), seq))
    events: queue.Queue[RunEvent | None] = queue.Queue()

    def _forward(event: RunEvent) -> None:
        events.put(event)
        if isinstance(event, RunFinishedEvent):
            events.put(None)  # 终态哨兵：关流

    async def generate() -> AsyncIterator[str]:
        unsubscribe = runner.subscribe(_forward)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    # 模型等待期间保持可取消，不占用线程池等待下一条业务事件。
                    await asyncio.sleep(0.1)
                    continue
                if event is None:
                    break
                yield _sse(event.kind, event.to_payload())
        finally:
            unsubscribe()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict[str, object]) -> str:
    """一条 SSE 帧（event + data 两行）；JSON 不转义中文，与一期 /label/stream 同构。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# -- 重试列表（两段式界面的前半段：「加入重试」只攒名单，「开始重试」走上面的
#    POST runs mode=retry——名单在运行开始的瞬间拍快照，运行期编辑只影响下一次） ---

retry_router = APIRouter(
    prefix="/api/workdirs/{wid}/batches/{sN}/retry-list", tags=["跑批"]
)

_PROBLEM_404_BATCH: dict[int | str, dict[str, Any]] = {
    404: problem("wid 或批次不存在（workdir-not-found / batch-not-found）"),
}

_PROBLEM_422_NOT_ELIGIBLE: dict[int | str, dict[str, Any]] = {
    422: {
        "model": Problem,
        "content": {"application/problem+json": {}},
        "description": (
            "有不可入列的条目（problem+json: retry-item-not-eligible），"
            "rejections 扩展字段带逐条原因；整体拒绝、不做部分入列"
        ),
    },
}


@retry_router.post(
    "",
    response_model=RetryListView,
    responses={**_PROBLEM_404_BATCH, **_PROBLEM_422_NOT_ELIGIBLE},
)
def add_batch_retry_list(wid: str, sN: str, body: RetryListRequest) -> RetryListView:
    """把条目加入重试列表（幂等去重），返回当前名单（名单顺序即重试顺序）。

    只收「已完成」与「可重试的未完成」条目（PRD F7）——排队中无需重试、缺失要
    先补素材、不可重试失败要先解决格式问题；资格用当刻的条目视图现判。改动经
    mutate_state 在状态锁内完成；运行期写入照常受理（本次运行按启动时的快照执行）。
    """
    workdir = workdir_root(wid)
    seq = parse_seq(sN)
    rejections = retry_rejections(workdir, seq, body.items)
    if rejections:
        raise RetryItemNotEligibleError(
            "有 "
            + str(len(rejections))
            + " 个条目不可加入重试列表（已完成与可重试的未完成条目才可入列）。",
            rejections=rejections,
        )
    items = add_retry_items(workdir, seq, body.items)
    return RetryListView(id=f"s{seq}", seq=seq, items=items)


@retry_router.delete(
    "/{item}",
    response_model=RetryListView,
    responses=_PROBLEM_404_BATCH,
)
def remove_batch_retry_list_item(wid: str, sN: str, item: str) -> RetryListView:
    """把一个条目移出重试列表（幂等：不在名单里时原样返回），返回当前名单。"""
    workdir = workdir_root(wid)
    seq = parse_seq(sN)
    get_batch(workdir, seq)  # 批次不存在当场 404（契约声明与 POST 一致）
    items = remove_retry_items(workdir, seq, [item])
    return RetryListView(id=f"s{seq}", seq=seq, items=items)


@retry_router.delete(
    "",
    response_model=RetryListView,
    responses=_PROBLEM_404_BATCH,
)
def clear_batch_retry_list(wid: str, sN: str) -> RetryListView:
    """整体清空本批次的重试列表（其他批次的名单不动），返回空名单。"""
    workdir = workdir_root(wid)
    seq = parse_seq(sN)
    get_batch(workdir, seq)  # 批次不存在当场 404（契约声明与 POST 一致）
    clear_retry_list(workdir, seq)
    return RetryListView(id=f"s{seq}", seq=seq, items=[])
