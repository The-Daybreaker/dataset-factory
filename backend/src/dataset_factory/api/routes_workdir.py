"""工作目录注册表与导入端点（二期新增）。

本模块承载：
- 注册表读面：GET /api/workdirs、GET /{wid}（两级下拉数据源与详情）；
- 登记：POST /api/workdirs（带 source = 复制导入，不带 = 就地采用）——同步完成
  注册表登记（wid 立即可用），扫描 + 复制 + 哈希登记作为长任务受理（202 + task_id
  + Retry-After，Google LRO 同构）；
- 补充导入：POST /{wid}/imports（任务句柄同上）；
- 导入历史：GET /{wid}/imports（``.dsf/imports.jsonl`` 全量，出身回看的数据源）；
- 素材原件：GET /{wid}/items/{item}/asset（只读预览，原生 Range 支持视频 seek）——
  素材是**工作目录级**资源、不属于任何批次（同一份素材被该目录下每个批次共享），
  故挂在这里而不是批次作用域的条目模块下。

错误一律 problem+json（api.problems）：404 wid 不在注册表、400 路径不合法、
422 来源与工作目录相同或互为嵌套。
"""

from __future__ import annotations

import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..runs.journal import load_recent_success_hashes
from ..strategies import get_batch, list_batches, parse_seq
from ..tasks import RETRY_AFTER_SECONDS, TaskManager, TaskResult
from ..workdir import (
    ImportInProgressError,
    WorkdirEntry,
    WorkdirPathError,
    WorkdirRegistry,
    WorkdirStore,
    ensure_importable_source,
    import_assets,
    mime_for_suffix,
    resolve_asset,
)
from ..workdir.integrity import IntegrityItem, rebuild_import_records, scan_integrity
from .schemas import (
    ImportAccepted,
    ImportRecord,
    Problem,
    WorkdirCreateAccepted,
    WorkdirCreateRequest,
    WorkdirImportRequest,
    WorkdirInfo,
)

router = APIRouter(prefix="/api/workdirs", tags=["工作目录"])


class BatchIntegrity(BaseModel):
    """一个批次的完整性结果（同一素材在不同批次的打标锚点独立）。"""

    batch: str
    items: list[IntegrityItem]


class IntegrityReport(BaseModel):
    """本次手动校验结果；缺少导入记录时显式提示可重建。"""

    checked_at: str
    imports_available: bool
    batches: list[BatchIntegrity]


@router.post(
    "/{wid}/integrity/scan",
    response_model=IntegrityReport,
    responses={404: {"model": Problem}},
)
def verify_integrity(wid: str, batch: str | None = None) -> IntegrityReport:
    """校验全部活跃批次，或通过 batch=sN 校验指定活跃批次；不写业务状态。"""
    workdir = Path(WorkdirRegistry.get(wid).path)
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    store = WorkdirStore(workdir)
    entries = (
        [get_batch(workdir, parse_seq(batch))]
        if batch is not None
        else list_batches(workdir)
    )
    return IntegrityReport(
        checked_at=datetime.now(UTC).isoformat(),
        imports_available=bool(store.read_import_records()),
        batches=[
            BatchIntegrity(
                batch=f"s{entry.seq}",
                items=scan_integrity(
                    workdir, load_recent_success_hashes(store.runs_dir, entry.seq)
                ),
            )
            for entry in entries
            if entry.active
        ],
    )


def _to_info(entry: WorkdirEntry) -> WorkdirInfo:
    """注册表条目 → 响应模型。"""
    return WorkdirInfo(
        id=entry.id,
        path=entry.path,
        title=entry.title,
        last_used_at=entry.last_used_at,
    )


def _manager(request: Request) -> TaskManager:
    """取应用级任务管理器（create_app 时装配到 app.state，随应用实例隔离）。"""
    return cast(TaskManager, request.app.state.task_manager)


