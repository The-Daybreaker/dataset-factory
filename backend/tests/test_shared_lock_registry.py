"""并发回归测试：锁实例共享、跨进程写锁的两两互斥，以及「同线程不得自锁」这条缺陷的哨兵。

这一批属**缺陷修复**而非纯重构（Windows + filelock 3.32 实测：同一线程拿两个不同
``FileLock`` 实例去锁同一个文件，第二个 ``acquire()`` 连 3 秒都拿不到）。因此判据不是
「跑起来不报错」，而是四件事都能被机器看见：

1. 同线程在同一把锁上嵌套进入 → 必须能过（走可重入计数，而不是自锁到超时）；
2. 两个线程争同一把锁 → 必须串行（临界区里的计数器不允许交错）；
3. 提示词的「备份 + 换版」在并发保存下不互相吞历史；
4. 技能与提示词的写锁共用同一份注册表（同一把物理锁全进程一个实例）。
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from dataset_factory._locks import shared_file_lock
from dataset_factory.prompts import Prompt, list_prompts, save_prompt
from dataset_factory.prompts import store as prompt_store
from dataset_factory.prompts.errors import PromptError
from dataset_factory.skills import store as skill_store
from dataset_factory.workdir.locks import StateLock

LOCK_TIMEOUT = 20.0


def _prompt(name: str, body: str) -> Prompt:
    """测试便捷封装：一条合法提示词。"""
    return Prompt(name=name, description="", body=body)


def test_nested_acquire_on_same_path_does_not_self_lock(tmp_path: Path) -> None:
    """同线程在同一锁文件上嵌套进入：靠共享实例的可重入计数通过，不自锁。"""
    target = tmp_path / "same.lock"
    outer = shared_file_lock(target)

    with outer:
        inner = shared_file_lock(tmp_path / "same.lock")
        inner.acquire(timeout=LOCK_TIMEOUT)
        inner.release()

    assert shared_file_lock(target) is outer


def test_two_threads_serialise_on_one_lock(tmp_path: Path) -> None:
    """两个线程争同一把锁：临界区必须串行，计数不丢更新。"""
    lock_path = tmp_path / "counter.lock"
    counter = {"value": 0}
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(40):
                with shared_file_lock(lock_path).acquire(timeout=LOCK_TIMEOUT):
                    seen = counter["value"]
                    threading.Event().wait(0.001)
                    counter["value"] = seen + 1
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(LOCK_TIMEOUT * 4)

    assert not errors
    assert counter["value"] == 160


def test_prompts_and_skills_share_the_registry(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """三个域取同一把物理锁 → 同一个实例（各自新建实例的写法正是自锁来源）。"""
    prompts_lock = tmp_path / "shared-name.edit.lock"
    skills_lock = tmp_path / "shared-skill.edit.lock"

    first = shared_file_lock(prompts_lock)
    second = shared_file_lock(prompts_lock)
    other = shared_file_lock(skills_lock)

    assert first is second
    assert first is not other
    assert shared_file_lock is prompt_store.shared_file_lock
    assert shared_file_lock is skill_store.shared_file_lock


def test_concurrent_prompt_saves_keep_every_history_entry(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发保存同一条提示词：历史一份不吞、当前版是两次写入之一的完整内容。"""
    save_prompt(_prompt("caption", "第一版"))
    history_dir = temp_data_root / "prompts" / "_history"
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def writer(body: str) -> None:
        try:
            barrier.wait(LOCK_TIMEOUT)
            save_prompt(_prompt("caption", body))
        except BaseException as exc:  # noqa: BLE001 - 异常带回主线程断言
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(f"第 {index} 版",))
        for index in range(1, 3)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(LOCK_TIMEOUT)
    for thread in threads:
        thread.join(LOCK_TIMEOUT * 2)

    assert not errors
    assert len(list_prompts()) == 1
    history = sorted(path.name for path in history_dir.glob("caption.*.md"))
    assert len(history) == 2
    current = (temp_data_root / "prompts" / "caption.md").read_text(encoding="utf-8")
    assert "第 1 版" in current or "第 2 版" in current


def test_prompt_lock_reports_busy_when_another_writer_holds_it(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """另一个写者长期持锁：本方超时即报可操作的忙，且不改磁盘内容。

    必须换个线程持锁——同线程嵌套是共享实例的可重入，本来就该过（前一条用例就是它）。
    """
    save_prompt(_prompt("locked", "原版"))
    lock_path = temp_data_root / "prompts" / ".locked.edit.lock"
    acquired = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with shared_file_lock(lock_path).acquire(timeout=LOCK_TIMEOUT):
            acquired.set()
            release.wait(LOCK_TIMEOUT)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(LOCK_TIMEOUT)
    monkeypatch.setattr("dataset_factory.prompts.store._EDIT_LOCK_TIMEOUT_SECONDS", 0.2)
    try:
        with pytest.raises(PromptError, match="正在被另一个进程修改"):
            save_prompt(_prompt("locked", "新版"))
    finally:
        release.set()
        thread.join(LOCK_TIMEOUT)

    assert "原版" in (temp_data_root / "prompts" / "locked.md").read_text(
        encoding="utf-8"
    )


def test_only_the_registry_constructs_file_locks() -> None:
    """除注册表本身，产品代码里不许再出现 `FileLock(...)` 构造（按 AST 认，不误伤文档）。

    自建实例就是这次自锁缺陷的来源；这条静态守卫把它挡在源码层。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "dataset_factory"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name == "_locks.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "FileLock":
                offenders.append(f"{path.relative_to(src)}:{node.lineno}")

    assert offenders == []


def test_state_lock_is_reentrant_in_one_thread(tmp_path: Path) -> None:
    """工作目录状态锁的既有姿势不回退：同线程嵌套仍然只在最外层真正持锁。"""
    dsf = tmp_path / ".dsf"
    dsf.mkdir()
    outer = StateLock(dsf)
    inner = StateLock(dsf)

    outer.acquire()
    try:
        # 内层是「另一个实例、同一把物理锁、同一线程」——旧写法在这里必失败（自锁）。
        inner.acquire()
        inner.release()
    finally:
        outer.release()
