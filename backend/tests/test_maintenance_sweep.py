"""维护记录启动清扫（``sweep_cleaned_maintenance_records``）的回归用例。

钉住三件事：

1. 只清 ``status=cleaned`` 的记录——删了与不删等价（全部消费方对 cleaned 的读法
   要么放行、要么跳过）；中断态（deleting / prepared / copying）与损坏记录一律
   不碰：前者承载重试与防呆，后者由 ``require_workdir_writable`` fail loud。
2. ``.lock`` 文件本体保留——删锁文件有互斥竞态，是 ``preserve_lock_file`` 下的
   无害残留。
3. 应用启动（lifespan）真实触发清扫（装配钉子，防「函数在、没人调」）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory._fs import atomic_write_text
from dataset_factory.api.app import create_app
from dataset_factory.workdir.errors import WorkdirPathError
from dataset_factory.workdir.locks import (
    maintenance_record,
    require_workdir_writable,
    sweep_cleaned_maintenance_records,
)
from dataset_factory.workdir.relocation import relocate_workdir
from dataset_factory.workdir.store import WorkdirRegistry, WorkdirStore


def test_sweep_removes_only_cleaned_records(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """真实搬迁留下的 cleaned 记录被清；中断态、损坏记录与 ``.lock`` 本体保留。"""
    source = tmp_path / "source"
    source.mkdir()
    entry = WorkdirRegistry.register(source)
    destination = tmp_path / "destination"
    relocate_workdir(entry.id, destination)

    record = maintenance_record(source)
    lock = record.with_suffix(".lock")
    assert record.is_file()
    assert lock.is_file()

    maintenance = temp_data_root / "workdir-maintenance"
    deleting = maintenance / ("ab" * 32 + ".json")
    deleting.write_text(
        json.dumps({"operation": "delete", "status": "deleting", "wid": "w1"}),
        encoding="utf-8",
    )
    corrupted = maintenance / ("cd" * 32 + ".json")
    corrupted.write_text("{not json", encoding="utf-8")

    swept = sweep_cleaned_maintenance_records()

    assert swept == 1
    assert not record.exists()
    assert lock.is_file()
    assert deleting.is_file()
    assert corrupted.is_file()


def test_swept_cleaned_record_still_allows_recreated_path(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """行为等价钉子：清扫后同路径重建照常登记与写入（与 cleaned 记录在场时等价）。"""
    source = tmp_path / "source"
    source.mkdir()
    entry = WorkdirRegistry.register(source)
    destination = tmp_path / "destination"
    relocate_workdir(entry.id, destination)

    assert sweep_cleaned_maintenance_records() == 1
    source.mkdir()
    new_entry = WorkdirRegistry.register(source)
    assert new_entry.id != entry.id
    WorkdirStore(source).mutate_state(lambda state: state.update({"new": True}))


def test_sweep_keeps_interrupted_delete_record_guard(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """中断的删除记录清扫后仍在：``require_workdir_writable`` 继续拒绝向现场写入。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / ".dsf").mkdir()
    record_path = maintenance_record(source)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        record_path,
        json.dumps({"operation": "delete", "status": "deleting", "wid": "w1"}),
    )

    sweep_cleaned_maintenance_records()

    with pytest.raises(WorkdirPathError):
        require_workdir_writable(source / ".dsf")


def test_app_lifespan_sweeps_cleaned_records(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """装配钉子：应用启动（lifespan）真实触发清扫，cleaned 消失、中断态保留。"""
    source = tmp_path / "source"
    source.mkdir()
    entry = WorkdirRegistry.register(source)
    relocate_workdir(entry.id, tmp_path / "destination")
    maintenance = temp_data_root / "workdir-maintenance"
    deleting = maintenance / ("ab" * 32 + ".json")
    deleting.write_text(json.dumps({"status": "deleting"}), encoding="utf-8")
    record = maintenance_record(source)
    assert record.is_file()

    with TestClient(create_app(frontend_dir=tmp_path / "frontend")):
        assert not record.exists()
    assert deleting.is_file()
