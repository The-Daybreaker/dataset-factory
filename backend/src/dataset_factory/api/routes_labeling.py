"""打标与会话端点：POST /api/label、GET /api/sessions/{latest,id}。"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, HTTPException

from ..labeling import LabelingEngine, SessionSnapshot
from ..llm import build_completer, read_config
from ..sessions import latest_session_id
from .schemas import (
    HistoryMessageView,
    LabelRequest,
    LabelResponse,
    SessionSnapshotResponse,
    SettingsView,
)

router = APIRouter(prefix="/api", tags=["打标与会话"])


def build_engine() -> LabelingEngine:
    """从当前端点配置装配打标引擎（api 版工厂，测试 monkeypatch 注入假客户端）。"""
    config = read_config()
    return LabelingEngine(build_completer(config), config.model)


def _decode_image(image_base64: str) -> bytes:
    """把 data URL 或纯 base64 解码成图片字节；不合法即 400（输入翻译在入口层做）。"""
    payload = (
        image_base64.split(",", 1)[-1]
        if image_base64.startswith("data:")
        else image_base64
    )
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="图片 base64 内容不合法；请确认上传的是有效图片。"
        ) from exc


@router.post("/label", response_model=LabelResponse)
def label(request: LabelRequest) -> LabelResponse:
    """跑一轮打标（带 session_id 即续接迭代改写）。"""
    image_bytes = _decode_image(request.image_base64) if request.image_base64 else None
    result = build_engine().label(
        session_id=request.session_id,
        prompt_name=request.prompt_name,
        skill_names=request.skill_names,
        instruction=request.instruction,
        image_bytes=image_bytes,
        image_name=request.image_name,
    )
    return LabelResponse(session_id=result.session_id, caption=result.caption)


@router.get("/sessions/latest", response_model=SessionSnapshotResponse)
def latest_session() -> SessionSnapshotResponse:
    """最新会话快照（重启恢复入口）；一个会话都没有时 404。"""
    session_id = latest_session_id()
    if session_id is None:
        raise HTTPException(
            status_code=404, detail="还没有任何会话；发第一轮打标即自动创建。"
        )
    return _snapshot_response(build_engine().restore(session_id))


@router.get("/sessions/{session_id}", response_model=SessionSnapshotResponse)
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
