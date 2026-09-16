"""strategies 域端点：用户级策略库 + 目录内批次生命周期（二期新增）。

两个 router 共存于本模块（同一域的两个面）：
- ``library_router``（/api/strategies）：库 CRUD / copy / rebind（同提示词库的
  直连管理方式）；
- ``batches_router``（/api/workdirs/{wid}/batches）：新建（library copy-on-apply /
  scratch）、改名 / 描述（组合不可改——批次是库策略的应用副本）、停用召回、
  删除、排除打包名单。

新建批次为 201 + Location；其余写操作返回操作后的现状视图。错误一律
problem+json（404 strategy/batch/workdir 不存在、400 名字 / 引用不合法、
500 元数据损坏经 workdir 域异常）。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request, Response

from ..runs import BatchRunner
from ..strategies import (
    BatchEntry,
    LibraryStrategy,
    add_exclusions,
    apply_library_strategy,
    copy_strategy,
    create_batch,
    create_strategy,
    delete_batch,
    delete_strategy,
    get_batch,
    get_strategy,
    list_batches,
    list_strategies,
    missing_refs,
    parse_seq,
    product_count,
    rebind_strategy,
    remove_exclusions,
    set_batch_active,
    update_batch,
    update_strategy,
)
from ..workdir import WorkdirRegistry
from .schemas import (
    BatchCreateRequest,
    BatchUpdateRequest,
    BatchView,
    ExclusionsRequest,
    ExclusionsView,
    Problem,
    StrategyRebindRequest,
    StrategySaveRequest,
    StrategyView,
)

library_router = APIRouter(prefix="/api/strategies", tags=["策略库"])
batches_router = APIRouter(
    prefix="/api/workdirs/{wid}/batches", tags=["批次（策略实例）"]
)


def _to_strategy_view(entry: LibraryStrategy) -> StrategyView:
    """库策略 → 响应模型（健康度现查）。"""
    problems = missing_refs(entry)
    return StrategyView(
        id=entry.id,
        name=entry.name,
        description=entry.description,
        endpoint=entry.endpoint,
        prompt=entry.prompt,
        skills=entry.skills,
        available=not problems,
        missing_refs=problems,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _to_batch_view(wid: str, entry: BatchEntry) -> BatchView:
    """批次记录 → 响应模型（产物计数现查）。"""
    workdir = Path(WorkdirRegistry.get(wid).path)
    return BatchView(
        id=f"s{entry.seq}",
        seq=entry.seq,
        name=entry.name,
        description=entry.description,
        active=entry.active,
        created_at=entry.created_at,
        product_count=product_count(workdir, entry.seq),
    )


def _workdir_path(wid: str) -> Path:
    """wid → 工作目录路径（未登记 404 由异常处理器翻译）。"""
    return Path(WorkdirRegistry.get(wid).path)


def _stop_registry_runner(request: Request, workdir: Path, seq: int) -> None:
    """该批次正在跑批则请求停止（停用批次中断运行的设计语义；无运行即空操作）。

    与 routes_runs 的注册表访问同一约定：按工作目录 realpath 键控、命中后校验
    批次归属——停用 s2 不能误停 s1 的运行。停止是协作式的（当前条目在安全点
    停下），本函数置位信号即返回、不等运行结束。
    """
    registry = request.app.state.run_registry
    runner = registry.get(str(workdir))
    if isinstance(runner, BatchRunner) and runner.snapshot()["batch"] == seq:
        runner.stop()


# --------------------------------------------------------------------------
# 策略库
# --------------------------------------------------------------------------


@library_router.get("", response_model=list[StrategyView])
def list_library() -> list[StrategyView]:
    """列出全部库策略（按显示名排序），健康度现查。"""
    return [_to_strategy_view(entry) for entry in list_strategies()]


@library_router.post(
    "",
    status_code=201,
    response_model=StrategyView,
    responses={
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "名字为空 / 引用不存在（strategy-name-invalid / strategy-refs-invalid）",
        },
    },
)
def create_library_entry(body: StrategySaveRequest) -> StrategyView:
    """新建库策略（引用必须现存在）。"""
    entry = create_strategy(
        name=body.name,
        description=body.description,
        endpoint=body.endpoint,
        prompt=body.prompt,
        skills=body.skills,
    )
    return _to_strategy_view(entry)


@library_router.get(
    "/{strategy_id}",
    response_model=StrategyView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "库策略不存在（problem+json: strategy-not-found）",
        },
    },
)
def get_library_entry(strategy_id: str) -> StrategyView:
    """按 ID 查库策略。"""
    return _to_strategy_view(get_strategy(strategy_id))


@library_router.put(
    "/{strategy_id}",
    response_model=StrategyView,
    responses={
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "名字为空 / 引用不存在（strategy-name-invalid / strategy-refs-invalid）",
        },
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "库策略不存在（problem+json: strategy-not-found）",
        },
    },
)
def update_library_entry(strategy_id: str, body: StrategySaveRequest) -> StrategyView:
    """整条更新库策略（策略页「保存」的落点；组合整体替换）。"""
    entry = update_strategy(
        strategy_id,
        name=body.name,
        description=body.description,
        endpoint=body.endpoint,
        prompt=body.prompt,
        skills=body.skills,
    )
    return _to_strategy_view(entry)


@library_router.delete(
    "/{strategy_id}",
    status_code=204,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "库策略不存在（problem+json: strategy-not-found）",
        },
    },
)
def delete_library_entry(strategy_id: str) -> Response:
    """删除库策略（已应用的批次不受影响——copy-on-apply 持有内容副本）。"""
    delete_strategy(strategy_id)
    return Response(status_code=204)


@library_router.post(
    "/{strategy_id}/copy",
    status_code=201,
    response_model=StrategyView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "库策略不存在（problem+json: strategy-not-found）",
        },
    },
)
def copy_library_entry(strategy_id: str) -> StrategyView:
    """复制一份（派生变体：新 ID、内容原样）。"""
    return _to_strategy_view(copy_strategy(strategy_id))


@library_router.post(
    "/{strategy_id}/rebind",
    response_model=StrategyView,
    responses={
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "新引用不存在（problem+json: strategy-refs-invalid）",
        },
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "库策略不存在（problem+json: strategy-not-found）",
        },
    },
)
def rebind_library_entry(strategy_id: str, body: StrategyRebindRequest) -> StrategyView:
    """重新指定缺失引用（只更新提供的引用位，其余保持不变）。"""
    entry = rebind_strategy(
        strategy_id,
        endpoint=body.endpoint,
        prompt=body.prompt,
        skills=body.skills,
    )
    return _to_strategy_view(entry)


# --------------------------------------------------------------------------
# 批次（= 策略 × 工作目录）
# --------------------------------------------------------------------------


@batches_router.get("", response_model=list[BatchView])
def list_workdir_batches(wid: str) -> list[BatchView]:
    """列出工作目录全部批次（按序号升序，含停用的——设置页要能召回）。

    打标页顶栏策略下拉与工作目录设置页策略区块的数据源。
    """
    workdir = _workdir_path(wid)
    return [_to_batch_view(wid, entry) for entry in list_batches(workdir)]


@batches_router.post(
    "",
    status_code=201,
    response_model=BatchView,
    responses={
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "引用不存在（problem+json: strategy-refs-invalid）",
        },
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或库策略不存在（workdir-not-found / strategy-not-found）",
        },
        422: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "请求体按 type 缺必填字段（FastAPI 校验）",
        },
    },
)
def create_workdir_batch(wid: str, body: BatchCreateRequest) -> Response:
    """新建批次：library = copy-on-apply 应用库策略（记来源）/ scratch = 从零配置。

    201 + Location 指向新批次（REST 惯例：创建成功告诉客户端新资源在哪）。
    """
    workdir = _workdir_path(wid)
    if body.type == "library":
        # id 非空已由 BatchCreateRequest 的模型校验器保证（422 挡在前），cast 仅为收窄。
        entry = apply_library_strategy(
            workdir,
            cast("str", body.id),
            name=body.name,
            description=body.description,
        )
    else:
        # name / endpoint / prompt 非空同样由模型校验器保证。
        entry = create_batch(
            workdir,
            name=cast("str", body.name),
            description=body.description or "",
            endpoint=cast("str", body.endpoint),
            prompt=cast("str", body.prompt),
            skills=body.skills or [],
        )
    view = _to_batch_view(wid, entry)
    return Response(
        status_code=201,
        content=view.model_dump_json(),
        media_type="application/json",
        headers={"Location": f"/api/workdirs/{wid}/batches/{view.id}"},
    )


@batches_router.get(
    "/{sN}",
    response_model=BatchView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
    },
)
def get_batch_detail(wid: str, sN: str) -> BatchView:
    """按序号查单个批次（新建 201 的 Location 指向这里，可解析）。"""
    entry = get_batch(_workdir_path(wid), parse_seq(sN))
    return _to_batch_view(wid, entry)


@batches_router.patch(
    "/{sN}",
    response_model=BatchView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
        422: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "请求体含未声明字段（组合不可改，extra=forbid）",
        },
    },
)
def patch_batch(wid: str, sN: str, body: BatchUpdateRequest) -> BatchView:
    """改名 / 描述（纯显示元数据）。

    组合不可改——工作目录下的策略是库策略的应用副本（copy-on-apply），
    库端编辑不传染、已应用批次不提供就地改组合；想换组合 = 新建批次。
    「保存策略」钮的落点是策略库（PUT /api/strategies/{id}），不是这里。
    """
    workdir = _workdir_path(wid)
    entry = update_batch(
        workdir,
        parse_seq(sN),
        name=body.name,
        description=body.description,
    )
    return _to_batch_view(wid, entry)


@batches_router.post(
    "/{sN}/hide",
    response_model=BatchView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
    },
)
def hide_batch(wid: str, sN: str, request: Request) -> BatchView:
    """停用批次：不出现在下拉 / 列表 / 打包选项，产物全部保留。

    该批次正在跑批则中断本次运行（design 定案「停用 = 停用」沿用手动停止语义）：
    查运行注册表命中本批次即置位协作取消，当前条目在安全点停下。
    """
    seq = parse_seq(sN)
    workdir = _workdir_path(wid)
    _stop_registry_runner(request, workdir, seq)
    entry = set_batch_active(workdir, seq, active=False)
    return _to_batch_view(wid, entry)


@batches_router.post(
    "/{sN}/unhide",
    response_model=BatchView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
    },
)
def unhide_batch(wid: str, sN: str) -> BatchView:
    """召回已停用的批次。"""
    entry = set_batch_active(_workdir_path(wid), parse_seq(sN), active=True)
    return _to_batch_view(wid, entry)


@batches_router.delete(
    "/{sN}",
    status_code=204,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
    },
)
def delete_workdir_batch(wid: str, sN: str) -> Response:
    """删除批次：该策略全部产物 txt + 快照 + state.json 记录 + 排除名单一并移除。

    删前告知条数由界面负责（批次视图的 product_count 即数据源）。
    """
    delete_batch(_workdir_path(wid), parse_seq(sN))
    return Response(status_code=204)


@batches_router.post(
    "/{sN}/exclusions",
    response_model=ExclusionsView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
    },
)
def add_batch_exclusions(wid: str, sN: str, body: ExclusionsRequest) -> ExclusionsView:
    """把条目加入排除打包名单（幂等去重），返回当前名单。

    名单随批次元数据持久、跨会话存活；改动全程持运行锁（T36 起生效）。
    """
    seq = parse_seq(sN)
    items = add_exclusions(_workdir_path(wid), seq, body.items)
    return ExclusionsView(id=f"s{seq}", seq=seq, items=items)


@batches_router.delete(
    "/{sN}/exclusions",
    response_model=ExclusionsView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
    },
)
def remove_batch_exclusions(
    wid: str, sN: str, body: ExclusionsRequest
) -> ExclusionsView:
    """把条目移出排除打包名单（撤销排除），返回当前名单。"""
    seq = parse_seq(sN)
    items = remove_exclusions(_workdir_path(wid), seq, body.items)
    return ExclusionsView(id=f"s{seq}", seq=seq, items=items)


__all__ = ["batches_router", "library_router"]
