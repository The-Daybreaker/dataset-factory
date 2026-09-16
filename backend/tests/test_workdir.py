"""单元测试：workdir 数据域（wid 注册表 + `.dsf/` 门面）。

注册表落数据根（temp_data_root 隔离）；门面落 tmp_path 工作目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataset_factory.workdir import (
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
    WorkdirRegistry,
    WorkdirStore,
    ensure_dsf_layout,
)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """一个真实存在的临时工作目录。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


def test_register_creates_entry_with_dirname_title(
    workdir: Path,
    temp_data_root: Path,
) -> None:
    """新登记：分配 wid、路径存规范形、显示名默认 = 目录名。"""
    entry = WorkdirRegistry.register(workdir, title="")

    assert entry.title == "photos"
    assert Path(entry.path).is_absolute()
    assert WorkdirRegistry.get(entry.id).path == entry.path


def test_register_missing_path_rejected(tmp_path: Path, temp_data_root: Path) -> None:
    """登记不存在的路径 → WorkdirPathError（路径不合法，400 档）。"""
    with pytest.raises(WorkdirPathError, match="不存在"):
        WorkdirRegistry.register(tmp_path / "nope", title="")


def test_register_same_realpath_is_idempotent(
    workdir: Path,
    temp_data_root: Path,
) -> None:
    """幂等判定 = realpath：同物理目录重复登记不新增条目、只更新显示名与时间。"""
    first = WorkdirRegistry.register(workdir, title="旧名")
    second = WorkdirRegistry.register(workdir, title="新名")

    assert first.id == second.id
    assert second.title == "新名"
    assert len(WorkdirRegistry.list_all()) == 1


def test_list_all_sorts_by_last_used_desc(
    workdir: Path,
    tmp_path: Path,
    temp_data_root: Path,
) -> None:
    """列表按最后使用倒序（最近在前）——两级下拉的数据源语义。"""
    other = tmp_path / "videos"
    other.mkdir()
    WorkdirRegistry.register(workdir, title="早的")
    WorkdirRegistry.register(other, title="晚的")

    titles = [entry.title for entry in WorkdirRegistry.list_all()]

    assert titles == ["晚的", "早的"]


def test_list_all_keeps_missing_entries_address_book_semantics(
    workdir: Path,
    temp_data_root: Path,
) -> None:
    """地址簿语义：登记过的目录被外部删掉后条目仍在（存在性以磁盘为准，消费方探测）。"""
    entry = WorkdirRegistry.register(workdir, title="")

    workdir.rmdir()

    assert [item.id for item in WorkdirRegistry.list_all()] == [entry.id]


def test_get_unknown_wid_raises_not_found(temp_data_root: Path) -> None:
    """查不存在的 wid → WorkdirNotFoundError（404 档）。"""
    with pytest.raises(WorkdirNotFoundError):
        WorkdirRegistry.get("no-such-wid")


def test_remove_removes_and_unknown_remove_raises(
    workdir: Path,
    temp_data_root: Path,
) -> None:
    """移除登记条目；对不存在的 wid 移除抛 WorkdirNotFoundError。"""
    entry = WorkdirRegistry.register(workdir, title="")

    WorkdirRegistry.remove(entry.id)

    assert WorkdirRegistry.list_all() == []
    with pytest.raises(WorkdirNotFoundError):
        WorkdirRegistry.remove(entry.id)


def test_update_path_repoints_entry_keeps_wid(
    workdir: Path, tmp_path: Path, temp_data_root: Path
) -> None:
    """搬迁语义：原地更新 path，wid 不变（reference anchor 必须稳定）。"""
    entry = WorkdirRegistry.register(workdir, title="")
    moved = tmp_path / "moved"
    moved.mkdir()

    updated = WorkdirRegistry.update_path(entry.id, moved)

    assert updated.id == entry.id
    assert updated.path == str(moved)
    assert WorkdirRegistry.get(entry.id).path == str(moved)


def test_update_path_rejects_missing_target(
    workdir: Path, temp_data_root: Path
) -> None:
    """搬迁目标不存在 → WorkdirPathError（搬迁未完成的现场不动注册表）。"""
    entry = WorkdirRegistry.register(workdir, title="")

    with pytest.raises(WorkdirPathError, match="搬迁未完成"):
        WorkdirRegistry.update_path(entry.id, workdir / "nope")


def test_update_path_unknown_wid_raises_not_found(
    workdir: Path, temp_data_root: Path
) -> None:
    """对不存在的 wid 更新路径 → WorkdirNotFoundError。"""
    moved = workdir / "moved"
    moved.mkdir()

    with pytest.raises(WorkdirNotFoundError):
        WorkdirRegistry.update_path("no-such-wid", moved)


def test_corrupted_registry_fails_loud(temp_data_root: Path) -> None:
    """注册表文件损坏 → WorkdirMetadataCorruptedError（fail loud，不静默兜底）。"""
    (temp_data_root / "workdirs.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(WorkdirMetadataCorruptedError, match="损坏"):
        WorkdirRegistry.list_all()


def test_ensure_dsf_layout_creates_idempotent(workdir: Path) -> None:
    """.dsf/ 布局创建幂等：strategies 与 runs 子目录就位、重复调用不报错。"""
    first = ensure_dsf_layout(workdir)
    second = ensure_dsf_layout(workdir)

    assert first == second == workdir / ".dsf"
    assert (first / "strategies").is_dir()
    assert (first / "runs").is_dir()


def test_store_state_roundtrip(workdir: Path) -> None:
    """state.json 读写往返：不存在 → 空字典；写入后原样读回（原子写）。"""
    store = WorkdirStore(workdir)

    assert store.read_state() == {}
    store.write_state({"batches": [{"seq": 1}]})

    assert store.read_state() == {"batches": [{"seq": 1}]}
    assert store.state_file == workdir / ".dsf" / "state.json"


def test_store_corrupted_state_fails_loud(workdir: Path) -> None:
    """state.json 损坏 → WorkdirMetadataCorruptedError（不静默兜底）。"""
    store = WorkdirStore(workdir)
    store.state_file.write_text("not json", encoding="utf-8")

    with pytest.raises(WorkdirMetadataCorruptedError, match="状态文件损坏"):
        store.read_state()


def test_store_layout_properties(workdir: Path) -> None:
    """门面路径属性：全部落在 .dsf/ 内（唯一写者边界的结构保证）。"""
    store = WorkdirStore(workdir)

    assert store.dsf_path == workdir / ".dsf"
    assert store.strategies_dir == workdir / ".dsf" / "strategies"
    assert store.runs_dir == workdir / ".dsf" / "runs"
    assert store.imports_file == workdir / ".dsf" / "imports.jsonl"
    assert store.state_file.parent == store.dsf_path
