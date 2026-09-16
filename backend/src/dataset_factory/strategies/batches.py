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

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .._fs import atomic_write_text
from ..workdir import WorkdirMetadataCorruptedError, WorkdirStore
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


def _now_iso() -> str:
    """当前 UTC 时刻（ISO 8601）。"""
    return datetime.now(UTC).isoformat()


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
    return sorted(
        (_entry_from_dict(item) for item in _batches_raw(store.read_state())),
        key=lambda entry: entry.seq,
    )


def get_batch(workdir: Path, seq: int) -> BatchEntry:
    """按序号查批次；不存在抛 BatchNotFoundError。"""
    for entry in list_batches(workdir):
        if entry.seq == seq:
            return entry
    raise BatchNotFoundError(
        f"批次 s{seq} 不在工作目录中——可能已被删除，请刷新后重试。",
    )


def _read_next_seq(workdir: Path, entries: list[BatchEntry]) -> int:
    """读序号计数（缺键时用现有批次最大序号 + 1 兜底——老 state 的兼容读法）。"""
    next_seq = WorkdirStore(workdir).read_state().get("next_seq")
    if isinstance(next_seq, int) and not isinstance(next_seq, bool):
        return next_seq
    return max((entry.seq for entry in entries), default=0) + 1


def _write_batches(workdir: Path, entries: list[BatchEntry], next_seq: int) -> None:
    """原子写回批次列表与序号计数（读—改—写全程持锁由调用方保证——T36 起）。

    序号计数只增不减：无论调用方传入什么，落盘值不低于「现有批次最大序号 + 1」，
    防止删除路径把计数写回去导致序号复用。
    """
    store = WorkdirStore(workdir)
    state = store.read_state()
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
    state["next_seq"] = max(
        next_seq, max((entry.seq for entry in entries), default=0) + 1
    )
    store.write_state(state)


def _replace_entry(entries: list[BatchEntry], entry: BatchEntry) -> list[BatchEntry]:
    """把改过的条目按序号替换回列表（保持原有顺序）。"""
    return [entry if item.seq == entry.seq else item for item in entries]


def product_count(workdir: Path, seq: int) -> int:
    """该批次现有产物 txt 数（删除确认弹窗的「将删多少个」数据源）。"""
    store = WorkdirStore(workdir)
    return sum(1 for _ in store.dsf_path.parent.glob(f"s{seq}__*.txt"))


def _write_snapshot(store: WorkdirStore, seq: int, snapshot: StrategySnapshot) -> None:
    """原子写一份策略快照（``.dsf/strategies/s<N>.json``，人可读）。"""
    path = store.strategies_dir / f"s{seq}.json"
    atomic_write_text(
        path, json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2)
    )


def create_batch(
    workdir: Path,
    *,
    name: str,
    description: str,
    endpoint: str,
    prompt: str,
    skills: list[str],
    source: dict[str, object] | None = None,
) -> BatchEntry:
    """从零配置新建一个批次：校验引用 → 装配快照 → 分配序号 → 落盘。

    Raises:
        StrategyRefsError: 引用的端点 / 提示词 / Skill 不存在。
        BatchNotFoundError: state.json 形状不对。
    """
    require_refs_exist(endpoint, prompt, skills)
    now = _now_iso()
    snapshot = build_snapshot(endpoint, prompt, skills, built_at=now, source=source)
    store = WorkdirStore(workdir)
    seq, entries = _allocate_seq(workdir)
    _write_snapshot(store, seq, snapshot)
    entry = BatchEntry(
        seq=seq,
        name=name,
        description=description,
        snapshot=f"s{seq}.json",
        active=True,
        created_at=now,
    )
    _write_batches(workdir, [*entries, entry], seq + 1)
    return entry


def _allocate_seq(workdir: Path) -> tuple[int, list[BatchEntry]]:
    """分配下一个序号（只增不复用）并返回（序号, 现有批次列表）。"""
    store = WorkdirStore(workdir)
    entries = [_entry_from_dict(item) for item in _batches_raw(store.read_state())]
    next_seq = store.read_state().get("next_seq")
    if isinstance(next_seq, int) and not isinstance(next_seq, bool):
        seq = next_seq
    else:
        seq = max((entry.seq for entry in entries), default=0) + 1
    return seq, entries


