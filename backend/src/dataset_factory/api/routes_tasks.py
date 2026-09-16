"""tasks 横切端点：任务轮询与协作式取消（二期新增）。

受理端点（202 + task_id + Retry-After）在各自业务路由里（导入 / 搬迁 / 打包），
本模块只承载两类任务的共用查询面：

- ``GET /tasks/{task_id}``：轮询任务状态（status / progress / result / error）；
- ``POST /tasks/{task_id}/cancel``：协作式取消——置取消信号即返回当前快照，
  任务体在安全点检查信号后自行退出（不被强杀）。
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request

from ..tasks import TaskInfo, TaskManager

router = APIRouter(prefix="/api", tags=["tasks"])


def _manager(request: Request) -> TaskManager:
    """取应用级任务管理器（create_app 时装配到 app.state，随应用实例隔离）。"""
    return cast(TaskManager, request.app.state.task_manager)


def _task_view(info: TaskInfo) -> dict[str, object]:
    """TaskInfo → 响应体（design 横切约定字段：status + progress + result）。"""
    return {
        "id": info.id,
        "status": info.status,
        "progress": info.progress,
        "result": info.result,
        "error": info.error,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict[str, object]:
    """轮询任务状态；不存在返回 404 problem+json（task-not-found，消息只指动作）。"""
    return _task_view(_manager(request).get(task_id))


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request) -> dict[str, object]:
    """协作式取消：置信号、返回当前快照；任务体随后在安全点退出并归档状态。"""
    return _task_view(_manager(request).request_cancel(task_id))
