"""契约件：跑批进度的形状与状态取值域只有一处事实。

goal G6 点名的这一组重复是「同一个形状写了两遍」——执行器的运行中快照（写 `run-status.json`
与 HTTP current 的数据源）与跨进程读到的占位进度各列一遍键名，终态判定又各写一遍字面量。
收敛到 `.progress` + `journal.empty_counters()` 之后，这三条用例负责让「谁掉队」当场可见：
快照加字段而占位体没跟、计数字典加键而读侧模型没跟、状态改名而终态集合没跟，都会红。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from dataset_factory.api.schemas import RunStatusView
from dataset_factory.runs.control import current_run
from dataset_factory.runs.journal import RunCounters
from dataset_factory.runs.progress import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_INTERRUPTED,
    RUN_STATUS_PENDING,
    RUN_STATUS_RUNNING,
    TERMINAL_RUN_STATUSES,
)
from dataset_factory.workdir.locks import RunLock
from dataset_factory.workdir.store import WorkdirStore


@pytest.fixture
def workdir(tmp_path: Path, temp_data_root: Path) -> Path:
    """一个真实存在的临时工作目录（数据根已由 temp_data_root 隔离）。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


def _placeholder_progress(workdir: Path) -> dict[str, object]:
    """持一把跑批锁（只登记 run_id，不写 run-status.json）后读到的占位进度。"""
    dsf = WorkdirStore(workdir).dsf_path
    lock = RunLock(dsf)
    lock.acquire({"batch": "s1", "run_id": "contract-test", "mode": "retry"})
    try:
        return current_run(workdir, 1)
    finally:
        lock.release()


def test_placeholder_shape_matches_the_http_view(workdir: Path) -> None:
    """占位进度的键集 = current 端点响应模型的字段集（两处形状同源）。"""
    progress = _placeholder_progress(workdir)

    assert set(progress) == set(RunStatusView.model_fields)
    assert RunStatusView.model_validate(progress).mode == "retry"


def test_placeholder_counters_match_the_record_model(workdir: Path) -> None:
    """占位进度的计数键集 = 磁盘记录 `RunCounters` 的字段集，且全为 0。"""
    counters = cast("dict[str, int]", _placeholder_progress(workdir)["counters"])

    assert set(counters) == set(RunCounters.model_fields)
    assert set(counters.values()) == {0}


def test_terminal_statuses_are_the_three_finished_ones() -> None:
    """终态判定只认 completed / interrupted / failed，running 与 pending 仍算进行中。"""
    assert set(TERMINAL_RUN_STATUSES) == {
        RUN_STATUS_COMPLETED,
        RUN_STATUS_INTERRUPTED,
        RUN_STATUS_FAILED,
    }
    assert RUN_STATUS_RUNNING not in TERMINAL_RUN_STATUSES
    assert RUN_STATUS_PENDING not in TERMINAL_RUN_STATUSES
    assert RUN_STATUS_FAILED in TERMINAL_RUN_STATUSES
