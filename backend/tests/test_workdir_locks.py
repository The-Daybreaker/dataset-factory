"""workdir.locks 两把锁原语：互斥 / 超时 / 实例共享 / 进程强杀后无需清理。

goal B2（并发保护测试）：运行锁——占用拒绝与异常释放（进程强杀后重新跑批
无需手动清理）；状态锁——跨线程同时对 state.json 读—改—写不丢更新（丢更新
场景在 test_workdir.py 的 mutate_state 测试覆盖），以及持锁进程被强杀后
无需手动清理即可继续写。

强杀测试用真子进程持锁再 kill：文件锁的「随句柄自动释放」是操作系统行为，
进程内模拟不出来。子进程用 sys.executable（同一 venv，能 import 包）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from dataset_factory.workdir import (
    RunLock,
    RunOccupiedError,
    StateLock,
    StateLockTimeoutError,
    WorkdirStore,
)

_HOLD_STATE_LOCK_SCRIPT = """
import sys
import time
from pathlib import Path

from dataset_factory.workdir.locks import StateLock

dsf = Path(sys.argv[1])
marker = Path(sys.argv[2])

lock = StateLock(dsf)
lock.acquire()
marker.write_text("held", encoding="utf-8")
time.sleep(300)
"""

_HOLD_RUN_LOCK_SCRIPT = """
import sys
import time
from pathlib import Path

from dataset_factory.workdir.locks import RunLock

dsf = Path(sys.argv[1])
marker = Path(sys.argv[2])

lock = RunLock(dsf)
lock.acquire({"pid": 4321, "started_at": "2026-09-17T12:00:00+00:00", "batch": "s1"})
marker.write_text("held", encoding="utf-8")
time.sleep(300)
"""


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """一个真实存在的临时工作目录。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


def _wait_for_marker(marker: Path, deadline_seconds: float = 15.0) -> None:
    """等子进程持锁成功的标记文件出现（子进程启动 + import 需要一点时间）。"""
    limit = time.monotonic() + deadline_seconds
    while time.monotonic() < limit:
        if marker.is_file():
            return
        time.sleep(0.05)
    raise AssertionError("子进程未在时限内持锁（marker 未出现）")


def _spawn_holder(script: str, dsf: Path, marker: Path) -> subprocess.Popen[bytes]:
    """起一个持锁子进程（stdout/stderr 收进管道，失败时可读）。"""
    return subprocess.Popen(  # noqa: S603 — 命令与本仓测试脚本，无不可信输入
        [sys.executable, "-c", script, str(dsf), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_state_lock_blocks_other_thread_until_release(workdir: Path) -> None:
    """跨线程互斥：持有者释放前，另一线程的阻塞抢锁进不来；释放后照常进入。"""
    dsf = WorkdirStore(workdir).dsf_path
    held_by_a = StateLock(dsf)
    contender = StateLock(dsf)
    order: list[str] = []
    a_holding = threading.Event()
    release_a = threading.Event()

    def holder() -> None:
        held_by_a.acquire()
        try:
            order.append("a-in")
            a_holding.set()
            release_a.wait(5)
        finally:
            held_by_a.release()

    thread_a = threading.Thread(target=holder)
    thread_a.start()
    assert a_holding.wait(5)

    def contender_body() -> None:
        contender.acquire()
        try:
            order.append("b-in")
        finally:
            contender.release()

    thread_b = threading.Thread(target=contender_body)
    thread_b.start()
    # 给 b 一个「错误的时机窗口」：若互斥失效它会在 a 释放前进入，顺序断言即红。
    time.sleep(0.3)
    order.append("pre-release")
    release_a.set()

    thread_a.join(5)
    thread_b.join(5)

    assert order == ["a-in", "pre-release", "b-in"]


def test_state_lock_timeout_raises_diagnostic_error(workdir: Path) -> None:
    """跨线程等锁超时 → StateLockTimeoutError；持有者不受影响、锁可继续流转。

    竞争者必须放另一线程：同线程的两个 StateLock 包的是同一把共享底层锁，
    重入会直接成功（这正是实例共享的设计语义），永远到不了超时路径。
    """
    dsf = WorkdirStore(workdir).dsf_path
    holder = StateLock(dsf)
    holder.acquire()
    errors: list[BaseException] = []

    def contender_body() -> None:
        try:
            StateLock(dsf, timeout=0.1).acquire()
        except StateLockTimeoutError as exc:
            errors.append(exc)

    contender = threading.Thread(target=contender_body)
    contender.start()
    contender.join(5)

    assert len(errors) == 1
    assert "超时" in str(errors[0])

    holder.release()
    follower = StateLock(dsf, timeout=1)
    follower.acquire()
    follower.release()


def test_state_lock_instances_share_one_underlying_lock(workdir: Path) -> None:
    """同线程两个 StateLock 实例：重入成功 = 底层实例按 realpath 共享（否则 Windows 自锁）。"""
    dsf = WorkdirStore(workdir).dsf_path
    first = StateLock(dsf)
    second = StateLock(dsf)

    first.acquire()
    second.acquire()  # 若各自新建底层实例，这里在 Windows 上会同线程自锁死
    second.release()
    first.release()

    third = StateLock(dsf, timeout=1)
    third.acquire()
    third.release()  # 计数归零后可重新获取（上面的重入没有泄漏计数）


def test_state_lock_survives_holder_process_kill(workdir: Path) -> None:
    """持锁进程被强杀：无需手动清理即可继续写（文件锁随句柄自动释放）。"""
    dsf = WorkdirStore(workdir).dsf_path
    marker = workdir / "state-lock-marker.txt"
    child = _spawn_holder(_HOLD_STATE_LOCK_SCRIPT, dsf, marker)
    try:
        _wait_for_marker(marker)
        with pytest.raises(StateLockTimeoutError):
            StateLock(dsf, timeout=0.3).acquire()
    finally:
        child.kill()
        child.wait(15)

    store = WorkdirStore(workdir)
    store.mutate_state(lambda state: state.update({"recovered": True}))
    assert store.read_state() == {"recovered": True}


def test_run_lock_survives_holder_process_kill(workdir: Path) -> None:
    """运行锁同理：持锁子进程被强杀后，重新抢锁 + 写占用者信息照常成功。

    收尾抢锁带超时护栏地轮询：子进程被 TerminateProcess 后，OS 释放锁句柄有
    微小延迟，非阻塞单次尝试偶发撞上——被测性质是「无需人工清理即可继续」，
    短暂的内核释放延迟不违背它（10 秒内自然到手）。
    """
    dsf = WorkdirStore(workdir).dsf_path
    marker = workdir / "run-lock-marker.txt"
    child = _spawn_holder(_HOLD_RUN_LOCK_SCRIPT, dsf, marker)
    try:
        _wait_for_marker(marker)
        with pytest.raises(RunOccupiedError) as exc_info:
            RunLock(dsf).acquire({"pid": 9999, "batch": "s2"})
        occupier: dict[str, Any] | None = exc_info.value.occupier
        assert occupier is not None
        assert occupier["pid"] == 4321  # 占用者信息来自子进程写的 run-info.json
    finally:
        child.kill()
        child.wait(15)

    lock = RunLock(dsf)
    deadline = time.monotonic() + 10.0
    while True:
        try:
            lock.acquire({"pid": 9999, "batch": "s2"})
            break
        except RunOccupiedError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    try:
        info = json.loads((dsf / "run-info.json").read_text(encoding="utf-8"))
        assert info["pid"] == 9999  # 残留的 run-info 被新持有者覆盖
    finally:
        lock.release()