def _spawn_import_task(
    request: Request,
    workdir: Path,
    source: Path | None,
    force_names: frozenset[str] | set[str] = frozenset(),
    *,
    rebuild: bool = False,
) -> str:
    """把一次导入包装成长任务受理，返回 task_id。

    同一工作目录**不允许多个导入任务并行**（并发双方基线互不可见，会破坏
    「主干唯一」不变量、imports.jsonl 可能交错）——槽位表做检查并预留
    （guard 锁内原子完成），占用中抛 ImportInProgressError（409）。
    任务体在 finally 里释放槽位（成败 / 取消都释放）。

    任务体 = 同步阻塞的 import_assets（跑在工作线程）；进度按文件粒度回报，
    取消信号在文件边界检查（任务体安全点约定）。
    """
    manager = _manager(request)
    key = os.path.realpath(workdir)
    guard = request.app.state.import_slots_guard
    slots: dict[str, str] = request.app.state.import_slots
    with guard:
        if key in slots:
            raise ImportInProgressError(
                "该工作目录已有导入任务在进行中——请等待其完成后再发起新导入。",
            )
        reserve = secrets.token_hex(6)
        slots[key] = reserve

    def runner(task_id: str, should_stop: threading.Event) -> TaskResult:
        """导入任务体：受理后把占位令牌换成 task_id；结束时释放槽位。"""
        try:
            with guard:
                if slots.get(key) == reserve:
                    slots[key] = task_id

            def report_progress(value: float) -> None:
                manager.set_progress(task_id, value)

            if rebuild:
                return rebuild_import_records(workdir, should_stop=should_stop)
            return import_assets(
                workdir,
                source,
                force_names=force_names,
                should_stop=should_stop,
                progress=report_progress,
            )
        finally:
            with guard:
                if slots.get(key) in (reserve, task_id):
                    del slots[key]

    try:
        return manager.create(runner)
    except BaseException:
        with guard:
            if slots.get(key) == reserve:
                del slots[key]
        raise


def _accepted_response(payload: WorkdirCreateAccepted | ImportAccepted) -> JSONResponse:
    """202 受理响应：带 Retry-After 头提示轮询间隔（Microsoft Graph 长动作同款）。"""
    return JSONResponse(
        status_code=202,
        content=payload.model_dump(),
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


@router.get("", response_model=list[WorkdirInfo])
def list_all() -> list[WorkdirInfo]:
    """列出全部登记的工作目录，按最后使用时间倒序（最近在前）。"""
    return [_to_info(entry) for entry in WorkdirRegistry.list_all()]


@router.post(
    "",
    status_code=202,
    response_model=WorkdirCreateAccepted,
    responses={
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "工作目录或来源目录路径不合法（problem+json: workdir-path-invalid）",
        },
        422: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "来源目录与工作目录相同或互为嵌套（problem+json: import-source-conflict）",
        },
    },
)
async def create_workdir(body: WorkdirCreateRequest, request: Request) -> JSONResponse:
    """登记工作目录并受理初始导入（长任务，202 + task_id + Retry-After）。

    校验在登记之前完成（失败的请求不在注册表留痕）；注册表登记同步完成
    （wid 立即可用、幂等——同 realpath 只更新已有条目），扫描与复制走任务。
    """
    workdir_path = Path(os.path.abspath(body.path))
    if not workdir_path.is_dir():
        raise WorkdirPathError(
            f"路径「{body.path}」不存在或不是目录——请检查后重试。",
        )
    source_path = Path(os.path.abspath(body.source)) if body.source else None
    if source_path is not None:
        ensure_importable_source(workdir_path, source_path)
    entry = WorkdirRegistry.register(workdir_path, title=body.title)
    task_id = _spawn_import_task(request, workdir_path, source_path)
    return _accepted_response(
        WorkdirCreateAccepted(task_id=task_id, workdir=_to_info(entry)),
    )


@router.get(
    "/{wid}",
    response_model=WorkdirInfo,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 不在注册表（problem+json: workdir-not-found）",
        },
    },
)
def get_one(wid: str) -> WorkdirInfo:
    """按 wid 查注册表条目。"""
    return _to_info(WorkdirRegistry.get(wid))


