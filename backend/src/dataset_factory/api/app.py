"""api 入口层：FastAPI 应用工厂——路由组装、域异常 → HTTP 状态码映射、托管 frontend。

错误映射（域异常的消息已可操作，直接进 detail；子类 handler 先于基类注册即先匹配）：
400 输入/配置错、404 找不到、409 重名冲突、413 超限、502 上游模型端点错、500 数据
损坏 / 文件 IO（系统侧）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..labeling import (
    EmptyTurnError,
    LabelingError,
    PromptNotSelectedError,
    SettingsFormatError,
)
from ..llm import (
    ConfigError,
    ImageTooLargeError,
    LLMError,
    UnsupportedImageError,
)
from ..prompts import (
    PromptError,
    PromptNameError,
    PromptNotFoundError,
    PromptParseError,
    PromptTooLargeError,
)
from ..sessions import (
    SessionError,
    SessionEventError,
    SessionIdError,
    SessionNotFoundError,
)
from ..skills import (
    SkillError,
    SkillExistsError,
    SkillFormatError,
    SkillNameError,
    SkillNotFoundError,
    SkillSourceError,
)
from . import routes_config, routes_labeling, routes_prompts, routes_skills
from .middleware import RequestLogMiddleware


def create_app(frontend_dir: Path | None = None) -> FastAPI:
    """组装应用；frontend_dir 存在时把静态页面挂到根路径（API 路由先注册、优先匹配）。"""
    app = FastAPI(
        title="Dataset Factory",
        summary="AI 打标工具（发图 + 指令产出 caption，支持迭代改写）",
        version="0.1.0",
    )
    # 访问日志中间件：放在最外层，它量到的耗时才是整个请求的真实总耗时。
    app.add_middleware(RequestLogMiddleware)
    _register_error_handlers(app)
    app.include_router(routes_labeling.router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_skills.router)
    app.include_router(routes_config.router)
    directory = frontend_dir if frontend_dir is not None else _default_frontend_dir()
    if directory.is_dir():
        app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
    return app


def _default_frontend_dir() -> Path:
    """frontend 默认位置 = backend 工程旁的 ../frontend（workspace/frontend）。"""
    return Path(__file__).resolve().parents[3].parent / "frontend"


# 域异常 → HTTP 状态码：先注册子类（精确匹配优先），再注册基类兜底。
_ERROR_MAP: list[tuple[int, tuple[type[Exception], ...]]] = [
    (
        400,
        (
            UnsupportedImageError,
            ImageTooLargeError,
            PromptNameError,
            SkillNameError,
            SkillFormatError,
            SkillSourceError,
            PromptParseError,
            EmptyTurnError,
            PromptNotSelectedError,
            ConfigError,
            SessionIdError,
        ),
    ),
    (
        404,
        (PromptNotFoundError, SkillNotFoundError, SessionNotFoundError),
    ),
    (409, (SkillExistsError,)),
    (413, (PromptTooLargeError,)),
    (502, (LLMError,)),
    (
        500,
        (
            SettingsFormatError,
            SessionEventError,
            LabelingError,
            PromptError,
            SkillError,
            SessionError,
        ),
    ),
]


def _register_error_handlers(app: FastAPI) -> None:
    """按映射表注册异常处理器：域异常消息进 detail、状态码按分类。"""

    def make_handler(
        status_code: int,
    ) -> Callable[[Request, Exception], JSONResponse]:
        def handler(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

        return handler

    for status_code, exc_types in _ERROR_MAP:
        for exc_type in exc_types:
            app.add_exception_handler(exc_type, make_handler(status_code))


app = create_app()
