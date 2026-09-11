"""提示词库端点：GET /api/prompts、GET/PUT/DELETE /api/prompts/{name}。"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..prompts import (
    Prompt,
    delete_prompt,
    list_prompts,
    read_prompt,
    save_prompt,
)
from .schemas import ErrorDetail, PromptFull, PromptInfo, PromptSaveRequest

router = APIRouter(prefix="/api/prompts", tags=["提示词库"])


@router.get("", response_model=list[PromptInfo])
def list_all() -> list[PromptInfo]:
    """列出全部提示词。"""
    return [
        PromptInfo(name=item.name, description=item.description)
        for item in list_prompts()
    ]


@router.get(
    "/{name}",
    response_model=PromptFull,
    responses={
        400: {"model": ErrorDetail, "description": "提示词文件损坏（格式非法）"},
        404: {"model": ErrorDetail, "description": "提示词不存在"},
    },
)
def get_one(name: str) -> PromptFull:
    """读某条提示词全文。"""
    prompt = read_prompt(name)
    return PromptFull(
        name=prompt.name, description=prompt.description, body=prompt.body
    )


@router.put(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorDetail, "description": "名称不合法（含路径分隔符等）"},
        413: {"model": ErrorDetail, "description": "提示词超出 32 KiB 字节护栏"},
    },
)
def save(name: str, request: PromptSaveRequest) -> Response:
    """保存提示词（不存在即新建、已存在即覆盖——旧版进 _history 滚动备份）。"""
    save_prompt(Prompt(name=name, description=request.description, body=request.body))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "提示词不存在"}},
)
def remove(name: str) -> Response:
    """删除提示词。"""
    delete_prompt(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
