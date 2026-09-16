"""条目视图：打标页左列六分组的读模型（二期 T37）。

**条目状态不落库**（design「条目状态不落库」）——每次请求现算，数据源三处：文件系统
现状（素材在不在、产物在不在且非空白）、运行流水（每条素材最近一次尝试的结果与
原因）、``state.json`` 的重试列表（叠加标记）。没有状态表，也就不存在「状态表与
文件系统漂移」这回事。

六分组 = **四个互斥状态位**（排队中 / 已完成 / 未完成 / 缺失）+ **重试列表**（叠加
标记而非状态位：条目同时留在自己的状态分组里，只是多一个「已排重试」标）+ **未导入**
（素材级待办清单，不属于条目——工作目录里没登记过的文件，交给用户导入或删除）。

「打标后素材已变更」不在本视图里：判定它要现算每条素材的哈希，而完整性对账的纪律是
「只在交付 / 打包这类天然要读全量的时刻做」，不在每次列条目时做（T39 的
``integrity/scan`` 负责，界面把扫描结果叠加到本视图上）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..strategies.batches import get_batch
from ..workdir.assets import (
    ImportOrigin,
    media_kind,
    product_filename,
    product_has_content,
    registered_origins,
    scan_assets,
    unimported_files,
)
from ..workdir.store import WorkdirStore
from .journal import ItemRecord, load_latest_item_records
from .runner import RETRYABLE_REASON_CODES, read_retry_list

__all__ = [
    "GROUP_DONE",
    "GROUP_FAILED",
    "GROUP_MISSING",
    "GROUP_QUEUED",
    "GROUP_RETRY",
    "GROUP_UNIMPORTED",
    "ITEM_GROUPS",
    "ItemRow",
    "ItemView",
    "build_item_view",
]

#: 排队中：还没打过（或产物异常待重打），也没有失败记录。
GROUP_QUEUED = "queued"
#: 已完成：该批次的产物 txt 存在且非空白。
GROUP_DONE = "done"
#: 未完成：最近一次尝试失败（行内带原因）。
GROUP_FAILED = "failed"
#: 缺失：登记在册但素材已不在工作目录（第四个状态位，与前三个互斥）。
GROUP_MISSING = "missing"
#: 重试列表：叠加标记的聚合视图（条目同时留在自己的状态分组里）。
GROUP_RETRY = "retry"
#: 未导入：工作目录里没登记过的文件（素材级待办，不属于条目）。
GROUP_UNIMPORTED = "unimported"

#: 六分组的键与固定顺序（界面按自己的版式排，这份顺序只保证响应稳定可比对）。
ITEM_GROUPS = (
    GROUP_QUEUED,
    GROUP_DONE,
    GROUP_FAILED,
    GROUP_MISSING,
    GROUP_RETRY,
    GROUP_UNIMPORTED,
)


@dataclass(frozen=True)
class ItemRow:
    """左列的一行。

    字段按「哪一类行用得上」分工，用不上的为 None——六分组共用一个行形状，界面
    不必为每个分组各写一套解析：

    - 全部行：``item`` / ``name`` / ``media`` / ``status`` / ``can_retry`` / ``in_retry``；
    - 未完成行：``attempt`` / ``reason_code`` / ``message``（原因码给界面判「可不可
      重试」的措辞，消息是运行流水里那句人读原因）；
    - 缺失行：``source``（来源文件的完整路径，悬停提示与「从别处导入」都要它）与
      ``recoverable``（来源那儿还有没有这份素材，决定「重新导入」可不可点）；
    - 未导入行：``reason``（标准措辞）与 ``size`` / ``limit``（超限那一类给出实际
      字节数与该档上限，界面自己拼「412 MiB ＞ 100 MiB」这类对比）。

    Attributes:
        item: 条目身份 = 素材主干（不含扩展名）。
        name: 展示用文件名（含扩展名）。
        media: ``image`` / ``video`` / ``file``（界面选图标）。
        status: 状态位；未导入行的 status 就是 ``unimported``。
        can_retry: 能不能加入重试列表（已完成与可重试类失败能；排队中没什么可重试、
            缺失要先补素材、不可重试失败要先解决格式问题——PRD F7 的置灰依据）。
        in_retry: 是否已在重试列表里（叠加标记，界面显示「已排重试」）。
    """

    item: str
    name: str
    media: str
    status: str
    can_retry: bool
    in_retry: bool
    attempt: int | None = None
    reason_code: str | None = None
    message: str | None = None
    source: str | None = None
    recoverable: bool | None = None
    size: int | None = None
    limit: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ItemView:
    """一次条目视图请求的结果（六个分组 + 回显查询词）。

    Attributes:
        batch: 批次序号（响应里带上它，防前端把 s2 的视图渲染进 s1 的列表）。
        query: 生效的搜索词（空串 = 没过滤）。
        groups: 分组键 → 该组的行；六组恒在（空组给空列表，界面不必判键存在）。
    """

    batch: int
    query: str
    groups: dict[str, list[ItemRow]]


def build_item_view(workdir: Path, seq: int, *, query: str = "") -> ItemView:
    """算出一个批次的条目视图（六分组）。

    Args:
        workdir: 工作目录路径（须已登记）。
        seq: 批次序号（sN 的 N）。
        query: 搜索词——按文件名做大小写不敏感的子串匹配，命中行留下、其余滤掉；
            分组计数随之变小（与原型的搜索行为同口径：搜的时候计数就是命中数）。

    Returns:
        六个分组的视图。

    Raises:
        BatchNotFoundError: 批次不存在（strategies 域异常冒泡）。
        WorkdirMetadataCorruptedError: ``imports.jsonl`` 或 ``state.json`` 损坏。
        RunJournalCorruptedError: 历史运行流水损坏。
    """
    get_batch(workdir, seq)  # 批次不存在当场 404，不去算一份没人要的视图
    store = WorkdirStore(workdir)
    origins = registered_origins(store)
    assets = scan_assets(workdir)
    present_names = {path.name for path in assets.values()}
    latest = load_latest_item_records(store.runs_dir, seq)
    retry_list = read_retry_list(workdir, seq)
    retry_set = set(retry_list)

    rows = _build_item_rows(workdir, seq, origins, present_names, latest, retry_set)
    unimported = _build_unimported_rows(workdir, set(origins))

    by_item = {row.item: row for row in rows}
    # 重试列表按名单顺序成组（名单顺序即重试顺序）；名单里出现视图没有的主干
    # （手工改过 state.json 才会发生）直接跳过——不为它现造一个状态。
    retry_rows = [by_item[item] for item in retry_list if item in by_item]

    needle = query.strip().lower()
    grouped: dict[str, list[ItemRow]] = {key: [] for key in ITEM_GROUPS}
    for row in [*rows, *unimported]:
        grouped[row.status].append(row)
    grouped[GROUP_RETRY] = retry_rows
    if needle:
        grouped = {
            key: [row for row in value if needle in row.name.lower()]
            for key, value in grouped.items()
        }
    return ItemView(batch=seq, query=query, groups=grouped)


def _build_item_rows(
    workdir: Path,
    seq: int,
    origins: dict[str, ImportOrigin],
    present_names: set[str],
    latest: dict[str, ItemRecord],
    retry_set: set[str],
) -> list[ItemRow]:
    """登记在册的条目逐条定状态（按主干排序，响应顺序确定）。

    同一主干可能有多份登记名（旧扩展名的那份被删、换名重新导入过）：在盘的那份
    优先，都不在盘时取最近一次登记的名做缺失行——条目身份是主干，一个主干只出一行。
    """
    by_stem: dict[str, list[ImportOrigin]] = {}
    for origin in origins.values():
        by_stem.setdefault(Path(origin.name).stem, []).append(origin)

    rows: list[ItemRow] = []
    for stem in sorted(by_stem):
        entries = by_stem[stem]
        present = next(
            (origin for origin in entries if origin.name in present_names), None
        )
        in_retry = stem in retry_set
        if present is not None:
            rows.append(
                _status_row(
                    workdir, seq, stem, present.name, latest.get(stem), in_retry
                )
            )
            continue
        newest = max(entries, key=lambda origin: origin.imported_at)
        rows.append(_missing_row(stem, newest, in_retry))
    return rows


def _status_row(
    workdir: Path,
    seq: int,
    stem: str,
    name: str,
    record: ItemRecord | None,
    in_retry: bool,
) -> ItemRow:
    """素材在盘时定状态位：失败记录优先于产物，其次产物，其余排队中。

    失败优先于「有产物」不是随手排的顺序：上一轮成功产出过、这一轮因素材被换而重打
    却失败了，此时盘上留着的是**旧素材的产物**——PRD F4 把这种情况明确算「未完成」
    （哈希比对不一致按未完成重打），报成「已完成」会让用户以为新素材已经打好了。
    """
    media = media_kind(name)
    if record is not None and record.status == "failed":
        return ItemRow(
            item=stem,
            name=name,
            media=media,
            status=GROUP_FAILED,
            can_retry=record.reason_code in RETRYABLE_REASON_CODES,
            in_retry=in_retry,
            attempt=record.attempt,
            reason_code=record.reason_code,
            message=record.message,
        )
    if product_has_content(workdir / product_filename(seq, stem)):
        return ItemRow(
            item=stem,
            name=name,
            media=media,
            status=GROUP_DONE,
            # 已完成条目可以重打（覆盖旧 txt）——PRD F7 允许把满意的条目也收进名单。
            can_retry=True,
            in_retry=in_retry,
        )
    return ItemRow(
        item=stem,
        name=name,
        media=media,
        status=GROUP_QUEUED,
        # 排队中没什么可「重试」：它本来就在下一次全量跑批的计划里。
        can_retry=False,
        in_retry=in_retry,
    )


def _missing_row(stem: str, origin: ImportOrigin, in_retry: bool) -> ItemRow:
    """素材缺失行：对来源路径做只读探测，决定「重新导入」可不可点。

    探测只看来源那儿还有没有这份文件（``is_file``，不复制不写盘）——就地采用的
    来源就是工作目录自身，素材既然没了，探测自然落空，界面据此改走「从别处导入」。
    """
    source_file = Path(origin.source) / origin.name
    return ItemRow(
        item=stem,
        name=origin.name,
        media=media_kind(origin.name),
        status=GROUP_MISSING,
        # 素材都没了，重试必然失败——要先补回素材（PRD F6）。
        can_retry=False,
        in_retry=in_retry,
        source=str(source_file),
        recoverable=source_file.is_file(),
    )


def _build_unimported_rows(workdir: Path, registered: set[str]) -> list[ItemRow]:
    """未导入分组的行：工作目录里不会成为条目的文件，带标准原因措辞。"""
    return [
        ItemRow(
            item=Path(entry.name).stem,
            name=entry.name,
            media=media_kind(entry.name),
            status=GROUP_UNIMPORTED,
            can_retry=False,
            in_retry=False,
            reason=entry.reason,
            size=entry.size,
            limit=entry.limit,
        )
        for entry in unimported_files(workdir, registered)
    ]
