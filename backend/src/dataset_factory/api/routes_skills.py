"""Skill 库端点：GET /api/skills、POST /api/skills/import、POST /api/skills/import-upload、enable/disable、DELETE。

skill 是本地目录包（agentskills.io 标准）。两条导入路线：CLI / 脚本走本地路径复制
（import-upload 之外），浏览器走「文件夹选择器 / 拖拽」上传文件集——浏览器安全模型拿不到
所选文件夹的本地路径，传的是文件内容；上传路线的相对路径在此清洗（拒绝穿越与空段）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, UploadFile, status

from ..skills import (
    delete_skill,
    import_skill,
    import_skill_files,
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


@router.post(
    "/import-upload",
    response_model=SkillImportResponse,
    responses={
        400: {
            "model": ErrorDetail,
            "description": "上传内容不合法（缺 SKILL.md / 文件名穿越 / 格式非法）",
        },
        409: {"model": ErrorDetail, "description": "同名 skill 已存在（重名不合并）"},
    },
)
async def import_upload(files: list[UploadFile]) -> SkillImportResponse:
    """从浏览器上传的文件集导入 skill 包（文件夹选择器 / 拖拽；传内容不传路径）。"""
    payload: dict[str, bytes] = {}
    for upload in files:
        raw = (upload.filename or "").replace("\\", "/")
        parts = [part for part in raw.split("/") if part != ""]
        if not parts or any(part in (".", "..") for part in parts):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"上传文件名不合法：{raw!r}。",
            )
        rel = "/".join(parts)
        if rel in payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"上传内容存在重复路径：{rel}。",
            )
        payload[rel] = await upload.read()
    result = import_skill_files(payload)
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