def apply_library_strategy(
    workdir: Path,
    strategy_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> BatchEntry:
    """应用库策略到工作目录（copy-on-apply）：分配序号、记来源、装配快照。

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
    now = _now_iso()
    snapshot = build_snapshot(
        library_entry.endpoint,
        library_entry.prompt,
        library_entry.skills,
        built_at=now,
        source={
            "strategy_id": strategy_id,
            "strategy_sha256": strategy_content_hash(library_entry),
        },
    )
    seq, entries = _allocate_seq(workdir)
    _write_snapshot(WorkdirStore(workdir), seq, snapshot)
    entry = BatchEntry(
        seq=seq,
        name=name if name is not None else library_entry.name,
        description=description
        if description is not None
        else library_entry.description,
        snapshot=f"s{seq}.json",
        active=True,
        created_at=now,
    )
    _write_batches(workdir, [*entries, entry], seq + 1)
    return entry


def update_batch(
    workdir: Path,
    seq: int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> BatchEntry:
    """配置补丁：改名 / 描述（纯显示元数据，快照不动）。

    组合不可改——工作目录下的策略是库策略的应用副本，想换组合 = 新建批次。
    """
    entry = get_batch(workdir, seq)
    if name is not None:
        entry.name = name
    if description is not None:
        entry.description = description
    entries = list_batches(workdir)
    _write_batches(
        workdir, _replace_entry(entries, entry), _read_next_seq(workdir, entries)
    )
    return entry


def set_batch_active(workdir: Path, seq: int, active: bool) -> BatchEntry:
    """停用（隐藏）/ 召回批次。停用正在运行的策略需中断运行——T36 运行器落地。"""
    entry = get_batch(workdir, seq)
    entry.active = active
    entries = list_batches(workdir)
    _write_batches(
        workdir, _replace_entry(entries, entry), _read_next_seq(workdir, entries)
    )
    return entry


def delete_batch(workdir: Path, seq: int) -> int:
    """删除批次：产物 txt + 快照 + state.json 记录 + 排除名单一并移除。

    Returns:
        删除的产物 txt 数（历史 runs/ 保留）。

    Raises:
        BatchNotFoundError: 批次不存在。
    """
    get_batch(workdir, seq)  # 不存在先报错，失败时现场不动
    store = WorkdirStore(workdir)
    count = 0
    for product in store.dsf_path.parent.glob(f"s{seq}__*.txt"):
        product.unlink()
        count += 1
    snapshot_path = store.strategies_dir / f"s{seq}.json"
    snapshot_path.unlink(missing_ok=True)
    # 计数在移除条目**之前**读：legacy state（无 next_seq 键）回退 max+1 时
    # 才能算上被删的那个序号，删完不回退（配合 _write_batches 的只增不减钳制）。
    entries = list_batches(workdir)
    next_seq = _read_next_seq(workdir, entries)
    _write_batches(workdir, [item for item in entries if item.seq != seq], next_seq)
    exclusions = _read_exclusions(workdir)
    exclusions.pop(str(seq), None)
    _write_exclusions(workdir, exclusions)
    return count


def _read_exclusions(workdir: Path) -> dict[str, list[str]]:
    """读各批次排除名单（缺键视为空；形状不对 fail loud）。"""
    value = WorkdirStore(workdir).read_state().get("exclusions")
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


def _write_exclusions(workdir: Path, exclusions: dict[str, list[str]]) -> None:
    """原子写排除名单（读—改—写全程持锁由调用方保证——T36 起）。"""
    store = WorkdirStore(workdir)
    state = store.read_state()
    state["exclusions"] = exclusions
    store.write_state(state)


def add_exclusions(workdir: Path, seq: int, items: list[str]) -> list[str]:
    """把条目加入该批次的排除名单（幂等去重），返回当前名单。"""
    get_batch(workdir, seq)  # 批次不存在先报错
    exclusions = _read_exclusions(workdir)
    current = exclusions.setdefault(str(seq), [])
    for item in items:
        if item not in current:
            current.append(item)
    _write_exclusions(workdir, exclusions)
    return list(current)


def remove_exclusions(workdir: Path, seq: int, items: list[str]) -> list[str]:
    """把条目移出该批次的排除名单（撤销排除），返回当前名单。"""
    get_batch(workdir, seq)
    exclusions = _read_exclusions(workdir)
    removal = set(items)
    current = [item for item in exclusions.get(str(seq), []) if item not in removal]
    exclusions[str(seq)] = current
    _write_exclusions(workdir, exclusions)
    return list(current)


def read_snapshot(workdir: Path, seq: int) -> StrategySnapshot:
    """读一份批次快照（批次概览「快照」弹窗与哈希比对的数据源）。

    Raises:
        BatchNotFoundError: 批次不存在。
        StrategyNotFoundError: 快照文件缺失或损坏（元数据与文件不一致）。
    """
    get_batch(workdir, seq)
    path = WorkdirStore(workdir).strategies_dir / f"s{seq}.json"
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
        return StrategySnapshot.from_json(data)
    except OSError as exc:
        raise StrategyNotFoundError(
            f"策略快照文件无法读取（{path.name}）——批次元数据与快照不一致，请检查后重试。",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise StrategyNotFoundError(
            f"策略快照文件损坏（{path.name}）：{exc}",
        ) from exc
