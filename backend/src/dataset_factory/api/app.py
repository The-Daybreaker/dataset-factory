"""api 入口层：FastAPI 应用工厂——路由组装、域异常 → HTTP 状态码映射、托管 frontend。

错误映射（域异常的消息已可操作，直接进 detail；Starlette 按 `type(exc).__mro__` 就近匹配
处理器——子类命中自身或最近基类的 handler，与注册顺序无关）：
400 输入/配置错、404 找不到、409 重名冲突、413 超限、502 上游模型端点错、500 数据
损坏 / 文件 IO（系统侧）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, NamedTuple

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
from ..workdir.locks import sweep_cleaned_maintenance_records
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

logger = logging.getLogger(__name__)


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
    """应用启动钩子：播种产品内置预置提示词 + 清扫已完成（cleaned）的维护记录。"""
    seed_builtin_presets()
    swept = sweep_cleaned_maintenance_records()
    if swept:
        logger.info("已清扫 %d 条已完成的维护记录（workdir-maintenance）。", swept)
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


class _ProblemRule(NamedTuple):
    """一条 problem+json 规则：异常类 → 状态码 + type slug + title（可附扩展字段名）。"""

    exc: type[Exception]
    status: int
    slug: str
    title: str
    extras_from: str | None = None


#: 二期端点的异常 → problem+json 规则表：一条一行 (异常类, 状态码, type slug, title, 扩展字段名)。
#: 原先这 23 条各写一个闭包（168 行），彼此差别只有四个字段——摊成表才看得出全貌、也才改得动。
_PROBLEM_RULES: tuple[_ProblemRule, ...] = (
    _ProblemRule(ExportError, 400, "export-invalid", "无法导出"),
    _ProblemRule(TaskNotFoundError, 404, "task-not-found", "任务不存在"),
    _ProblemRule(WorkdirNotFoundError, 404, "workdir-not-found", "工作目录不存在"),
    # 占用者是「目录正在搬迁 / 删除」，不是「有跑批在跑」——提示不能张冠李戴，故两条分开。
    _ProblemRule(
        WorkdirMaintenanceError, 409, "workdir-maintenance", "工作目录正在维护"
    ),
    _ProblemRule(WorkdirPathError, 400, "workdir-path-invalid", "路径不合法"),
    _ProblemRule(
        WorkdirMetadataCorruptedError,
        500,
        "workdir-metadata-corrupted",
        "工作目录元数据损坏",
    ),
    # 状态锁等满宽超时 = 诊断信号（临界区毫秒级，正常永不触发）：500 档。
    _ProblemRule(StateLockTimeoutError, 500, "state-lock-timeout", "状态锁等待超时"),
    _ProblemRule(AssetNotFoundError, 404, "asset-not-found", "素材不存在"),
    _ProblemRule(ProductNotFoundError, 404, "product-not-found", "产物不存在"),
    _ProblemRule(AssetPathError, 400, "asset-path-invalid", "条目名不合法"),
    _ProblemRule(
        ImportSourceConflictError, 422, "import-source-conflict", "导入来源冲突"
    ),
    _ProblemRule(ImportInProgressError, 409, "import-in-progress", "导入任务进行中"),
    _ProblemRule(StrategyNotFoundError, 404, "strategy-not-found", "库策略不存在"),
    _ProblemRule(StrategyNameError, 400, "strategy-name-invalid", "策略名不合法"),
    _ProblemRule(StrategyRefsError, 400, "strategy-refs-invalid", "策略引用不合法"),
    _ProblemRule(BatchNotFoundError, 404, "batch-not-found", "批次不存在"),
    # 基类兜底（库策略文件损坏等）：500 档，消息已可操作。
    _ProblemRule(StrategyError, 500, "strategy-error", "策略数据异常"),
    # occupier 进 RFC 9457 扩展字段（前端提示「谁在占用」用）；跨进程残留信息损坏时为
    # None，detail 已有笼统文案兜底。
    _ProblemRule(
        RunOccupiedError,
        409,
        "run-occupied",
        "工作目录已有跑批在运行",
        extras_from="occupier",
    ),
    _ProblemRule(BatchInactiveError, 409, "batch-inactive", "批次已停用"),
    _ProblemRule(RunNotActiveError, 404, "run-not-active", "当前没有进行中的跑批"),
    _ProblemRule(
        RunJournalCorruptedError, 500, "run-journal-corrupted", "运行流水损坏"
    ),
    # runs 域基类兜底：500 档，消息已可操作。
    _ProblemRule(RunError, 500, "run-error", "跑批数据异常"),
    # 逐条拒绝原因进扩展字段（前端弹「哪些没进名单、为什么」用）。
    _ProblemRule(
        RetryItemNotEligibleError,
        422,
        "retry-item-not-eligible",
        "有不可加入重试列表的条目",
        extras_from="rejections",
    ),
)


def _problem_handler(
    rule: _ProblemRule,
) -> Callable[[Request, Exception], JSONResponse]:
    """按规则表造一个 problem+json 处理器（响应体与逐条闭包写法逐字段一致）。

    Args:
        rule: 表里的一条规则（状态码 / type slug / title / 可选的扩展字段名）。

    Returns:
        可直接交给 ``add_exception_handler`` 的处理器。
    """

    def handler(request: Request, exc: Exception) -> JSONResponse:
        extras: dict[str, Any] | None = None
        if rule.extras_from is not None:
            value = getattr(exc, rule.extras_from, None)
            if value:
                extras = {rule.extras_from: value}
        return problem_response(rule.status, rule.slug, rule.title, str(exc), extras)

    return handler


def _detail_handler(status_code: int) -> Callable[[Request, Exception], JSONResponse]:
    """一期端点的简形处理器：域异常消息进 detail、状态码按 ``_ERROR_MAP`` 分类。"""

    def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler


def _register_error_handlers(app: FastAPI) -> None:
    """按两张表注册异常处理器：一期 ``{"detail"}`` 简形、二期 problem+json。"""
    for status_code, exc_types in _ERROR_MAP:
        for exc_type in exc_types:
            app.add_exception_handler(exc_type, _detail_handler(status_code))
    for rule in _PROBLEM_RULES:
        app.add_exception_handler(rule.exc, _problem_handler(rule))
