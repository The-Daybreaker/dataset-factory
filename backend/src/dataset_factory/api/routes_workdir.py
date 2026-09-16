"""工作目录注册表与导入端点（二期新增）。

本模块承载：
- 注册表读面：GET /api/workdirs、GET /{wid}（两级下拉数据源与详情）；
- 登记：POST /api/workdirs（带 source = 复制导入，不带 = 就地采用）——同步完成
  注册表登记（wid 立即可用），扫描 + 复制 + 哈希登记作为长任务受理（202 + task_id
  + Retry-After，Google LRO 同构）；
- 补充导入：POST /{wid}/imports（任务句柄同上）；
- 导入历史：GET /{wid}/imports（``.dsf/imports.jsonl`` 全量，出身回看的数据源）。

错误一律 problem+json（api.problems）：404 wid 不在注册表、400 路径不合法、
422 来源与工作目录相同或互为嵌套。
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..tasks import RETRY_AFTER_SECONDS, TaskManager, TaskResult
from ..workdir import (
    ImportInProgressError,
    WorkdirEntry,
    WorkdirPathError,
    WorkdirRegistry,
    WorkdirStore,
    ensure_importable_source,
    import_assets,
)
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
