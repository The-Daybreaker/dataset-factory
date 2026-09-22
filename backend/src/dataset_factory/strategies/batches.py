"""目录内批次生命周期：新建 / 配置补丁 / 停用召回 / 删除 / 排除打包名单。

批次 = 策略 × 工作目录（design 项 2 修订口径）：序号 ``sN`` 既标识策略实例也
标识批次；**序号只增不复用**（删掉 s2 后新建的是 s3，历史运行记录里的 s2
不会与新的 s2 混淆）。批次数据随工作目录走：

- ``state.json``（经 WorkdirStore 读写）：批次列表（序号 → 快照文件 / 名称 /
  描述 / 活跃状态 / 创建时刻）+ 各批次排除打包名单；
- ``.dsf/strategies/s<N>.json``：每套策略一份应用时刻全文快照（snapshot.py 装配）。

新建 = 应用（apply）：从库策略 copy-on-apply（记来源：库 ID + 应用时刻组合哈希）
或从零配置直接装配；此后库里的改动 / 删除不影响已应用批次。**组合不可改**
（2026-09-16 用户澄清）：工作目录下的策略是库策略的只读副本，配置补丁只支持
改名 / 描述（纯显示元数据）；想换组合 = 新建批次（序号递增）。

排除名单 = 用户排除出本次打包的条目清单，随批次元数据持久、跨会话存活；
只被读来看（打包资格判定在 export 包），不参与运行判定。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .._clock import now_iso
from .._fs import atomic_write_text
from ..workdir import (
    WorkdirMetadataCorruptedError,
    WorkdirRegistry,
    WorkdirStore,
    product_pattern,
)
from ..workdir.locks import RunLock, workdir_write
from .errors import BatchNotFoundError, StrategyNotFoundError, StrategyRefsError
from .snapshot import StrategySnapshot, build_snapshot
from .store import (
    get_strategy,
    missing_refs,
    require_refs_exist,
    strategy_content_hash,
)

__all__ = [
    "BatchEntry",
    "add_exclusions",
    "apply_library_strategy",
    "create_batch",
    "delete_batch",
    "get_batch",
    "list_batches",
    "parse_seq",
    "product_count",
    "read_snapshot",
    "remove_exclusions",
    "set_batch_active",
    "update_batch",
]


@dataclass
class BatchEntry:
    """state.json 里的一条批次记录（摘要元数据；组合全文在快照文件里）。"""

    seq: int
    name: str
    description: str
    snapshot: str  # 快照文件名（sN.json）
    active: bool  # False = 停用（隐藏）：不出现在下拉 / 列表 / 打包选项
    created_at: str


def parse_seq(raw: str) -> int:
    """解析路径参数 ``sN`` 为序号（严格 ``s`` + 正整数）；不合法按不存在处理。"""
    if not raw.startswith("s") or not raw[1:].isdigit() or int(raw[1:]) < 1:
        raise BatchNotFoundError(f"批次标识「{raw}」不合法——序号形如 s1、s2。")
    return int(raw[1:])


def _batches_raw(state: dict[str, object]) -> list[object]:
    """从 state 取批次原始列表（缺键视为空；形状不对 fail loud）。"""
    value = state.get("batches")
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorkdirMetadataCorruptedError(
            "工作目录状态文件的批次列表损坏——请检查 .dsf/state.json。"
        )
    return list(cast("list[object]", value))


def _entry_from_dict(data: object) -> BatchEntry:
    """字典 → 批次记录（形状不对 fail loud）。"""
    if not isinstance(data, dict):
        raise WorkdirMetadataCorruptedError("工作目录状态文件的批次记录形状不对。")
    record = cast("dict[str, object]", data)
    seq = record.get("seq")
    name = record.get("name")
    description = record.get("description")
    snapshot = record.get("snapshot")
    active = record.get("active")
    created_at = record.get("created_at")
    if (
        not isinstance(seq, int)
        or isinstance(seq, bool)
        or not isinstance(name, str)
        or not isinstance(description, str)
        or not isinstance(snapshot, str)
        or not isinstance(active, bool)
        or not isinstance(created_at, str)
    ):
        raise WorkdirMetadataCorruptedError(
            "工作目录状态文件的批次记录缺字段或类型不对——请检查 .dsf/state.json。"
        )
    return BatchEntry(
        seq=seq,
        name=name,
        description=description,
        snapshot=snapshot,
        active=active,
        created_at=created_at,
    )


def list_batches(workdir: Path) -> list[BatchEntry]:
    """列出工作目录全部批次（按序号升序；含停用的——设置页要能召回）。"""
    store = WorkdirStore(workdir)
    return sorted(_entries_from_state(store.read_state()), key=lambda entry: entry.seq)


def get_batch(workdir: Path, seq: int) -> BatchEntry:
    """按序号查批次；不存在抛 BatchNotFoundError。"""
    return _require_entry(list_batches(workdir), seq)


def _entries_from_state(state: dict[str, object]) -> list[BatchEntry]:
    """从 state 字典取批次列表（缺键视为空；形状不对 fail loud）。"""
    return [_entry_from_dict(item) for item in _batches_raw(state)]


def _require_entry(entries: list[BatchEntry], seq: int) -> BatchEntry:
    """按序号取批次条目；不存在抛 BatchNotFoundError（读路径与锁内判定共用）。"""
    for entry in entries:
        if entry.seq == seq:
            return entry
    raise BatchNotFoundError(
        f"批次 s{seq} 不在工作目录中——可能已被删除，请刷新后重试。",
    )


def _entries_into_state(
    state: dict[str, object],
    entries: list[BatchEntry],
    *,
    seq_floor: int = 0,
) -> None:
    """把批次列表写回 state 字典；序号计数只增不减。

    ``next_seq`` 落盘值不低于「现有批次最大序号 + 1」与 ``seq_floor`` 的较大者，
    防止删除路径把计数写回去导致序号复用。``seq_floor`` 供删除路径传「移除前」
    的最大序号 + 1：legacy state（无 next_seq 键）按最大序号兜底时，被删的那个
    序号也已烧掉、删完不回退。
    """
    state["batches"] = [
        {
            "seq": entry.seq,
            "name": entry.name,
            "description": entry.description,
            "snapshot": entry.snapshot,
            "active": entry.active,
            "created_at": entry.created_at,
        }
        for entry in entries
    ]
    current = state.get("next_seq")
    current_floor = (
        current if isinstance(current, int) and not isinstance(current, bool) else 0
    )
    state["next_seq"] = max(
        current_floor,
        max((entry.seq for entry in entries), default=0) + 1,
        seq_floor,
    )


def _replace_entry(entries: list[BatchEntry], entry: BatchEntry) -> list[BatchEntry]:
    """把改过的条目按序号替换回列表（保持原有顺序）。"""
    return [entry if item.seq == entry.seq else item for item in entries]


def product_count(workdir: Path, seq: int) -> int:
    """该批次现有产物 txt 数（删除确认弹窗的「将删多少个」数据源）。"""
    store = WorkdirStore(workdir)
    return sum(1 for _ in store.dsf_path.parent.glob(product_pattern(seq)))


def _write_snapshot(store: WorkdirStore, seq: int, snapshot: StrategySnapshot) -> None:
    """原子写一份策略快照（``.dsf/strategies/s<N>.json``，人可读）。"""
    path = store.strategies_dir / f"s{seq}.json"
    atomic_write_text(
        path, json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2)
    )


def _reserve_seq(store: WorkdirStore) -> int:
    """在状态锁内分配并预占下一个序号（只增不复用），返回分配到的序号。

    预占与登记分两段：序号在锁内定死（防 Web 与 CLI 并发建批撞号），快照文件
    随后在**锁外**写（非状态 IO 不进临界区，design「并发保护」），最后才在锁内
    登记条目。两段之间崩溃最坏烧掉一个序号——与删批烧号同性质、无观察者；
    换来的是 state.json 里的批次条目必有快照文件在手，不会出现「登记了却
    读不到快照」的批次。
    """

    def mutator(state: dict[str, object]) -> int:
        entries = _entries_from_state(state)
        raw = state.get("next_seq")
        if isinstance(raw, int) and not isinstance(raw, bool):
            seq = raw
        else:
            seq = max((entry.seq for entry in entries), default=0) + 1
        state["next_seq"] = seq + 1
        return seq

    return store.mutate_state(mutator)


def _append_batch_entry(store: WorkdirStore, entry: BatchEntry) -> BatchEntry:
    """在状态锁内把批次条目登记进 state.json（调用前快照文件已落盘）。"""

    def mutator(state: dict[str, object]) -> BatchEntry:
        entries = _entries_from_state(state)
        entries.append(entry)
        _entries_into_state(state, entries)
        return entry

    return store.mutate_state(mutator)


@workdir_write
def create_batch(
    workdir: Path,
    *,
    name: str,
    description: str,
    endpoint_id: str,
    prompt_id: str,
    skill_ids: list[str],
    source: dict[str, object] | None = None,
) -> BatchEntry:
    """从零配置新建一个批次：校验引用 → 锁内预占序号 → 锁外写快照 → 锁内登记。

    引用一律传各资产的稳定 ID（2026-09-23 ID 化）。

    Raises:
        StrategyRefsError: 引用的端点 / 提示词 / Skill 不存在。
        BatchNotFoundError: state.json 形状不对。
    """
    require_refs_exist(endpoint_id, prompt_id, skill_ids)
    now = now_iso()
    snapshot = build_snapshot(
        endpoint_id, prompt_id, skill_ids, built_at=now, source=source
    )
    store = WorkdirStore(workdir)
    seq = _reserve_seq(store)
    _write_snapshot(store, seq, snapshot)
    return _append_batch_entry(
        store,
        BatchEntry(
            seq=seq,
            name=name,
            description=description,
            snapshot=f"s{seq}.json",
            active=True,
            created_at=now,
        ),
    )


@workdir_write
def apply_library_strategy(
    workdir: Path,
    strategy_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> BatchEntry:
    """应用库策略到工作目录（copy-on-apply）：锁内预占序号、记来源、装配快照。

    库策略引用已缺失（置灰）时拒绝应用——应用出来的批次会带病运行。
    之后库里的改动 / 删除不影响本批次（批次持有内容副本）。
    """
    library_entry = get_strategy(strategy_id)
    problems = missing_refs(library_entry)
    if problems:
        raise StrategyRefsError(
            "该策略引用的资产已不存在，暂不可应用（可回到策略库「重新指定」后重试）："
            + "；".join(problems)
        )
    now = now_iso()
    snapshot = build_snapshot(
        library_entry.endpoint_id,
        library_entry.prompt_id,
        library_entry.skill_ids,
        built_at=now,
        source={
            "strategy_id": strategy_id,
            "strategy_sha256": strategy_content_hash(library_entry),
        },
    )
    store = WorkdirStore(workdir)
    seq = _reserve_seq(store)
    _write_snapshot(store, seq, snapshot)
    return _append_batch_entry(
        store,
        BatchEntry(
            seq=seq,
            name=name if name is not None else library_entry.name,
            description=description
            if description is not None
            else library_entry.description,
            snapshot=f"s{seq}.json",
            active=True,
            created_at=now,
        ),
    )


def update_batch(
    workdir: Path,
    seq: int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> BatchEntry:
    """配置补丁：改名 / 描述（纯显示元数据，快照不动）。

    组合不可改——工作目录下的策略是库策略的应用副本，想换组合 = 新建批次。
    读—改—写在状态锁内一次完成（``WorkdirStore.mutate_state`` 唯一写入口）。
    """
    store = WorkdirStore(workdir)

    def mutator(state: dict[str, object]) -> BatchEntry:
        entries = _entries_from_state(state)
        entry = _require_entry(entries, seq)
        if name is not None:
            entry.name = name
        if description is not None:
            entry.description = description
        _entries_into_state(state, _replace_entry(entries, entry))
        return entry

    return store.mutate_state(mutator)


def set_batch_active(workdir: Path, seq: int, active: bool) -> BatchEntry:
    """停用（隐藏）/ 召回批次。停用正在运行的策略需中断运行——T36 运行器落地。"""
    store = WorkdirStore(workdir)

    def mutator(state: dict[str, object]) -> BatchEntry:
        entries = _entries_from_state(state)
        entry = _require_entry(entries, seq)
        entry.active = active
        _entries_into_state(state, _replace_entry(entries, entry))
        return entry

    return store.mutate_state(mutator)


@workdir_write
def delete_batch(
    workdir: Path,
    seq: int,
    *,
    cleanup_state: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """删除批次：产物 txt + 快照 + state.json 记录 + 排除名单一并移除。

    运行锁阻止删除正在写出的产物。调用方提供附属状态清理函数，使跨域名单
    与批次条目在同一次状态写入中出清；回调只修改传入字典，不另写文件。

    **文件删除留在状态锁外**：状态临界区只放「读—改—写 state.json」那几毫秒，
    产物可能有几百个文件，逐条 unlink 是系统调用密集的操作，压在临界区里会把
    其它写者（改名单 / 改名 / 跑批收尾出列）挡到状态锁的宽超时上、误报成
    「状态锁卡死」。顺序取「先提交状态、再删文件」：万一删到一半失败，留下的是
    批次已不存在、盘上却还有 `s<N>__*.txt` 的**无主产物**——它正是清理区
    「清理无素材产物」认得、且用户能自己处置的那一类；反过来先删文件再提交状态，
    失败会留下「批次还在、产物已少一半」的坏批次，那是用户看不见的窟窿。

    Returns:
        删除的产物 txt 数（历史 runs/ 保留）。

    Raises:
        BatchNotFoundError: 批次不存在。
    """
    get_batch(workdir, seq)  # 不存在先报错，失败时现场不动
    store = WorkdirStore(workdir)
    lock = RunLock(store.dsf_path)
    lock.acquire({"pid": os.getpid(), "batch": f"s{seq}", "operation": "delete-batch"})
    try:
        products = list(store.dsf_path.parent.glob(product_pattern(seq)))
        for product in products:
            store.validate_cleanup_path(product)
        snapshot_path = store.strategies_dir / f"s{seq}.json"
        store.validate_cleanup_path(snapshot_path)

        def mutator(state: dict[str, object]) -> None:
            entries = _entries_from_state(state)
            _require_entry(entries, seq)
            seq_floor = max((item.seq for item in entries), default=0) + 1
            _entries_into_state(
                state,
                [item for item in entries if item.seq != seq],
                seq_floor=seq_floor,
            )
            exclusions = _exclusions_from_state(state)
            exclusions.pop(str(seq), None)
            state["exclusions"] = exclusions
            if cleanup_state is not None:
                cleanup_state(state)

        store.mutate_state(mutator)
        for product in products:
            product.unlink()
        snapshot_path.unlink(missing_ok=True)
        return len(products)
    finally:
        lock.release()


def _exclusions_from_state(state: dict[str, object]) -> dict[str, list[str]]:
    """从 state 字典取各批次排除名单（缺键视为空；形状不对 fail loud）。"""
    value = state.get("exclusions")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkdirMetadataCorruptedError(
            "工作目录状态文件的排除名单损坏——请检查 .dsf/state.json。"
        )
    result: dict[str, list[str]] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(item, list):
            raise WorkdirMetadataCorruptedError(
                "工作目录状态文件的排除名单形状不对——请检查 .dsf/state.json。"
            )
        result[str(key)] = [str(name) for name in cast("list[object]", item)]
    return result


def read_exclusions(workdir: Path, seq: int) -> list[str]:
    """读取该批次的排除名单，复用写入侧的结构校验。"""
    get_batch(workdir, seq)
    return _exclusions_from_state(WorkdirStore(workdir).read_state()).get(str(seq), [])


def add_exclusions(workdir: Path, seq: int, items: list[str]) -> list[str]:
    """把条目加入该批次的排除名单（幂等去重），返回当前名单。"""
    store = WorkdirStore(workdir)

    def mutator(state: dict[str, object]) -> list[str]:
        _require_entry(_entries_from_state(state), seq)  # 批次不存在先报错
        exclusions = _exclusions_from_state(state)
        current = exclusions.setdefault(str(seq), [])
        for item in items:
            if item not in current:
                current.append(item)
        state["exclusions"] = exclusions
        return list(current)

    return store.mutate_state(mutator)


def remove_exclusions(workdir: Path, seq: int, items: list[str]) -> list[str]:
    """把条目移出该批次的排除名单（撤销排除），返回当前名单。"""
    store = WorkdirStore(workdir)

    def mutator(state: dict[str, object]) -> list[str]:
        _require_entry(_entries_from_state(state), seq)
        exclusions = _exclusions_from_state(state)
        removal = set(items)
        current = [item for item in exclusions.get(str(seq), []) if item not in removal]
        exclusions[str(seq)] = current
        state["exclusions"] = exclusions
        return list(current)

    return store.mutate_state(mutator)


def read_snapshot(workdir: Path, seq: int) -> StrategySnapshot:
    """读一份批次快照（批次概览「快照」弹窗与哈希比对的数据源）。

    Raises:
        BatchNotFoundError: 批次不存在。
        StrategyNotFoundError: 快照文件缺失或损坏（元数据与文件不一致）。
    """
    return read_snapshot_with_hash(workdir, seq)[0]


def read_snapshot_with_hash(workdir: Path, seq: int) -> tuple[StrategySnapshot, str]:
    """从同一次字节读取返回快照与文件哈希，避免显示内容和校验对象不一致。"""
    get_batch(workdir, seq)
    path = WorkdirStore(workdir).strategies_dir / f"s{seq}.json"
    try:
        if not path.resolve().is_relative_to(
            WorkdirStore(workdir).strategies_dir.resolve()
        ):
            raise StrategyNotFoundError("策略快照路径不在策略目录内。")
        raw = path.read_bytes()
        data: object = json.loads(raw.decode("utf-8"))
        return StrategySnapshot.from_json(data), hashlib.sha256(raw).hexdigest()
    except OSError as exc:
        raise StrategyNotFoundError(
            f"策略快照文件无法读取（{path.name}）——批次元数据与快照不一致，请检查后重试。",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise StrategyNotFoundError(
            f"策略快照文件损坏（{path.name}）：{exc}",
        ) from exc


@dataclass(frozen=True)
class StrategyReference:
    """一条库策略出身记录：哪个工作目录的哪个批次应用了它。

    库—快照隔离（copy-on-apply）下的出身对照——批次持有应用时刻的副本，库的
    后续改动 / 删除不影响它们；本记录只作「已被 N 个批次应用」的提示，
    不参与任何运行判定。
    """

    workdir_id: str
    workdir_title: str
    seq: int
    batch_name: str


def find_strategy_references(strategy_id: str) -> list[StrategyReference]:
    """跨全部工作目录查找应用了该库策略的批次（按快照 ``source.strategy_id`` 匹配）。

    单个工作目录 / 快照读不出来（已损坏或被手动删改）就跳过，不挡住其余统计
    ——这是给人看的提示，与「记录只作对账、不进运行回路」的纪律一致。
    """
    matches: list[StrategyReference] = []
    for entry in WorkdirRegistry.list_all():
        workdir = Path(entry.path)
        try:
            batches = list_batches(workdir)
        except WorkdirMetadataCorruptedError:
            continue
        for batch in batches:
            try:
                snapshot, _ = read_snapshot_with_hash(workdir, batch.seq)
            except (StrategyNotFoundError, ValueError):
                continue
            source = snapshot.source
            if isinstance(source, dict) and source.get("strategy_id") == strategy_id:
                matches.append(
                    StrategyReference(
                        workdir_id=entry.id,
                        workdir_title=entry.title,
                        seq=batch.seq,
                        batch_name=batch.name,
                    )
                )
    return matches
