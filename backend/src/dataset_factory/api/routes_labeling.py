"""打标与会话端点：POST /api/label（JSON 一次性）、POST /api/label/stream（SSE 流式）、GET /api/sessions/{latest,id}。"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Generator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

from ..labeling import (
    LabelingEngine,
    SessionSnapshot,
    StreamFinished,
    StreamStarted,
    active_session_ids,
)
from ..llm import (
    VIDEO_MIME_BY_SUFFIX,
    LLMError,
    StreamDelta,
    build_completer,
    read_config,
)
from ..sessions import (
    SessionError,
    SessionNotFoundError,
    attachment_path,
    delete_session,
    latest_session_id,
    latest_session_id_for,
    retain_latest_for,
    write_strategy_id,
)
from ..workdir.assets import mime_for_suffix
from .schemas import (
    AssignStrategyRequest,
    ErrorDetail,
    HistoryMessageView,
    LabelRequest,
    LabelResponse,
    SessionSnapshotResponse,
    SettingsView,
)

router = APIRouter(prefix="/api", tags=["打标与会话"])

# MIME 映射单一事实源在 llm.messages（audit 2026-09-14 收敛）；未识别扩展名回落 mp4。
_VIDEO_MIME = VIDEO_MIME_BY_SUFFIX


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
        # fps 在请求边界已校验为整数值（LabelRequest 模型校验器），float→int 转换精确无损；
        # 端点（SiliconFlow）对浮点 fps 判非法，wire 上必须是整型。
        video_fps=int(request.video_fps),
        video_max_frames=request.video_max_frames,
        strategy_id=request.strategy_id,
    )
    # 滚动保留（三期 v3）：新会话首轮成功落盘后，同桶旧会话删除；失败轮在上方抛出、
    # 走不到这里，旧会话保留。
    _retain_bucket(request.strategy_id, keep=result.session_id)
    return LabelResponse(session_id=result.session_id, caption=result.caption)


@router.post(
    "/label/stream",
    responses={
        400: {
            "model": ErrorDetail,
            "description": "输入不合法（同 /api/label；预备段失败走正常状态码，流中失败发 error 事件）",
        },
        404: {"model": ErrorDetail, "description": "会话或提示词不存在"},
    },
)
def label_stream(request: LabelRequest) -> StreamingResponse:
    """流式打标（SSE）：事件 = start → delta(kind=reasoning|content)… → done；失败发 error。"""
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
    generator = build_engine().label_stream(
        session_id=request.session_id,
        prompt_name=request.prompt_name,
        skill_names=request.skill_names,
        instruction=request.instruction,
        image_bytes=image_bytes,
        image_name=request.image_name,
        video_bytes=video_bytes,
        video_name=request.video_name,
        video_mime=video_mime,
        video_fps=int(request.video_fps),
        video_max_frames=request.video_max_frames,
        strategy_id=request.strategy_id,
    )
    # 预备段（校验 / 落信封）在返回响应前先执行到首个事件：域错误在此按全局映射转
    # 状态码（400/404…），不吞进 SSE——已开始的 SSE 无法再改状态码。
    first = next(generator)

    def event_stream() -> Generator[str, None, None]:
        try:
            yield _sse_event(first)
            for item in generator:
                yield _sse_event(item)
                # 滚动保留（三期 v3）：终稿落盘（done 帧）后删同桶旧会话；失败 /
                # 中断轮走不到 StreamFinished，旧会话保留。
                if isinstance(item, StreamFinished):
                    _retain_bucket(request.strategy_id, keep=item.result.session_id)
        except LLMError as exc:
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_event(
    item: StreamStarted | StreamDelta | StreamFinished,
) -> str:
    """把引擎的流事件转成一条 SSE 帧。"""
    if isinstance(item, StreamStarted):
        return _sse("start", {"session_id": item.session_id})
    if isinstance(item, StreamDelta):
        return _sse("delta", {"kind": item.kind, "text": item.text})
    return _sse(
        "done",
        {
            "session_id": item.result.session_id,
            "caption": item.result.caption,
        },
    )


def _sse(event: str, data: dict[str, str]) -> str:
    """一条 SSE 帧（event + data 两行）；JSON 不转义中文，保持可读。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/sessions/latest",
    response_model=SessionSnapshotResponse,
    responses={404: {"model": ErrorDetail, "description": "还没有任何会话"}},
)
def latest_session(strategy_id: str | None = None) -> SessionSnapshotResponse:
    """最新会话快照（重启恢复入口）；一个会话都没有时 404。

    带 ``strategy_id`` 查询时按归属桶取最新（三期 v3：每策略各自的最近会话），
    该桶为空同样 404；不带时为全局最新（存量认领垫层用）。
    """
    session_id = (
        latest_session_id_for(strategy_id) if strategy_id else latest_session_id()
    )
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
    """某会话快照（设置 + 对话历史 + 归属）。"""
    return _snapshot_response(build_engine().restore(session_id))


