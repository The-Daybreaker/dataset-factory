"""api 入口层：FastAPI 应用工厂——路由组装、域异常 → HTTP 状态码映射、托管 frontend。

错误映射（域异常的消息已可操作，直接进 detail；Starlette 按 `type(exc).__mro__` 就近匹配
处理器——子类命中自身或最近基类的 handler，与注册顺序无关）：
400 输入/配置错、404 找不到、409 重名冲突、413 超限、502 上游模型端点错、500 数据
损坏 / 文件 IO（系统侧）。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
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
    ConfigConflictError,
    ConfigError,
    ConfigNotFoundError,
    ImageTooLargeError,
    LLMError,
    UnsupportedImageError,
)
from ..prompts import (
    PromptError,
    PromptExistsError,
    PromptNameError,
    PromptNotFoundError,
    PromptParseError,
    PromptTooLargeError,
    seed_builtin_presets,
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
    SkillFileNotPreviewableError,
    SkillFilePathError,
    SkillFormatError,
    SkillNameError,
    SkillNotFoundError,
    SkillSourceError,
)
from ..tasks import TaskManager, TaskNotFoundError
from ..workdir import (
    ImportSourceConflictError,
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
)
from . import (
    routes_config,
    routes_endpoints,
    routes_labeling,
    routes_prompts,
    routes_service,
    routes_skills,
    routes_tasks,
    routes_workdir,
)
from .middleware import RequestLogMiddleware
from .problems import problem_response


def create_app(frontend_dir: Path | None = None) -> FastAPI:
    """组装应用；frontend_dir 存在时把静态页面挂到根路径（API 路由先注册、优先匹配）。"""
    app = FastAPI(
        title="Dataset Factory",
        summary="AI 打标工具（发图 + 指令产出 caption，支持迭代改写）",
        version="0.1.0",
        lifespan=_lifespan,
    )
    # 访问日志中间件：放在最外层，它量到的耗时才是整个请求的真实总耗时。
    app.add_middleware(RequestLogMiddleware)
    _register_error_handlers(app)
    # 长任务管理器随应用实例装配（内存态、重启即丢；测试各自 create_app 天然隔离）。
    app.state.task_manager = TaskManager()
    app.include_router(routes_labeling.router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_skills.router)
    app.include_router(routes_endpoints.router)
    app.include_router(routes_config.router)
    app.include_router(routes_service.router)
    app.include_router(routes_tasks.router)
    app.include_router(routes_workdir.router)
    directory = frontend_dir if frontend_dir is not None else _default_frontend_dir()
    if directory.is_dir():
        app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用启动钩子：播种产品内置预置提示词（标记文件在即 no-op，常态零开销）。"""
    seed_builtin_presets()
    yield


def _default_frontend_dir() -> Path:
    """frontend 默认位置 = backend 工程旁的 ../frontend/dist（前端构建产物）。

    前端已改为工程化构建（Vite + React + TypeScript）：托管的是构建产物而非源码。
    开发期改用 `npm run dev` 起 Vite dev server（它自带 /api 代理到后端），
    平时用 `npm run build` 产出 dist/ 后由本服务托管。
    """
    return Path(__file__).resolve().parents[3].parent / "frontend" / "dist"


# 域异常 → HTTP 状态码：Starlette 按异常类的 MRO 就近匹配（子类优先于基类），注册顺序无关。
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
            SkillFilePathError,
            SkillFileNotPreviewableError,
        ),
    ),
    (
        404,
        (
            PromptNotFoundError,
            SkillNotFoundError,
            SessionNotFoundError,
            ConfigNotFoundError,
        ),
    ),
    (409, (SkillExistsError, ConfigConflictError, PromptExistsError)),
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

    # 二期新端点走 problem+json 错误形（api.problems）：错误体 = type slug + title + status + detail。
    def task_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "task-not-found", "任务不存在", str(exc))

    app.add_exception_handler(TaskNotFoundError, task_not_found_handler)

    def workdir_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "workdir-not-found", "工作目录不存在", str(exc))

    app.add_exception_handler(WorkdirNotFoundError, workdir_not_found_handler)

    def workdir_path_invalid_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(400, "workdir-path-invalid", "路径不合法", str(exc))

    app.add_exception_handler(WorkdirPathError, workdir_path_invalid_handler)

    def workdir_metadata_corrupted_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return problem_response(
            500, "workdir-metadata-corrupted", "工作目录元数据损坏", str(exc)
        )

    app.add_exception_handler(
        WorkdirMetadataCorruptedError, workdir_metadata_corrupted_handler
    )

    def import_source_conflict_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return problem_response(422, "import-source-conflict", "导入来源冲突", str(exc))

    app.add_exception_handler(ImportSourceConflictError, import_source_conflict_handler)
