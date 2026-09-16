"""当前批次导出：计划、后台任务与已完成交付物下载。"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import asdict
from pathlib import Path
from threading import Event
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from ..export import ExportError, ExportPlan, build_export_plan, write_export
from ..runs import BatchInactiveError
from ..runs.journal import load_latest_item_records, load_recent_success_hashes
from ..strategies import get_batch, parse_seq
from ..strategies.batches import read_exclusions
from ..tasks import RETRY_AFTER_SECONDS, TaskManager, TaskResult
from ..workdir import AssetNotFoundError, WorkdirRegistry, WorkdirStore
from ..workdir.assets import confine_to_workdir
from ..workdir.locks import RunLock, import_guard
from .schemas import ExportAccepted, ExportPlanView, ExportStartRequest, Problem

router = APIRouter(
    prefix="/api/workdirs/{wid}/export",
    tags=["导出"],
    responses={
        code: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": description,
        }
        for code, description in (
            (400, "导出参数或文件路径不合法"),
            (404, "工作目录、批次或交付文件不存在"),
            (409, "批次已停用"),
            (500, "工作目录元数据或运行流水损坏"),
        )
    },
)


def _plan(
    workdir: Path, seq: int, sequential: bool, should_stop: Event | None = None
) -> ExportPlan:
    """入口编排汇合批次、排除名单与运行流水，导出核心只消费明确输入。"""
    if not get_batch(workdir, seq).active:
        raise BatchInactiveError("该批次已停用，请先显示该批次再导出。")
    store = WorkdirStore(workdir)
    latest = load_latest_item_records(store.runs_dir, seq)
    return build_export_plan(
        workdir,
        seq,
        labeling_hashes=load_recent_success_hashes(store.runs_dir, seq),
        failed_items={item for item, row in latest.items() if row.status == "failed"},
        excluded_items=set(read_exclusions(workdir, seq)),
        sequential=sequential,
        should_stop=should_stop,
    )


@router.get("/plan", response_model=ExportPlanView)
def get_export_plan(wid: str, batch: str, sequential: bool = True) -> ExportPlanView:
    """按当前批次返回实时计划，开关改变时重新计算输出名与兼容性提示。"""
    workdir = Path(WorkdirRegistry.get(wid).path)
    return ExportPlanView.model_validate(
        asdict(_plan(workdir, parse_seq(batch), sequential))
    )


@router.post("", status_code=202, response_model=ExportAccepted)
async def start_export(
    wid: str, body: ExportStartRequest, request: Request
) -> JSONResponse:
    """受理当前批次导出；任务完成返回路径和同源下载地址。"""
    workdir = Path(WorkdirRegistry.get(wid).path)
    seq = parse_seq(body.batch)
    if not get_batch(workdir, seq).active:
        raise BatchInactiveError("该批次已停用，请先显示该批次再导出。")
    manager = cast(TaskManager, request.app.state.task_manager)

    def runner(task_id: str, should_stop: Event) -> TaskResult:
        store = WorkdirStore(workdir)
        lock = RunLock(store.dsf_path)
        lock.acquire({"pid": os.getpid(), "batch": body.batch, "operation": "export"})
        try:
            with import_guard(store.dsf_path):
                directory = store.export_directory()
                plan = _plan(workdir, seq, body.sequential, should_stop)
                filename = f"s{seq}-{secrets.token_hex(12)}.zip"

                def progress(value: float) -> None:
                    manager.set_progress(task_id, value)

                output = write_export(
                    workdir,
                    plan,
                    directory / filename,
                    should_stop=should_stop,
                    progress=progress,
                )
                return {
                    "path": str(output),
                    "download_url": f"/api/workdirs/{wid}/export/files/{filename}",
                    "file_count": len(plan.included),
                    "total_bytes": plan.total_bytes,
                }
        finally:
            lock.release()

    task_id = manager.create(runner)
    return JSONResponse(
        status_code=202,
        content=ExportAccepted(task_id=task_id).model_dump(),
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


class _ZipResponse(FileResponse):
    """把下载的 ZIP 媒体类型同时写进 OpenAPI 契约。"""

    media_type = "application/zip"


@router.get("/files/{filename}", response_class=_ZipResponse)
def download_export(wid: str, filename: str) -> _ZipResponse:
    """仅下载本工具命名的完成包，临时文件与其他元数据均不可寻址。"""
    workdir = Path(WorkdirRegistry.get(wid).path)
    if re.fullmatch(r"s[1-9][0-9]*-[0-9a-f]{24}\.zip", filename) is None:
        raise ExportError("导出文件名不合法，请从导出任务结果重新下载。")
    path = confine_to_workdir(workdir, workdir / ".dsf" / "export" / filename)
    if not path.is_file():
        raise AssetNotFoundError("导出文件不存在，请重新执行导出。")
    return _ZipResponse(path, filename=filename, media_type="application/zip")
