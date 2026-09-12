"""Skill 库端点：GET /api/skills、POST /api/skills/import、enable/disable、DELETE。

skill 是本地目录包（agentskills.io 标准），导入按路径复制进库——Web 端跑在本机、
浏览器与服务端同机，填本地路径即可（一期不做目录上传）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response, status

from ..skills import (
    delete_skill,
    import_skill,
    list_skill_files,
    list_skills,
    read_skill_file,
    set_enabled,
)
from .schemas import (
    ErrorDetail,
    SkillFileContent,
    SkillFileInfo,
    SkillFilesResponse,
    SkillImportRequest,
    SkillImportResponse,
    SkillInfo,
)

router = APIRouter(prefix="/api/skills", tags=["Skill 库"])


@router.get("", response_model=list[SkillInfo])
def list_all() -> list[SkillInfo]:
    """列出全部 skill（含启用状态）。"""
    return [
        SkillInfo(name=item.name, description=item.description, enabled=item.enabled)
        for item in list_skills()
    ]


@router.post(
    "/import",
    response_model=SkillImportResponse,
    responses={
        400: {"model": ErrorDetail, "description": "路径不存在 / 格式不合法"},
        409: {"model": ErrorDetail, "description": "同名 skill 已存在（重名不合并）"},
    },
)
def import_one(request: SkillImportRequest) -> SkillImportResponse:
    """从本地路径导入 skill 包（整目录复制进库、默认启用）。"""
    result = import_skill(Path(request.path))
    return SkillImportResponse(
        name=result.skill.name,
        description=result.skill.description,
        enabled=result.skill.enabled,
        total_bytes=result.total_bytes,
    )


@router.get(
    "/{name}/files",
    response_model=SkillFilesResponse,
    responses={404: {"model": ErrorDetail, "description": "skill 不存在"}},
)
def list_package_files(name: str) -> SkillFilesResponse:
    """列出技能包内文件（角色标注：SKILL.md 与 references/ 可预览，assets / scripts 灰显占位）。"""
    return SkillFilesResponse(
        name=name,
        files=[
            SkillFileInfo(
                path=entry.path, role=entry.role, previewable=entry.previewable
            )
            for entry in list_skill_files(name)
        ],
    )


@router.get(
    "/{name}/files/{path:path}",
    response_model=SkillFileContent,
    responses={
        400: {
            "model": ErrorDetail,
            "description": "路径不合法 / 文件不参与预览 / 内容不是 UTF-8 文本",
        },
        404: {"model": ErrorDetail, "description": "skill 或包内文件不存在"},
    },
)
def read_package_file(name: str, path: str) -> SkillFileContent:
    """读技能包内一个可预览文件的文本内容（UTF-8；仅 SKILL.md 与 references/ 开放）。"""
    return SkillFileContent(path=path, content=read_skill_file(name, path))


@router.post(
    "/{name}/enable",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "skill 不存在"}},
)
def enable(name: str) -> Response:
    """启用 skill。"""
    set_enabled(name, True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{name}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "skill 不存在"}},
)
def disable(name: str) -> Response:
    """停用 skill（保留在库中，打标不注入）。"""
    set_enabled(name, False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "skill 不存在"}},
)
def remove(name: str) -> Response:
    """删除 skill（整目录移除）。"""
    delete_skill(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
