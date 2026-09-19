"""Skill 库端点：GET /api/skills、POST /api/skills/import、POST /api/skills/import-upload、enable/disable/rename、DELETE。

skill 是本地目录包（agentskills.io 标准）。导入有三条路线：CLI / 脚本与 Web「路径导入」走本地
路径（/api/skills/import，目录整包或单个 SKILL.md 文件），浏览器走「文件夹选择器」上传文件集
（/api/skills/import-upload）——浏览器安全模型拿不到所选文件夹的本地路径，传的是文件内容；
上传路线的相对路径在此清洗（拒绝穿越与空段）。
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
    rename_skill,
    set_enabled,
)
from ..skills.store import save_skill_file
from .schemas import (
    ErrorDetail,
    SkillFileContent,
    SkillFileInfo,
    SkillFileSaveRequest,
    SkillFilesResponse,
    SkillImportRequest,
    SkillImportResponse,
    SkillInfo,
    SkillRenameRequest,
)

router = APIRouter(prefix="/api/skills", tags=["Skill 库"])

_SKILL_MD = "SKILL.md"


def _strip_picker_root(payload: dict[str, bytes]) -> dict[str, bytes]:
    """剥掉浏览器文件夹选择器多带的那一层「所选文件夹名」。

    文件夹选择器给的相对路径形如 ``<所选文件夹>/SKILL.md``（``webkitRelativePath`` 的
    形状），而 skill 包根应是 SKILL.md 所在那层。仅当「根上没有 SKILL.md、所有路径都在
    同一个顶层目录下、且该目录下确有 SKILL.md」时整体降一级；其余形状原样交给导入校验
    报错（不猜用户意图）。

    Args:
        payload: 清洗后的「包内相对路径 → 内容」。

    Returns:
        降级后的文件集；不需要降级时原样返回。
    """
    if _SKILL_MD in payload or not payload:
        return payload
    roots = {rel.split("/", 1)[0] for rel in payload}
    if len(roots) != 1 or any("/" not in rel for rel in payload):
        return payload
    prefix = f"{roots.pop()}/"
    if f"{prefix}{_SKILL_MD}" not in payload:
        return payload
    return {rel.removeprefix(prefix): content for rel, content in payload.items()}


@router.get("", response_model=list[SkillInfo])
def list_all() -> list[SkillInfo]:
    """列出全部 skill（含启用状态）。"""
    return [
        SkillInfo(
            name=item.name,
            description=item.description,
            enabled=item.enabled,
            body_chars=item.body_chars,
        )
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
    """从本机路径导入 skill（默认启用）：目录整包复制；单个文件按 SKILL.md 单文件导入。

    目录源走 agentskills.io 标准整包复制；指向一个 ``.md`` 文件时视为「无文件夹结构的
    单文件 skill」——文件整体按 SKILL.md 交付，名称 / 描述取自它的 frontmatter。
    """
    source = Path(request.path)
    if not source.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"路径不存在：{source}。",
        )
    if source.is_file():
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法读取文件：{exc.strerror or exc}。",
            ) from exc
        result = import_skill_files({_SKILL_MD: content})
    else:
        result = import_skill(source)
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
    result = import_skill_files(_strip_picker_root(payload))
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


@router.put(
    "/{name}/files/{path:path}",
    response_model=SkillFileContent,
    responses={
        400: {"model": ErrorDetail, "description": "文件或内容不合法"},
        404: {"model": ErrorDetail, "description": "文件不存在"},
        409: {"model": ErrorDetail, "description": "文件已被其他写者修改"},
    },
)
def save_package_file(
    name: str, path: str, request: SkillFileSaveRequest
) -> SkillFileContent:
    """写回现有技能文本文件，SKILL.md 同时校验其 frontmatter。"""
    return SkillFileContent(
        path=path,
        content=save_skill_file(
            name,
            path,
            request.content,
            original_content=request.original_content,
            description=request.description,
        ),
    )


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


@router.post(
    "/{name}/rename",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorDetail, "description": "skill 不存在"},
        409: {"model": ErrorDetail, "description": "新名称已被占用"},
    },
)
def rename(name: str, request: SkillRenameRequest) -> Response:
    """重命名 skill：目录改名，SKILL.md frontmatter 的 name 同步改写。"""
    rename_skill(name, request.new_name)
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