@router.get(
    "/{wid}/imports",
    response_model=list[ImportRecord],
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 不在注册表（problem+json: workdir-not-found）",
        },
    },
)
def list_imports(wid: str) -> list[ImportRecord]:
    """导入历史（追加序 = 时间正序）——素材出身回看的数据源。"""
    entry = WorkdirRegistry.get(wid)
    records = WorkdirStore(Path(entry.path)).read_import_records()
    return [ImportRecord.model_validate(record) for record in records]


@router.post(
    "/{wid}/imports",
    status_code=202,
    response_model=ImportAccepted,
    responses={
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "来源目录路径不合法（problem+json: workdir-path-invalid）",
        },
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 不在注册表（problem+json: workdir-not-found）",
        },
        422: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "来源目录与工作目录相同或互为嵌套（problem+json: import-source-conflict）",
        },
    },
)
async def create_import(
    wid: str, body: WorkdirImportRequest, request: Request
) -> JSONResponse:
    """补充导入（长任务，202 + task_id + Retry-After）。

    重新导入缺失素材也走本端点：来源指向原始目录，已登记的同名同容文件
    幂等跳过并重登记，缺失的补回。
    """
    entry = WorkdirRegistry.get(wid)
    workdir_path = Path(entry.path)
    source_path = Path(os.path.abspath(body.source))
    ensure_importable_source(workdir_path, source_path)
    task_id = _spawn_import_task(
        request,
        workdir_path,
        source_path,
        force_names=frozenset(body.force_names),
    )
    return _accepted_response(ImportAccepted(task_id=task_id))


class _AssetResponse(FileResponse):
    """FileResponse 的薄壳：把「这是二进制文件」写进**类属性**。

    FastAPI 生成契约时从 ``response_class`` 的类属性取媒体类型，而 FileResponse 的
    media_type 是构造时才定的（类上没有），于是文档会回落成默认的 application/json
    ——一个只吐文件的端点在契约里声称自己返回 JSON。这里补上类属性，契约就只列
    一种内容类型；实际响应的 Content-Type 仍按扩展名给（构造时传入，覆盖类属性）。
    """

    media_type = "application/octet-stream"


@router.get(
    "/{wid}/items/{item}/asset",
    response_class=_AssetResponse,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
            "description": "素材原件（Content-Type 按扩展名；带 accept-ranges: bytes，"
            "支持 Range 请求，视频可拖动进度条）",
        },
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "条目名不合法，或解析后越出工作目录（asset-path-invalid）",
        },
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 不在注册表，或素材缺失 / 未登记在册"
            "（workdir-not-found / asset-not-found）",
        },
    },
)
def get_item_asset(wid: str, item: str) -> _AssetResponse:
    """素材原件（只读预览）。

    三重校验分工：``WorkdirRegistry.get`` 管「wid 注册表存活」，``resolve_asset``
    管后两重——「在册」（没登记的文件不属于任何批次，预览端点不为它服务）与
    「realpath confine」（工作目录里的符号链接指向外部时拒绝，只读端点也不能
    变成读任意文件的通道）。

    交给 FileResponse 而不是自己读字节：它原生支持 Range（206 单段 / 多段、
    416 越界）与 ETag / Last-Modified，视频拖动进度条全靠这个；自己读整份字节
    会把 100 MiB 的视频整个塞进内存，还得手写一遍分段逻辑。不设
    ``content-disposition``——界面要在 ``<img>`` / ``<video>`` 里内联渲染，
    attachment 会让浏览器变成下载。
    """
    entry = WorkdirRegistry.get(wid)
    path = resolve_asset(Path(entry.path), item)
    return _AssetResponse(path, media_type=mime_for_suffix(path.suffix))


@router.post(
    "/{wid}/imports/rebuild",
    status_code=202,
    response_model=ImportAccepted,
    responses={404: {"model": Problem}},
)
async def rebuild_imports(wid: str, request: Request) -> JSONResponse:
    """重建导入记录（长任务）：扫现状、来源记空；旧记录保留（append-only）。"""
    entry = WorkdirRegistry.get(wid)
    workdir = Path(entry.path)
    if not workdir.is_dir():
        raise WorkdirPathError("工作目录不存在，请检查路径后重试。")
    task_id = _spawn_import_task(request, workdir, None, rebuild=True)
    return _accepted_response(ImportAccepted(task_id=task_id))