def _retain_bucket(strategy_id: str | None, *, keep: str) -> None:
    """按桶滚动保留（三期 v3）：桶内只留 ``keep``，其余删除。

    策略为 None（无归属轮）不滚动。删除失败静默跳过（retain_latest_for 内部
    已逐目录兜底）——保留失败不回滚本轮成功的打标结果。
    """
    if strategy_id:
        retain_latest_for(strategy_id, keep=keep)


@router.post(
    "/sessions/{session_id}/strategy",
    response_model=SessionSnapshotResponse,
    responses={
        404: {"model": ErrorDetail, "description": "会话不存在"},
        400: {"model": ErrorDetail, "description": "strategy_id 非法"},
    },
)
def assign_session_strategy(
    session_id: str, body: AssignStrategyRequest
) -> SessionSnapshotResponse:
    """改挂会话归属（三期 v3）：保存新策略时把当前草稿会话从 ``__new__`` 挂到新 id。"""
    try:
        write_strategy_id(session_id, body.strategy_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _snapshot_response(build_engine().restore(session_id))


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    responses={
        404: {"model": ErrorDetail, "description": "会话不存在"},
        409: {"model": ErrorDetail, "description": "会话有进行中的打标轮次"},
    },
)
def delete_session_endpoint(session_id: str) -> Response:
    """删除一个会话（事件流 + 附件 + 归属；有轮次在写时拒绝）。"""
    if session_id in active_session_ids():
        raise HTTPException(
            status_code=409, detail="该会话正在打标，请等本轮结束或停止后再删除。"
        )
    try:
        delete_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


def _snapshot_response(snapshot: SessionSnapshot) -> SessionSnapshotResponse:
    """把引擎的 SessionSnapshot 翻译成响应模型（入口层只做翻译）。"""
    return SessionSnapshotResponse(
        session_id=snapshot.session_id,
        strategy_id=snapshot.strategy_id,
        settings=SettingsView(
            prompt_name=snapshot.settings.prompt_name,
            skill_names=list(snapshot.settings.skill_names),
        ),
        messages=[
            HistoryMessageView(
                role=item.role,
                text=item.text,
                attachment=item.attachment,
                reasoning=item.reasoning,
                partial=item.partial,
                elapsed_ms=item.elapsed_ms,
                reasoning_ms=item.reasoning_ms,
            )
            for item in snapshot.messages
        ],
    )


@router.get(
    "/sessions/{session_id}/attachments/{name}",
    response_class=FileResponse,
    responses={
        404: {"model": ErrorDetail, "description": "会话或附件不存在"},
        400: {"model": ErrorDetail, "description": "附件名非法"},
    },
)
def session_attachment(session_id: str, name: str) -> FileResponse:
    """取会话附件的文件字节（B5，2026-09-21 审计）：历史缩略图不再依赖内存 dataURL。

    安全口径与素材域的 /asset 同源：附件名经 sessions 域的单段安全名校验（路径穿越
    与非法字符在数据域拦下），只读、越界即 404。

    Content-Type 显式按扩展名给（PRD-0004）：``FileResponse`` 缺省靠 mimetypes 猜，
    猜不中的扩展名回落 octet-stream 会令 ``<video>``（历史封面 / 大图预览）拒播；
    映射与素材域 /asset 同一份（MIME 单一事实源在 llm.messages）。
    """
    return FileResponse(
        attachment_path(session_id, name),
        filename=name,
        content_disposition_type="inline",
        media_type=mime_for_suffix(Path(name).suffix),
    )
