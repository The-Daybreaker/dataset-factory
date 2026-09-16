"""条目视图与产物预览端点（二期 T37）。

- ``GET …/batches/{sN}/items``：打标页左列六分组的读模型（可按文件名搜索）。条目状态
  **不落库**——每次请求从「文件系统现状 + 运行流水 + 重试列表」现算，界面看到的永远
  是磁盘现状，不存在状态表与文件系统漂移（design「条目状态不落库」）。SSE 断线重连
  后的「先全量拉取条目视图刷新界面」打的就是这个端点。
- ``GET …/batches/{sN}/items/{item}/txt``：该批次这个条目的产物正文（text/plain）。
  策略胶囊对比视图逐栏取数——每一栏是一次对本端点的调用，只有 sN 不同。

素材原件（asset）不在本模块：它是**工作目录级**资源（不属于任何批次，缺失条目与
未登记文件之外的预览都按同一份素材），挂在 routes_workdir 下。

错误一律 problem+json（api.problems）。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from ..runs import ItemRow, build_item_view
from ..strategies import get_batch, parse_seq
from ..workdir import ProductNotFoundError, WorkdirRegistry, product_path
from .schemas import ItemListView, ItemRowView, Problem

router = APIRouter(prefix="/api/workdirs/{wid}/batches/{sN}/items", tags=["条目"])


def _workdir_path(wid: str) -> Path:
    """wid → 工作目录路径（未登记 404 由异常处理器翻译）。"""
    return Path(WorkdirRegistry.get(wid).path)


def _to_row(row: ItemRow) -> ItemRowView:
    """核心行 → 响应模型（字段同名同形，Literal 在边界把词汇校验一遍）。"""
    return ItemRowView(**asdict(row))


@router.get(
    "",
    response_model=ItemListView,
    responses={
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid 或批次不存在（workdir-not-found / batch-not-found）",
        },
        500: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "导入记录 / 状态文件 / 运行流水损坏"
            "（workdir-metadata-corrupted / run-journal-corrupted）",
        },
    },
)
def list_items(
    wid: str,
    sN: str,
    q: str = Query(
        default="",
        description="搜索词：按文件名做大小写不敏感的子串匹配；缺省 = 不过滤。"
        "过滤后各组计数随之变小（与原型的搜索行为同口径）",
    ),
) -> ItemListView:
    """条目视图：六个分组一次给全（左列整列的数据源）。

    六分组 = 四个互斥状态位（排队中 / 已完成 / 未完成 / 缺失）+ 重试列表（叠加标记，
    条目同时留在自己的状态分组里）+ 未导入（工作目录里没登记过的文件）。
    """
    view = build_item_view(_workdir_path(wid), parse_seq(sN), query=q)
    return ItemListView(
        batch=view.batch,
        query=view.query,
        groups={
            key: [_to_row(row) for row in rows] for key, rows in view.groups.items()
        },
    )


@router.get(
    "/{item}/txt",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"text/plain": {"schema": {"type": "string"}}},
            "description": "产物正文（只含 caption 本身，无任何标记或元数据）",
        },
        400: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "条目名不合法或解析后越出工作目录（asset-path-invalid）",
        },
        404: {
            "model": Problem,
            "content": {"application/problem+json": {}},
            "description": "wid / 批次不存在，或该条目没有可用产物"
            "（workdir-not-found / batch-not-found / product-not-found）",
        },
    },
)
def read_item_product(wid: str, sN: str, item: str) -> PlainTextResponse:
    """该批次某条目的产物 txt 正文。

    「没有可用产物」统一 404：文件不存在、文件为空 / 全空白、文件读不出三种情况
    对用户是同一件事——这一栏没有 caption 可看。空与读不出在条目视图里同样被算作
    未完成（产物异常、可重打），两侧一个口径。
    """
    workdir = _workdir_path(wid)
    seq = parse_seq(sN)
    get_batch(workdir, seq)  # 批次不存在当场 404，不去拼一个没人要的文件路径
    path = product_path(workdir, seq, item)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProductNotFoundError(
            f"批次 s{seq} 还没有条目「{item}」的产物——它可能尚未打标，"
            "或产物已随批次清理被移除。",
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductNotFoundError(
            f"批次 s{seq} 条目「{item}」的产物读不出（{exc}）——"
            "文件可能已损坏，请把该条目加入重试列表重打。",
        ) from exc
    if not content.strip():
        raise ProductNotFoundError(
            f"批次 s{seq} 条目「{item}」的产物是空的（产物异常）——"
            "请把该条目加入重试列表重打。",
        )
    return PlainTextResponse(content)


__all__ = ["router"]
