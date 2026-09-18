"""错误映射的契约测试：把「域异常 → HTTP 响应」这 23 条逐条钉住。

为什么单独写一件：`api/app.py` 原来是 21 个手写闭包（145 行），本轮收成一张规则表 + 一个工厂。
「表与原逐条闭包等价」这件事不能只靠原有路由测试顺带覆盖——每条路由测试只撞到自己那一条，
表里另一条写错不会红。这里在测试内**独立列一份期望表**（不复用产码的表，避免「一起写错就一起绿」），
逐条造异常走真实 HTTP 栈，钉住状态码、problem+json 的四个字段、媒体类型与扩展字段的取舍。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from dataset_factory.api.app import create_app
from dataset_factory.export import ExportError
from dataset_factory.llm import ConfigError
from dataset_factory.runs import (
    BatchInactiveError,
    RetryItemNotEligibleError,
    RunError,
    RunJournalCorruptedError,
    RunNotActiveError,
)
from dataset_factory.strategies import (
    BatchNotFoundError,
    StrategyError,
    StrategyNameError,
    StrategyNotFoundError,
    StrategyRefsError,
)
from dataset_factory.tasks import TaskNotFoundError
from dataset_factory.workdir import (
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

#: 期望契约：异常类 → (状态码, type slug, title, 扩展字段名)。与产码表各写一份是有意的。
EXPECTED: list[tuple[type[Exception], int, str, str, str | None]] = [
    (ExportError, 400, "export-invalid", "无法导出", None),
    (TaskNotFoundError, 404, "task-not-found", "任务不存在", None),
    (WorkdirNotFoundError, 404, "workdir-not-found", "工作目录不存在", None),
    (WorkdirPathError, 400, "workdir-path-invalid", "路径不合法", None),
    (WorkdirMaintenanceError, 409, "workdir-maintenance", "工作目录正在维护", None),
    (
        WorkdirMetadataCorruptedError,
        500,
        "workdir-metadata-corrupted",
        "工作目录元数据损坏",
        None,
    ),
    (StateLockTimeoutError, 500, "state-lock-timeout", "状态锁等待超时", None),
    (AssetNotFoundError, 404, "asset-not-found", "素材不存在", None),
    (ProductNotFoundError, 404, "product-not-found", "产物不存在", None),
    (AssetPathError, 400, "asset-path-invalid", "条目名不合法", None),
    (ImportSourceConflictError, 422, "import-source-conflict", "导入来源冲突", None),
    (ImportInProgressError, 409, "import-in-progress", "导入任务进行中", None),
    (StrategyNotFoundError, 404, "strategy-not-found", "库策略不存在", None),
    (StrategyNameError, 400, "strategy-name-invalid", "策略名不合法", None),
    (StrategyRefsError, 400, "strategy-refs-invalid", "策略引用不合法", None),
    (BatchNotFoundError, 404, "batch-not-found", "批次不存在", None),
    (StrategyError, 500, "strategy-error", "策略数据异常", None),
    (RunOccupiedError, 409, "run-occupied", "工作目录已有跑批在运行", "occupier"),
    (BatchInactiveError, 409, "batch-inactive", "批次已停用", None),
    (RunNotActiveError, 404, "run-not-active", "当前没有进行中的跑批", None),
    (
        RunJournalCorruptedError,
        500,
        "run-journal-corrupted",
        "运行流水损坏",
        None,
    ),
    (RunError, 500, "run-error", "跑批数据异常", None),
    (
        RetryItemNotEligibleError,
        422,
        "retry-item-not-eligible",
        "有不可加入重试列表的条目",
        "rejections",
    ),
]

#: 扩展字段的样例值（有值才该出现在响应体里）。两条带扩展字段的异常都由构造函数收，
#: 故下面用一个显式装配器造实例。
OCCUPIER: dict[str, Any] = {
    "pid": 4321,
    "batch": "s2",
    "started_at": "2026-01-01T00:00:00+00:00",
}
REJECTIONS: dict[str, str] = {"alpha": "素材缺失，无法重试"}

MISSING_FRONTEND = Path("__no_such_frontend_dir__")


def _build(
    exc_type: type[Exception], message: str, extras_from: str | None
) -> Exception:
    """按契约造一个异常实例；带扩展字段的两条走各自的具名参数。

    Args:
        exc_type: 异常类。
        message: 进 detail 的可操作消息。
        extras_from: 扩展字段名；None = 该条规则不带扩展字段。

    Returns:
        异常实例（扩展字段留空，由调用方按需补上）。
    """
    if exc_type is RunOccupiedError:
        return RunOccupiedError(message)
    if exc_type is RetryItemNotEligibleError:
        return RetryItemNotEligibleError(message, rejections=REJECTIONS)
    return exc_type(message)


def _client_raising(exc: Exception) -> TestClient:
    """用真实应用工厂挂一条「必定抛出 exc」的端点，返回测试客户端。

    Args:
        exc: 端点要抛出的异常实例。

    Returns:
        TestClient；`frontend_dir` 指向不存在的路径，避免静态挂载吞掉探针路由。
    """
    app = create_app(frontend_dir=MISSING_FRONTEND)

    async def probe(request: Request) -> JSONResponse:
        raise exc

    app.add_route("/probe-error", probe, methods=["GET"])
    return TestClient(app)


@pytest.mark.parametrize(
    ("exc_type", "status", "slug", "title", "extras_from"),
    EXPECTED,
    ids=[entry[0].__name__ for entry in EXPECTED],
)
def test_problem_response_matches_contract(
    exc_type: type[Exception],
    status: int,
    slug: str,
    title: str,
    extras_from: str | None,
) -> None:
    """每条规则的状态码、媒体类型与 problem+json 四字段都要按契约出。"""
    message = "可操作的中文消息：下一步该做什么。"
    exc = _build(exc_type, message, extras_from)
    if extras_from == "occupier":
        exc.occupier = OCCUPIER  # type: ignore[attr-defined]
    expected_body: dict[str, Any] = {
        "type": slug,
        "title": title,
        "status": status,
        "detail": message,
    }
    if extras_from == "occupier":
        expected_body["occupier"] = OCCUPIER
    if extras_from == "rejections":
        expected_body["rejections"] = REJECTIONS

    response = _client_raising(exc).get("/probe-error")

    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    assert body == expected_body


@pytest.mark.parametrize(
    ("exc_type", "extras_from"),
    [(RunOccupiedError, "occupier"), (RetryItemNotEligibleError, "rejections")],
    ids=["run-occupied 无占用者", "retry 无拒绝清单"],
)
def test_empty_extension_member_is_omitted(
    exc_type: type[Exception], extras_from: str
) -> None:
    """扩展字段为空（跨进程残留信息损坏 / 无逐条原因）时不多出一个空键。"""
    empty = _build(exc_type, "笼统文案", extras_from)
    if extras_from == "occupier":
        empty.occupier = None  # type: ignore[attr-defined]
    if extras_from == "rejections":
        empty.rejections = {}  # type: ignore[attr-defined]
    body: dict[str, Any] = _client_raising(empty).get("/probe-error").json()

    assert extras_from not in body
    assert body["detail"] == "笼统文案"


def test_one_handler_per_exception_class() -> None:
    """每张表项都在应用上注册到了处理器，且同异常类不被注册两次。"""
    app = create_app(frontend_dir=MISSING_FRONTEND)
    registered = [
        exc_type for exc_type, *_rest in EXPECTED if exc_type in app.exception_handlers
    ]

    assert len(registered) == len(EXPECTED)
    assert len(set(registered)) == len(EXPECTED)


def test_phase_one_detail_shape_still_served() -> None:
    """一期端点的 ``{"detail"}`` 简形没有被表驱动的改造顺带改掉。"""
    response = _client_raising(ConfigError("配置缺失")).get("/probe-error")

    assert response.status_code == 400
    assert response.json() == {"detail": "配置缺失"}
    assert "problem+json" not in response.headers["content-type"]
