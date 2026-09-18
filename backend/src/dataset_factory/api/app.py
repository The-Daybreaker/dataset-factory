"""api 入口层：FastAPI 应用工厂——路由组装、域异常 → HTTP 状态码映射、托管 frontend。

错误映射（域异常的消息已可操作，直接进 detail；Starlette 按 `type(exc).__mro__` 就近匹配
处理器——子类命中自身或最近基类的 handler，与注册顺序无关）：
400 输入/配置错、404 找不到、409 重名冲突、413 超限、502 上游模型端点错、500 数据
损坏 / 文件 IO（系统侧）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..export import ExportError
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
from ..runs import (
    BatchInactiveError,
    RetryItemNotEligibleError,
    RunError,
    RunJournalCorruptedError,
    RunNotActiveError,
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
from ..strategies import (
    BatchNotFoundError,
    StrategyError,
    StrategyNameError,
    StrategyNotFoundError,
    StrategyRefsError,
)
from ..tasks import TaskManager, TaskNotFoundError
from ..workdir import (
    AssetNotFoundError,
    AssetPathError,
    ImportInProgressError,
    ImportSourceConflictError,
    ProductNotFoundError,
    RunOccupiedError,
    StateLockTimeoutError,
    WorkdirMaintenanceError,
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
)
from . import (
    routes_config,
    routes_endpoints,
    routes_export,
    routes_filesystem,
    routes_items,
    routes_labeling,
    routes_prompts,
    routes_runs,
    routes_service,
    routes_skills,
    routes_strategies,
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
    # 导入槽位表：同一工作目录同时只允许一个导入任务（routes_workdir 检查与释放）。
    app.state.import_slots = {}
    app.state.import_slots_guard = threading.Lock()
    # 跑批运行注册表：工作目录 realpath → 运行中的 BatchRunner（内存态、重启即丢；
    # 磁盘运行锁是跨进程权威，注册表只做进程内快速拒绝与 current/stop/stream 的寻址）。
    app.state.run_registry = {}
    app.state.run_registry_guard = threading.Lock()
    app.include_router(routes_labeling.router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_skills.router)
    app.include_router(routes_endpoints.router)
    app.include_router(routes_config.router)
    app.include_router(routes_service.router)
    app.include_router(routes_filesystem.router)
    app.include_router(routes_tasks.router)
    app.include_router(routes_workdir.router)
    app.include_router(routes_export.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_runs.retry_router)
    app.include_router(routes_items.router)
    app.include_router(routes_strategies.library_router)
    app.include_router(routes_strategies.batches_router)
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

    def export_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(
            status_code=400,
            type_slug="export-invalid",
            title="无法导出",
            detail=str(exc),
        )

    app.add_exception_handler(ExportError, export_handler)

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

    def workdir_maintenance_handler(request: Request, exc: Exception) -> JSONResponse:
        # 与 run-occupied 分开：占用者是「目录正在搬迁 / 删除」，不是「有跑批在跑」。
        return problem_response(
            409, "workdir-maintenance", "工作目录正在维护", str(exc)
        )

    app.add_exception_handler(WorkdirMaintenanceError, workdir_maintenance_handler)

    def workdir_metadata_corrupted_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return problem_response(
            500, "workdir-metadata-corrupted", "工作目录元数据损坏", str(exc)
        )

    app.add_exception_handler(
        WorkdirMetadataCorruptedError, workdir_metadata_corrupted_handler
    )

    def state_lock_timeout_handler(request: Request, exc: Exception) -> JSONResponse:
        # 状态锁等满宽超时 = 诊断信号（临界区毫秒级，正常永不触发）：500 档。
        return problem_response(500, "state-lock-timeout", "状态锁等待超时", str(exc))

    app.add_exception_handler(StateLockTimeoutError, state_lock_timeout_handler)

    def asset_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "asset-not-found", "素材不存在", str(exc))

    app.add_exception_handler(AssetNotFoundError, asset_not_found_handler)

    def product_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "product-not-found", "产物不存在", str(exc))

    app.add_exception_handler(ProductNotFoundError, product_not_found_handler)

    def asset_path_invalid_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(400, "asset-path-invalid", "条目名不合法", str(exc))

    app.add_exception_handler(AssetPathError, asset_path_invalid_handler)

    def import_source_conflict_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return problem_response(422, "import-source-conflict", "导入来源冲突", str(exc))

    app.add_exception_handler(ImportSourceConflictError, import_source_conflict_handler)

    def import_in_progress_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(409, "import-in-progress", "导入任务进行中", str(exc))

    app.add_exception_handler(ImportInProgressError, import_in_progress_handler)

    def strategy_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "strategy-not-found", "库策略不存在", str(exc))

    app.add_exception_handler(StrategyNotFoundError, strategy_not_found_handler)

    def strategy_name_invalid_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(400, "strategy-name-invalid", "策略名不合法", str(exc))

    app.add_exception_handler(StrategyNameError, strategy_name_invalid_handler)

    def strategy_refs_invalid_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(
            400, "strategy-refs-invalid", "策略引用不合法", str(exc)
        )

    app.add_exception_handler(StrategyRefsError, strategy_refs_invalid_handler)

    def batch_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "batch-not-found", "批次不存在", str(exc))

    app.add_exception_handler(BatchNotFoundError, batch_not_found_handler)

    def strategy_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # 基类兜底（库策略文件损坏等）：500 档，消息已可操作。
        return problem_response(500, "strategy-error", "策略数据异常", str(exc))

    app.add_exception_handler(StrategyError, strategy_error_handler)

    def run_occupied_handler(request: Request, exc: Exception) -> JSONResponse:
        # occupier 进 RFC 9457 扩展字段（前端提示「谁在占用」用）；跨进程残留信息
        # 损坏时为 None，detail 已有笼统文案兜底。
        occupier = getattr(exc, "occupier", None)
        return problem_response(
            409,
            "run-occupied",
            "工作目录已有跑批在运行",
            str(exc),
            extras={"occupier": occupier} if occupier else None,
        )

    app.add_exception_handler(RunOccupiedError, run_occupied_handler)

    def batch_inactive_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(409, "batch-inactive", "批次已停用", str(exc))

    app.add_exception_handler(BatchInactiveError, batch_inactive_handler)

    def run_not_active_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(404, "run-not-active", "当前没有进行中的跑批", str(exc))

    app.add_exception_handler(RunNotActiveError, run_not_active_handler)

    def run_journal_corrupted_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(500, "run-journal-corrupted", "运行流水损坏", str(exc))

    app.add_exception_handler(RunJournalCorruptedError, run_journal_corrupted_handler)

    def run_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # runs 域基类兜底：500 档，消息已可操作。
        return problem_response(500, "run-error", "跑批数据异常", str(exc))

    app.add_exception_handler(RunError, run_error_handler)

    def retry_not_eligible_handler(request: Request, exc: Exception) -> JSONResponse:
        # 逐条拒绝原因进扩展字段（前端弹「哪些没进名单、为什么」用）。
        rejections = getattr(exc, "rejections", None)
        return problem_response(
            422,
            "retry-item-not-eligible",
            "有不可加入重试列表的条目",
            str(exc),
            extras={"rejections": rejections} if rejections else None,
        )

    app.add_exception_handler(RetryItemNotEligibleError, retry_not_eligible_handler)
