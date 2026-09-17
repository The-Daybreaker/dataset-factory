"""单元测试：workdir 数据域（wid 注册表 + `.dsf/` 门面 + 素材导入）。

注册表落数据根（temp_data_root 隔离）；门面与导入落 tmp_path 工作目录。
"""

from __future__ import annotations

import hashlib
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from dataset_factory.tasks import TaskCancelledError
from dataset_factory.workdir import (
    ImportSourceConflictError,
    WorkdirError,
    WorkdirMetadataCorruptedError,
    WorkdirNotFoundError,
    WorkdirPathError,
    WorkdirRegistry,
    WorkdirStore,
    ensure_dsf_layout,
    import_assets,
)
from dataset_factory.workdir import importer as importer_module

_PNG_BYTES = b"\x89PNG-fake-image-bytes"
_MP4_BYTES = b"\x00\x00\x00\x18ftypmp4-fake-video-bytes"


@pytest.fixture
def workdir(tmp_path: Path, temp_data_root: Path) -> Path:
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


def test_concurrent_registration_preserves_every_entry(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """多个写者同时登记不同目录，注册表保留每一条改动。"""
    paths = [tmp_path / f"workdir-{index}" for index in range(8)]
    for path in paths:
        path.mkdir()
    ready = threading.Barrier(len(paths))

    def register(path: Path) -> str:
        ready.wait(timeout=10)
        return WorkdirRegistry.register(path).id

    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        ids = list(pool.map(register, paths))

    assert {entry.id for entry in WorkdirRegistry.list_all()} == set(ids)
    assert len(ids) == len(set(ids))


def test_update_path_rejects_another_registered_directory(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """搬迁更新不能让两个不同标识指向同一个已登记目录。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    entry = WorkdirRegistry.register(first)
    other = WorkdirRegistry.register(second)

    with pytest.raises(WorkdirPathError):
        WorkdirRegistry.update_path(entry.id, second)

    assert WorkdirRegistry.get(entry.id).path == str(first)
    assert WorkdirRegistry.get(other.id).path == str(second)


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


def test_store_mutate_state_roundtrip(workdir: Path) -> None:
    """mutate_state 唯一写入口：空起写入、读回一致（原子写、全程状态锁内）。"""
    store = WorkdirStore(workdir)

    assert store.read_state() == {}
    store.mutate_state(lambda state: state.update({"batches": [{"seq": 1}]}))

    assert store.read_state() == {"batches": [{"seq": 1}]}
    assert store.state_file == workdir / ".dsf" / "state.json"


def test_store_mutate_state_passthrough(workdir: Path) -> None:
    """mutator 原地改 + 返回值透传：改动落盘、返回值原样带回调用方。"""
    store = WorkdirStore(workdir)

    def grow(state: dict[str, object]) -> int:
        state["n"] = 1
        return 41

    assert store.mutate_state(grow) == 41
    assert store.read_state() == {"n": 1}


def test_store_mutate_state_releases_lock_when_mutator_raises(workdir: Path) -> None:
    """mutator 抛错：异常冒泡、状态保持改前原样、锁随 finally 释放。"""
    store = WorkdirStore(workdir)

    def boom(state: dict[str, object]) -> None:
        state["dirty"] = True
        raise RuntimeError("炸一个")

    with pytest.raises(RuntimeError, match="炸一个"):
        store.mutate_state(boom)

    assert store.read_state() == {}
    store.mutate_state(lambda state: state.update({"k": "v"}))
    assert store.read_state() == {"k": "v"}


@pytest.mark.parametrize("iteration", range(10))
def test_store_mutate_state_no_lost_update_across_threads(
    workdir: Path, iteration: int
) -> None:
    """两线程并发读—改—写：各自追加的条目全部落盘（goal B2 的不丢更新）。"""
    store = WorkdirStore(workdir)
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def append(prefix: str) -> None:
        try:
            barrier.wait(5)
            for index in range(20):
                tag = f"{prefix}{index}"

                def mutator(state: dict[str, object], tag: str = tag) -> None:
                    items = cast("list[object]", state.get("items", []))
                    items.append(tag)
                    state["items"] = items

                store.mutate_state(mutator)
        except Exception:  # noqa: BLE001 — 将线程完整异常栈送回主线程断言
            errors.append(traceback.format_exc())

    threads = [
        threading.Thread(target=append, args=("甲",)),
        threading.Thread(target=append, args=("乙",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == [], "\n".join(errors)
    items = cast("list[object]", store.read_state().get("items", []))
    assert sorted(str(item) for item in items) == sorted(
        f"{prefix}{index}" for prefix in ("甲", "乙") for index in range(20)
    )


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
    assert store.tmp_dir == workdir / ".dsf" / "tmp"
    assert store.state_file.parent == store.dsf_path


# --------------------------------------------------------------------------
# 素材导入（importer）：窄清单 / 大小护栏 / 重复四情形 / 登记与自愈
# --------------------------------------------------------------------------


def _write(directory: Path, name: str, content: bytes) -> Path:
    """在目录里放一个文件（测试素材的统一写法），返回其路径。"""
    path = directory / name
    path.write_bytes(content)
    return path


def _sha(content: bytes) -> str:
    """测试内独立计算 SHA-256（不读实现代码，防两侧共用同一假设）。"""
    return hashlib.sha256(content).hexdigest()


def _read_records(workdir: Path) -> list[dict[str, object]]:
    """读工作目录的导入记录（测试侧便捷读取）。"""
    return WorkdirStore(workdir).read_import_records()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """一个独立的来源目录。"""
    target = tmp_path / "source"
    target.mkdir()
    return target


def test_copy_import_copies_and_registers(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """复制导入：素材复制进工作目录、原始目录不动、记录带来源与内容哈希。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    _write(source, "clip_001.mp4", _MP4_BYTES)

    report = import_assets(workdir, source)

    assert (workdir / "cat_001.jpg").read_bytes() == _PNG_BYTES
    assert (workdir / "clip_001.mp4").read_bytes() == _MP4_BYTES
    assert (source / "cat_001.jpg").read_bytes() == _PNG_BYTES
    assert report["imported"] == ["cat_001.jpg", "clip_001.mp4"]
    records = _read_records(workdir)
    assert len(records) == 1
    assert records[0]["source"] == str(source)
    assert records[0]["files"] == [
        {"name": "cat_001.jpg", "sha256": _sha(_PNG_BYTES)},
        {"name": "clip_001.mp4", "sha256": _sha(_MP4_BYTES)},
    ]


def test_in_place_adoption_registers_without_copy(
    workdir: Path, temp_data_root: Path
) -> None:
    """就地采用：不复制、来源记工作目录自身、素材全量登记。"""
    _write(workdir, "cat_001.jpg", _PNG_BYTES)

    report = import_assets(workdir, None)

    assert report["imported"] == ["cat_001.jpg"]
    records = _read_records(workdir)
    assert len(records) == 1
    assert records[0]["source"] == str(workdir)
    assert records[0]["files"] == [{"name": "cat_001.jpg", "sha256": _sha(_PNG_BYTES)}]


def test_flat_scan_skips_subdirectories(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """平铺扫描不递归：子目录里的素材不导入。"""
    _write(source, "top.jpg", _PNG_BYTES)
    nested = source / "nested"
    nested.mkdir()
    _write(nested, "deep.jpg", _PNG_BYTES)

    report = import_assets(workdir, source)

    assert report["imported"] == ["top.jpg"]
    assert not (workdir / "deep.jpg").exists()


def test_unsupported_extension_rejected(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """白名单外扩展名：不导入、不登记、拒绝原因 = 扩展名不支持。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    _write(source, "notes.txt", b"text")
    _write(source, "photo.heic", b"heic")

    report = import_assets(workdir, source)

    assert report["imported"] == ["cat_001.jpg"]
    assert not (workdir / "notes.txt").exists()
    rejected = {item["name"]: item["reason"] for item in report["rejected"]}
    assert rejected["notes.txt"] == "扩展名不支持"
    assert rejected["photo.heic"] == "扩展名不支持"
    assert _read_records(workdir)[0]["files"] == [
        {"name": "cat_001.jpg", "sha256": _sha(_PNG_BYTES)},
    ]


def test_oversize_rejected_by_kind_limit(
    workdir: Path,
    source: Path,
    temp_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大小护栏按类型分档：图片与视频各自超限不登记，边界值（=上限）放行。"""
    monkeypatch.setattr(importer_module, "MAX_IMAGE_BYTES", 10)
    monkeypatch.setattr(importer_module, "MAX_VIDEO_BYTES", 8)
    _write(source, "ok_img.jpg", b"1234567890")
    _write(source, "big_img.jpg", b"12345678901")
    _write(source, "ok_vid.mp4", b"12345678")
    _write(source, "big_vid.mp4", b"123456789")

    report = import_assets(workdir, source)

    assert sorted(report["imported"]) == ["ok_img.jpg", "ok_vid.mp4"]
    rejected = {item["name"]: item["reason"] for item in report["rejected"]}
    assert rejected["big_img.jpg"] == "超出大小上限"
    assert rejected["big_vid.mp4"] == "超出大小上限"


def test_image_real_limit_is_20_mib(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """真实护栏值钉死：图片恰好 20 MiB 放行、20 MiB + 1 字节拒绝。"""
    boundary = 20 * 1024 * 1024
    _write(source, "max.jpg", b"0" * boundary)

    report = import_assets(workdir, source)

    assert report["imported"] == ["max.jpg"]
    _write(source, "over.jpg", b"0" * (boundary + 1))

    report = import_assets(workdir, source)

    assert report["imported"] == []
    rejected = {item["name"]: item["reason"] for item in report["rejected"]}
    assert rejected["over.jpg"] == "超出大小上限"


def test_same_name_same_content_skips_but_reregisters(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """重复导入情形一（同名同容）：幂等跳过复制，但重登记（出身刷新 + 中断自愈）。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    import_assets(workdir, source)

    report = import_assets(workdir, source)

    assert report["imported"] == []
    assert report["skipped_identical"] == ["cat_001.jpg"]
    records = _read_records(workdir)
    assert len(records) == 2
    assert records[1]["files"] == [{"name": "cat_001.jpg", "sha256": _sha(_PNG_BYTES)}]


def test_same_name_diff_content_never_overwrites(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """重复导入情形二（同名异容）：不覆盖、跳过并给新旧对照（本版无替换操作）。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    import_assets(workdir, source)
    _write(source, "cat_001.jpg", b"\x89PNG-new-content")

    report = import_assets(workdir, source)

    assert (workdir / "cat_001.jpg").read_bytes() == _PNG_BYTES
    assert report["skipped_conflict"] == [
        {
            "name": "cat_001.jpg",
            "existing_size": len(_PNG_BYTES),
            "incoming_size": len(b"\x89PNG-new-content"),
            "existing_sha256": _sha(_PNG_BYTES),
            "incoming_sha256": _sha(b"\x89PNG-new-content"),
        },
    ]
    assert _read_records(workdir)[0]["files"] == [
        {"name": "cat_001.jpg", "sha256": _sha(_PNG_BYTES)},
    ]


def test_diff_name_same_content_skips_by_default(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """重复导入情形三（异名同容）：默认跳过并指明撞容条目。"""
    _write(workdir, "a.jpg", _PNG_BYTES)
    import_assets(workdir, None)
    _write(source, "b.jpg", _PNG_BYTES)

    report = import_assets(workdir, source)

    assert report["imported"] == []
    assert report["skipped_duplicate"] == [{"name": "b.jpg", "duplicate_of": "a.jpg"}]
    assert not (workdir / "b.jpg").exists()
    assert _read_records(workdir)[-1]["files"] == []


def test_diff_name_same_content_forced_by_force_names(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """异名同容点名强制：仍按新名导入（内容相同、名字并存是用户显式选择）。"""
    _write(workdir, "a.jpg", _PNG_BYTES)
    import_assets(workdir, None)
    _write(source, "b.jpg", _PNG_BYTES)

    report = import_assets(workdir, source, force_names={"b.jpg"})

    assert report["imported"] == ["b.jpg"]
    assert (workdir / "b.jpg").read_bytes() == _PNG_BYTES
    assert _read_records(workdir)[-1]["files"] == [
        {"name": "b.jpg", "sha256": _sha(_PNG_BYTES)},
    ]


def test_diff_name_diff_content_imports(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """重复导入情形四（异名异容）：正常导入为新条目。"""
    _write(workdir, "a.jpg", _PNG_BYTES)
    import_assets(workdir, None)
    _write(source, "b.jpg", b"\x89PNG-other-content")

    report = import_assets(workdir, source)

    assert report["imported"] == ["b.jpg"]
    assert (workdir / "b.jpg").read_bytes() == b"\x89PNG-other-content"


def test_stem_conflict_rejected_against_existing(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """条目身份 = 主干：工作目录已有 cat.jpg 时导入 cat.mp4 被拒（要求重命名其一）。"""
    _write(workdir, "cat.jpg", _PNG_BYTES)
    import_assets(workdir, None)
    _write(source, "cat.mp4", _MP4_BYTES)

    report = import_assets(workdir, source)

    assert report["imported"] == []
    assert not (workdir / "cat.mp4").exists()
    rejected = {item["name"]: item["reason"] for item in report["rejected"]}
    assert "重命名其一" in rejected["cat.mp4"]


def test_stem_conflict_rejected_within_one_batch(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """同一批来源里同名不同扩展的两个文件：先者导入、后者拒（批内也守主干不变量）。"""
    _write(source, "cat.jpg", _PNG_BYTES)
    _write(source, "cat.mp4", _MP4_BYTES)

    report = import_assets(workdir, source)

    assert report["imported"] == ["cat.jpg"]
    rejected = {item["name"]: item["reason"] for item in report["rejected"]}
    assert "重命名其一" in rejected["cat.mp4"]


def test_empty_import_still_writes_record(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """没有可导入素材：记录照写（append-only 事件流，空记录也是一次导入尝试）。"""
    _write(source, "notes.txt", b"text")

    report = import_assets(workdir, source)

    assert report["imported"] == []
    records = _read_records(workdir)
    assert len(records) == 1
    assert records[0]["files"] == []


def test_interrupted_import_self_heals_on_reimport(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """中断重入幂等：已复制但记录未写的文件，重导时按同名同容重登记。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    _write(source, "dog_001.jpg", b"\x89PNG-dog")
    _write(workdir, "cat_001.jpg", _PNG_BYTES)

    report = import_assets(workdir, source)

    assert report["skipped_identical"] == ["cat_001.jpg"]
    assert report["imported"] == ["dog_001.jpg"]
    assert _read_records(workdir)[-1]["files"] == [
        {"name": "cat_001.jpg", "sha256": _sha(_PNG_BYTES)},
        {"name": "dog_001.jpg", "sha256": _sha(b"\x89PNG-dog")},
    ]


def test_import_cleans_stale_tmp(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """导入开头清掉上次中断留下的 .dsf/tmp 垃圾（自愈语义）。"""
    store = WorkdirStore(workdir)
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    _write(store.tmp_dir, "half-finished.jpg", b"partial")
    _write(source, "cat_001.jpg", _PNG_BYTES)

    import_assets(workdir, source)

    assert store.tmp_dir.is_dir()
    assert list(store.tmp_dir.iterdir()) == []


def test_cancellation_stops_before_processing(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """取消信号置位：在文件边界抛 TaskCancelledError、记录一个不写。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    should_stop = threading.Event()
    should_stop.set()

    with pytest.raises(TaskCancelledError):
        import_assets(workdir, source, should_stop=should_stop)

    assert not (workdir / "cat_001.jpg").exists()
    assert not WorkdirStore(workdir).imports_file.exists()


def test_progress_reports_monotonic_to_one(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """进度回调：按候选推进、首值 0.05、终值 1.0、单调不减。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)
    _write(source, "clip_001.mp4", _MP4_BYTES)
    seen: list[float] = []

    import_assets(workdir, source, progress=seen.append)

    assert seen[0] == pytest.approx(0.05)
    assert seen[-1] == 1.0
    assert seen == sorted(seen)


def test_source_same_as_workdir_rejected(workdir: Path, temp_data_root: Path) -> None:
    """复制导入校验：来源 = 工作目录自身 → ImportSourceConflictError（422 档）。"""
    with pytest.raises(ImportSourceConflictError, match="嵌套"):
        import_assets(workdir, workdir)


def test_source_inside_workdir_rejected(workdir: Path, temp_data_root: Path) -> None:
    """来源在工作目录内部 → 拒绝（复制会自我覆盖）。"""
    inner = workdir / "inbox"
    inner.mkdir()

    with pytest.raises(ImportSourceConflictError, match="嵌套"):
        import_assets(workdir, inner)


def test_workdir_inside_source_rejected(
    workdir: Path, tmp_path: Path, temp_data_root: Path
) -> None:
    """工作目录在来源内部 → 拒绝（互为嵌套的另一方向）。"""
    with pytest.raises(ImportSourceConflictError, match="嵌套"):
        import_assets(workdir, tmp_path)


def test_source_missing_rejected(
    workdir: Path, tmp_path: Path, temp_data_root: Path
) -> None:
    """来源不存在 → WorkdirPathError（400 档）。"""
    with pytest.raises(WorkdirPathError, match="来源目录"):
        import_assets(workdir, tmp_path / "nope")


def test_copy_failure_fails_loud_with_filename(
    workdir: Path,
    source: Path,
    temp_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复制失败（磁盘 / 权限）：任务失败消息带文件名，不静默吞掉。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)

    def broken_copy(store: WorkdirStore, source_file: Path, dest_name: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(importer_module, "_copy_into_workdir", broken_copy)

    with pytest.raises(WorkdirError, match=r"cat_001\.jpg"):
        import_assets(workdir, source)


def test_in_batch_same_content_skips_second(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """批内异名同容：同一批里 b 与先导入的 a 内容相同 → 默认跳过（与工作目录全量比对）。"""
    _write(source, "a.jpg", _PNG_BYTES)
    _write(source, "b.jpg", _PNG_BYTES)

    report = import_assets(workdir, source)

    assert report["imported"] == ["a.jpg"]
    assert report["skipped_duplicate"] == [{"name": "b.jpg", "duplicate_of": "a.jpg"}]
    assert not (workdir / "b.jpg").exists()
    assert _read_records(workdir)[0]["files"] == [
        {"name": "a.jpg", "sha256": _sha(_PNG_BYTES)},
    ]


def test_same_name_oversized_file_is_never_overwritten(
    workdir: Path,
    source: Path,
    temp_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名保护对物理存在的超限文件同样生效：合法同名导入 → 冲突跳过、原文件不动。"""
    monkeypatch.setattr(importer_module, "MAX_IMAGE_BYTES", 10)
    _write(workdir, "cat.jpg", b"12345678901")
    _write(source, "cat.jpg", b"12345")

    report = import_assets(workdir, source)

    assert report["imported"] == []
    assert (workdir / "cat.jpg").read_bytes() == b"12345678901"
    assert report["skipped_conflict"][0]["name"] == "cat.jpg"


def test_stem_conflict_against_oversized_existing(
    workdir: Path,
    source: Path,
    temp_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主干唯一对超限的物理存在文件同样生效：超限 cat.jpg 在场时 cat.mp4 被拒。"""
    monkeypatch.setattr(importer_module, "MAX_IMAGE_BYTES", 10)
    _write(workdir, "cat.jpg", b"12345678901")
    import_assets(workdir, None)
    _write(source, "cat.mp4", _MP4_BYTES)

    report = import_assets(workdir, source)

    assert report["imported"] == []
    rejected = {item["name"]: item["reason"] for item in report["rejected"]}
    assert "重命名其一" in rejected["cat.mp4"]


def test_record_hash_refers_to_copied_bytes(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """导入记录的哈希 = 实际落盘内容的 SHA-256（锚点指向磁盘上那份字节）。"""
    _write(source, "cat_001.jpg", _PNG_BYTES)

    import_assets(workdir, source)

    files = cast("list[dict[str, str]]", _read_records(workdir)[0]["files"])
    record = files[0]
    on_disk = (workdir / "cat_001.jpg").read_bytes()
    assert record["sha256"] == _sha(on_disk)


def test_size_limit_constants_pinned() -> None:
    """护栏常量钉死（design 项 13）：图片 20 MiB / 视频 100 MiB——值被误改当场红。"""
    assert importer_module.MAX_IMAGE_BYTES == 20 * 1024 * 1024
    assert importer_module.MAX_VIDEO_BYTES == 100 * 1024 * 1024


def test_import_records_roundtrip(workdir: Path, temp_data_root: Path) -> None:
    """记录追加与读回：两次导入两行，读回按追加序（时间正序）。"""
    store = WorkdirStore(workdir)
    store.append_import_record(
        {"imported_at": "2026-09-16T00:00:00+00:00", "source": "a", "files": []},
    )
    store.append_import_record(
        {
            "imported_at": "2026-09-16T01:00:00+00:00",
            "source": "b",
            "files": [{"name": "x.jpg", "sha256": "abc"}],
        },
    )

    records = store.read_import_records()

    assert [record["source"] for record in records] == ["a", "b"]


def test_read_import_records_tolerates_incomplete_tail(
    workdir: Path, temp_data_root: Path
) -> None:
    """崩溃安全读法：末尾残缺行（无换行收尾）砍掉，完整行照常读回。"""
    store = WorkdirStore(workdir)
    good = (
        '{"imported_at": "t1", "source": "a", "files": []}\n'
        '{"imported_at": "t2", "source": "b", "files": []}\n'
    )
    store.imports_file.write_text(
        good + '{"imported_at": "t3", "sour', encoding="utf-8"
    )

    records = store.read_import_records()

    assert [record["source"] for record in records] == ["a", "b"]


def test_read_import_records_corrupt_middle_fails_loud(
    workdir: Path, temp_data_root: Path
) -> None:
    """中间完整行损坏 → WorkdirMetadataCorruptedError（坏文件不静默兜底）。"""
    store = WorkdirStore(workdir)
    store.imports_file.write_text(
        '{"imported_at": "t1", "source": "a", "files": []}\n{broken}\n',
        encoding="utf-8",
    )

    with pytest.raises(WorkdirMetadataCorruptedError, match="导入记录文件损坏"):
        store.read_import_records()


def test_read_import_records_bad_shape_fails_loud(
    workdir: Path, temp_data_root: Path
) -> None:
    """JSON 合法但形状不对（缺字段 / 类型不符）→ 同样按损坏处理。"""
    store = WorkdirStore(workdir)
    store.imports_file.write_text(
        '{"imported_at": 1, "source": "a"}\n', encoding="utf-8"
    )

    with pytest.raises(WorkdirMetadataCorruptedError, match="形状不对"):
        store.read_import_records()


def test_read_import_records_empty_when_missing(workdir: Path) -> None:
    """没有记录文件 → 空列表（新工作目录的空态）。"""
    assert WorkdirStore(workdir).read_import_records() == []
