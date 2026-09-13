"""打标与会话端点：POST /api/label、GET /api/sessions/{latest,id}。"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..labeling import LabelingEngine, SessionSnapshot
from ..llm import build_completer, read_config
from ..sessions import latest_session_id
from .schemas import (
    ErrorDetail,
    HistoryMessageView,
    LabelRequest,
    LabelResponse,
    SessionSnapshotResponse,
    SettingsView,
)

router = APIRouter(prefix="/api", tags=["打标与会话"])

# 视频扩展名 → MIME（进 video_url 的 data URL 前缀）；未识别的扩展名回落 mp4。
_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


def build_engine() -> LabelingEngine:
    """从当前端点配置装配打标引擎（api 版工厂，测试 monkeypatch 注入假客户端）。"""
    config = read_config()
    return LabelingEngine(build_completer(config), config.model)


def _decode_media(payload: str, kind: str) -> bytes:
    """把 data URL 或纯 base64 解码成媒体字节；不合法即 400（输入翻译在入口层做）。"""
    raw = payload.split(",", 1)[-1] if payload.startswith("data:") else payload
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{kind} base64 内容不合法；请确认上传的是有效文件。",
        ) from exc


@router.post(
    "/label",
    response_model=LabelResponse,
    responses={
        400: {
            "model": ErrorDetail,
            "description": "输入不合法（图片 / 视频不合法或互斥、提示词未选、空轮、端点配置缺失）",
        },
        404: {"model": ErrorDetail, "description": "会话或提示词不存在"},
        502: {"model": ErrorDetail, "description": "模型端点调用失败"},
        500: {"model": ErrorDetail, "description": "会话落盘等内部错误"},
    },
)
def label(request: LabelRequest) -> LabelResponse:
    """跑一轮打标（带 session_id 即续接迭代改写）。"""
    if request.image_base64 and request.video_base64:
        raise HTTPException(
            status_code=400, detail="图片与视频只能带一个（一期单素材/次）。"
        )
    image_bytes = (
        _decode_media(request.image_base64, "图片") if request.image_base64 else None
    )
    video_bytes = (
        _decode_media(request.video_base64, "视频") if request.video_base64 else None
    )
    video_mime = _VIDEO_MIME.get(Path(request.video_name).suffix.lower(), "video/mp4")
    result = build_engine().label(
        session_id=request.session_id,
        prompt_name=request.prompt_name,
        skill_names=request.skill_names,
        instruction=request.instruction,
        image_bytes=image_bytes,
        image_name=request.image_name,
        video_bytes=video_bytes,
        video_name=request.video_name,
        video_mime=video_mime,
        video_fps=request.video_fps,
        video_max_frames=request.video_max_frames,
    )
    return LabelResponse(session_id=result.session_id, caption=result.caption)


@router.get(
    "/sessions/latest",
    response_model=SessionSnapshotResponse,
    responses={404: {"model": ErrorDetail, "description": "还没有任何会话"}},
)
def latest_session() -> SessionSnapshotResponse:
    """最新会话快照（重启恢复入口）；一个会话都没有时 404。"""
    session_id = latest_session_id()
    if session_id is None:
        raise HTTPException(
            status_code=404, detail="还没有任何会话；发第一轮打标即自动创建。"
        )
    return _snapshot_response(build_engine().restore(session_id))


@router.get(
    "/sessions/{session_id}",
    response_model=SessionSnapshotResponse,
    responses={404: {"model": ErrorDetail, "description": "会话不存在"}},
)
def get_session(session_id: str) -> SessionSnapshotResponse:
    """某会话快照（设置 + 对话历史）。"""
    return _snapshot_response(build_engine().restore(session_id))


def _snapshot_response(snapshot: SessionSnapshot) -> SessionSnapshotResponse:
    """把引擎的 SessionSnapshot 翻译成响应模型（入口层只做翻译）。"""
    return SessionSnapshotResponse(
        session_id=snapshot.session_id,
        settings=SettingsView(
            prompt_name=snapshot.settings.prompt_name,
            skill_names=list(snapshot.settings.skill_names),
        ),
        messages=[
            HistoryMessageView(
                role=item.role, text=item.text, attachment=item.attachment
            )
            for item in snapshot.messages
        ],
    )
