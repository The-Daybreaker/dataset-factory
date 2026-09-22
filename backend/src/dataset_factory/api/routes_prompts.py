"""提示词库端点：GET /api/prompts、GET/PUT/DELETE /api/prompts/{id}、POST /{id}/rename。

寻址一律用提示词的稳定 ID（文件名即 ID）；显示名可改、允许重名，不参与寻址
（2026-09-23 ID 化）。创建走 PUT 到一个新 ID（前端生成或由 POST /api/prompts 分配）。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..prompts import (
    Prompt,
    delete_prompt,
    list_prompts,
    read_prompt,
    rename_prompt,
    save_prompt,
)
from .schemas import (
    ErrorDetail,
    PromptCreated,
    PromptFull,
    PromptInfo,
    PromptRenameRequest,
    PromptSaveRequest,
)

router = APIRouter(prefix="/api/prompts", tags=["提示词库"])


@router.get("", response_model=list[PromptInfo])
def list_all() -> list[PromptInfo]:
    """列出全部提示词。"""
    return [
        PromptInfo(id=item.id, name=item.name, description=item.description)
        for item in list_prompts()
    ]


@router.post("", status_code=201, response_model=PromptCreated)
def create(request: PromptSaveRequest) -> PromptCreated:
    """新建一条提示词（服务端分配 ID）。"""
    pid = save_prompt(
        Prompt(name=request.name, description=request.description, body=request.body)
    )
    prompt = read_prompt(pid)
    return PromptCreated(id=pid, name=prompt.name, description=prompt.description)


@router.get(
    "/{pid}",
    response_model=PromptFull,
    responses={
        400: {"model": ErrorDetail, "description": "提示词文件损坏（格式非法）"},
        404: {"model": ErrorDetail, "description": "提示词不存在"},
    },
)
def get_one(pid: str) -> PromptFull:
    """读某条提示词全文。"""
    prompt = read_prompt(pid)
    return PromptFull(
        id=prompt.id,
        name=prompt.name,
        description=prompt.description,
        body=prompt.body,
    )


@router.put(
    "/{pid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorDetail, "description": "显示名不合法"},
        404: {"model": ErrorDetail, "description": "提示词不存在"},
        413: {"model": ErrorDetail, "description": "提示词超出 32 KiB 字节护栏"},
    },
)
def save(pid: str, request: PromptSaveRequest) -> Response:
    """覆盖保存提示词全文（旧版进 _history 滚动备份；显示名一并更新）。"""
    current = read_prompt(pid)
    save_prompt(
        Prompt(
            id=pid,
            name=request.name if request.name else current.name,
            description=request.description,
            body=request.body,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{pid}/rename",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorDetail, "description": "新名称不合法"},
        404: {"model": ErrorDetail, "description": "要改名的提示词不存在"},
    },
)
def rename(pid: str, request: PromptRenameRequest) -> Response:
    """改显示名（只写 frontmatter 的 name 字段；引用存 ID、不受影响）。"""
    rename_prompt(pid, request.new_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{pid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "提示词不存在"}},
)
def remove(pid: str) -> Response:
    """删除提示词。"""
    delete_prompt(pid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
