"""CLI 与 HTTP 共用的跨进程进度读取及协作停止请求。"""

import json
from pathlib import Path
from typing import Any

from .._fs import atomic_write_text
from ..workdir.locks import maintenance_guard, read_live_occupier, read_occupier
from .errors import RunNotActiveError


def current_run(workdir: Path, seq: int) -> dict[str, Any]:
    """读取正在持锁的指定批次进度，不将历史快照当作运行中。"""
    dsf = workdir / ".dsf"
    with maintenance_guard(workdir):
        owner = read_live_occupier(dsf)
        if owner is None or owner.get("batch") != f"s{seq}":
            raise RunNotActiveError("该批次当前没有进行中的跑批。")
        run_id = owner.get("run_id")
        if not isinstance(run_id, str):
            raise RunNotActiveError("当前占用操作不是可控制的跑批。")
        progress = read_occupier(dsf / "run-status.json")
        if progress is not None and progress.get("run_id") == run_id:
            return progress
        return {
            "run_id": run_id,
            "batch": seq,
            "mode": owner.get("mode", "full"),
            "status": "running",
            "counters": dict.fromkeys(
                ("planned", "attempted", "succeeded", "failed", "skipped"), 0
            ),
            "current_item": None,
            "error": None,
        }


def request_stop(workdir: Path, seq: int) -> str:
    """请求当前运行在安全点停止；运行 ID 防止误停后续运行。"""
    with maintenance_guard(workdir):
        progress = current_run(workdir, seq)
        if progress["status"] in {"completed", "interrupted", "failed"}:
            raise RunNotActiveError("该批次当前没有进行中的跑批。")
        run_id = str(progress["run_id"])
        atomic_write_text(
            workdir / ".dsf" / "run-stop.json", json.dumps({"run_id": run_id})
        )
        return run_id


def stop_requested(workdir: Path, run_id: str) -> bool:
    """执行器只接受属于本次运行的停止请求。"""
    request = read_occupier(workdir / ".dsf" / "run-stop.json")
    return request is not None and request.get("run_id") == run_id
