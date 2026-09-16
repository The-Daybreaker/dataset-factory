"""工作目录注册表端点：GET /api/workdirs、GET /api/workdirs/{wid}（二期新增）。

本模块只承载注册表读面（两级下拉的数据源）；登记（POST，202 + 任务句柄）、
删除（DELETE，破坏性 + 运行锁）随各自功能点落地。错误一律 problem+json。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..workdir import WorkdirEntry, WorkdirRegistry
from .schemas import Problem, WorkdirInfo

router = APIRouter(prefix="/api/workdirs", tags=["工作目录"])


def _to_info(entry: WorkdirEntry) -> WorkdirInfo:
    """注册表条目 → 响应模型。"""
    return WorkdirInfo(
        id=entry.id,
        path=entry.path,
        title=entry.title,
        last_used_at=entry.last_used_at,
    )


@router.get("", response_model=list[WorkdirInfo])
def list_all() -> list[WorkdirInfo]:
    """列出全部登记的工作目录，按最后使用时间倒序（最近在前）。"""
    return [_to_info(entry) for entry in WorkdirRegistry.list_all()]


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
