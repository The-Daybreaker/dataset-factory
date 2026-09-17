"""集成测试：runs 执行器（运行锁 / 三件套 / full·retry 两模式 / 退避重试 / 断点续跑 / 停止 / 事件）。

全部离线：temp_data_root 隔离数据根，ScriptedCompleter 按脚本返回或抛错（不真调 API），
sleeper 注入假实现（退避不打真盹，只记录等待秒数）。跑批素材走真导入（import_assets），
批次走真创建（create_batch），产物与流水落 tmp_path 工作目录的 .dsf/。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from filelock import FileLock

from dataset_factory.llm import create_config
from dataset_factory.llm.errors import (
    LLMBadRequestError,
    LLMConnectionError,
    LLMRateLimitError,
)
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.runs import (
    BatchInactiveError,
    BatchRunner,
    RunEvent,
    RunJournalCorruptedError,
    read_retry_list,
)
from dataset_factory.strategies import (
    BatchNotFoundError,
    create_batch,
    set_batch_active,
)
from dataset_factory.workdir import RunOccupiedError, WorkdirStore, import_assets

_PROMPT_BODY = "你是打标助手。"


class ScriptedCompleter:
    """逐次按脚本返回文本或抛异常（耗尽后兜底回「打标结果」），记录调用次数。"""

    def __init__(self, script: Sequence[str | Exception] = ()) -> None:
        """以逐次动作脚本初始化（str = 返回文本，Exception = 抛出）。"""
        self._script = list(script)
        self.calls = 0

    def complete(self, messages: object) -> str:
        """弹出下一个脚本动作执行；脚本耗尽后固定回「打标结果」。"""
        self.calls += 1
        action = self._script.pop(0) if self._script else "打标结果"
        if isinstance(action, Exception):
            raise action
        return action

    def stream(self, messages: object) -> object:
        """批量跑批不该走流式路径——走到即测试失败。"""
        raise AssertionError("批量跑批不走流式路径")


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """一个真实存在的临时工作目录。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


@pytest.fixture
def batch(temp_data_root: Path, workdir: Path) -> Path:
    """预置完整可跑批环境：端点 + 提示词 + 两张已登记素材 + 一个批次，返回工作目录。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(Prompt(name="详细描述", description="d", body=_PROMPT_BODY))
    source = workdir.parent / "source"
    source.mkdir()
    (source / "cat_001.jpg").write_bytes(b"image-bytes-1")
    (source / "cat_002.jpg").write_bytes(b"image-bytes-2")
    import_assets(workdir, source)
    create_batch(
        workdir,
        name="一号批",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    return workdir


def _runner(
    workdir: Path,
    completer: object,
    *,
    mode: str = "full",
    seq: int = 1,
    sleeper: Callable[[float], None] | None = None,
) -> BatchRunner:
    """构造一个测试用执行器（默认 full 模式、web 触发）。"""
    return BatchRunner(
        workdir,
        seq,
        completer,
        mode=mode,  # type: ignore[arg-type]
        trigger="web",
        sleeper=sleeper,
    )


def _read_json(path: Path) -> dict[str, object]:
    """读一份 JSON 文件为字典。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _read_items(run_dir: Path) -> list[dict[str, object]]:
    """读一份 items.jsonl 为记录列表。"""
    raw = (run_dir / "items.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _last_run_dir(workdir: Path) -> Path:
    """取最近一次运行的目录（runs/ 下名字典序最大）。"""
    runs = sorted((WorkdirStore(workdir).dsf_path / "runs").iterdir())
    return runs[-1]


def _put_retry_list(workdir: Path, entries: list[dict[str, object]]) -> None:
    """直接把重试列表写进 state.json（T38 端点本体之前，测试从存储层搭景）。"""
    store = WorkdirStore(workdir)

    def seed(state: dict[str, object]) -> None:
        state["retry_list"] = entries

    store.mutate_state(seed)


# --------------------------------------------------------------------------
# full 模式：计划、产物、三件套
# --------------------------------------------------------------------------


def test_full_mode_labels_registered_items_and_writes_products(batch: Path) -> None:
    """full 模式：登记素材全部打标、产物按 sN__主干.txt 落盘；未登记素材不进计划。"""
    (batch / "stray.png").write_bytes(b"not-registered")
    completer = ScriptedCompleter()
    runner = _runner(batch, completer)

    report = runner.run()

    assert report.status == "completed"
    assert report.mode == "full"
    assert report.counters == {
        "planned": 2,
        "attempted": 2,
        "succeeded": 2,
        "failed": 0,
        "skipped": 0,
    }
    assert (batch / "s1__cat_001.txt").read_text(encoding="utf-8") == "打标结果"
    assert (batch / "s1__cat_002.txt").exists()
    assert not (batch / "s1__stray.txt").exists()
    assert completer.calls == 2


def test_full_mode_writes_complete_run_journal(batch: Path) -> None:
    """三件套：run.json 字段齐全、items.jsonl 只记实际调用条目、run.log 有始有终。

    items 行带素材哈希；run.log 含启动段与结束统计。
    """
    runner = _runner(batch, ScriptedCompleter())

    report = runner.run()

    run_json = _read_json(report.run_dir / "run.json")
    assert run_json["run_id"] == report.run_dir.name
    assert run_json["batch"] == 1
    assert run_json["mode"] == "full"
    assert run_json["trigger"] == "web"
    assert run_json["status"] == "completed"
    assert run_json["counters"] == report.counters
    assert isinstance(run_json["strategy_hash"], str)
    assert len(str(run_json["strategy_hash"])) == 64
    assert run_json["snapshot"] == "strategies/s1.json"
    assert run_json["started_at"] is not None
    assert run_json["finished_at"] is not None
    assert run_json["dsf_version"]

    items = _read_items(report.run_dir)
    assert len(items) == 2
    first = items[0]
    assert first["item"] == "cat_001"
    assert first["batch"] == 1
    assert first["status"] == "succeeded"
    assert first["attempt"] == 1
    assert first["asset_hash"] == hashlib.sha256(b"image-bytes-1").hexdigest()
    assert isinstance(first["elapsed_ms"], int)

    log_text = (report.run_dir / "run.log").read_text(encoding="utf-8")
    assert "启动" in log_text
    assert "结束（completed）" in log_text
    assert "cat_001 尝试 1 成功" in log_text


def test_run_info_removed_and_lock_released_after_run(batch: Path) -> None:
    """跑完之后：run-info.json 已清、运行锁已释放（可直接再次抢锁）。"""
    _runner(batch, ScriptedCompleter()).run()

    store = WorkdirStore(batch)
    assert not (store.dsf_path / "run-info.json").exists()
    probe = FileLock(store.dsf_path / "run.lock")
    probe.acquire(timeout=0)  # 抢得到 = 释放了
    probe.release()


# --------------------------------------------------------------------------
# 断点续跑（E1：哈希比对跳过判定）
# --------------------------------------------------------------------------


def test_resume_skips_items_whose_product_matches_asset_hash(batch: Path) -> None:
    """续跑：产物在且素材未变 → 跳过（不调模型、不记流水行，只进 skipped 计数）。"""
    _runner(batch, ScriptedCompleter()).run()

    completer = ScriptedCompleter()
    report = _runner(batch, completer).run()

    assert report.status == "completed"
    assert report.counters["skipped"] == 2
    assert report.counters["attempted"] == 0
    assert completer.calls == 0
    assert not (_last_run_dir(batch) / "items.jsonl").exists()


def test_resume_relables_item_whose_asset_changed_after_labeling(batch: Path) -> None:
    """打标后素材被换（哈希不一致）：该条按未完成重打，另一条照常跳过。"""
    _runner(batch, ScriptedCompleter()).run()
    (batch / "cat_001.jpg").write_bytes(b"replaced-image-bytes")

    completer = ScriptedCompleter()
    report = _runner(batch, completer).run()

    assert report.counters["skipped"] == 1
    assert report.counters["succeeded"] == 1
    assert completer.calls == 1
    items = _read_items(_last_run_dir(batch))
    assert items[0]["item"] == "cat_001"
    assert items[0]["asset_hash"] == hashlib.sha256(b"replaced-image-bytes").hexdigest()


def test_resume_relables_when_product_blank_or_without_anchor(batch: Path) -> None:
    """产物空白 / 产物在但无打标锚点（从未成功打过）：都按未完成重打。"""
    (batch / "s1__cat_001.txt").write_text("   ", encoding="utf-8")
    (batch / "s1__cat_002.txt").write_text("手工产物", encoding="utf-8")

    completer = ScriptedCompleter()
    report = _runner(batch, completer).run()

    assert report.counters["succeeded"] == 2
    assert report.counters["skipped"] == 0


def test_e1_anchor_is_per_batch_not_global(batch: Path) -> None:
    """E1 锚点按批次隔离（审计 P1 回归）：s1 后来对新材料打的标，不能当 s2 的锚点。

    s2 用素材 v1 打标 → 素材换成 v2 → s1 对 v2 打标 → s2 续跑必须重打
    （s2 的产物出自 v1；若锚点串批，s1 的 v2 哈希会让 s2 的过期产物被错误跳过）。
    """
    create_batch(
        batch,
        name="二号批",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    runner_s2_first = _runner(batch, ScriptedCompleter(), seq=2)
    runner_s2_first.run()  # s2 用 v1 打标（产物 s2__cat_001/002.txt + batch=2 流水）

    (batch / "cat_001.jpg").write_bytes(b"image-v2-bytes")  # 素材换成 v2

    runner_s1 = _runner(batch, ScriptedCompleter(), seq=1)
    runner_s1.run()  # s1 对 v2 打标（batch=1 流水记 v2 哈希）

    completer_s2 = ScriptedCompleter()
    report_s2 = _runner(batch, completer_s2, seq=2).run()

    # cat_001：s2 产物出自 v1，素材已到 v2 → 重打；cat_002：素材没变 → 正常跳过。
    assert report_s2.counters["skipped"] == 1
    assert completer_s2.calls == 1
    assert (batch / "s2__cat_001.txt").read_text(encoding="utf-8") == "打标结果"


def test_run_info_write_failure_does_not_leak_lock(
    batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁路径 OSError 防御（审计 P1 回归）：run-info 写失败只损失提示、锁照常持有。

    锁在结束时正常释放——该工作目录不会死锁到进程重启。
    """
    from dataset_factory.workdir import locks as runs_lock_module

    def _broken_write(path: Path, text: str) -> None:
        raise OSError("磁盘满（模拟）")

    monkeypatch.setattr(runs_lock_module, "atomic_write_text", _broken_write)

    report = _runner(batch, ScriptedCompleter()).run()

    assert report.status == "completed"
    assert not (WorkdirStore(batch).dsf_path / "run-info.json").exists()
    probe = FileLock(WorkdirStore(batch).dsf_path / "run.lock")
    probe.acquire(timeout=0)  # 抢得到 = 锁已正常释放
    probe.release()


def test_same_stem_multi_extension_uses_one_source(batch: Path) -> None:
    """同主干多扩展并存（审计 P2 回归）：计划比对与执行读取同源，E1 跳过判定生效。

    导入器按完整文件名查重、同主干两种扩展可并存（手工放置场景）；若计划与执行
    各取各的文件，E1 哈希永远对不上、每轮都重打。
    """
    (batch / "cat_001.png").write_bytes(b"png-variant")
    store = WorkdirStore(batch)
    store.append_import_record(
        {
            "imported_at": "2026-09-16T00:00:00+00:00",
            "source": "x",
            "files": [{"name": "cat_001.png", "sha256": "x"}],
        }
    )

    _runner(batch, ScriptedCompleter()).run()  # 首轮：两个条目都打标

    completer = ScriptedCompleter()
    report = _runner(batch, completer).run()

    # 两条全部命中锚点 = 哈希比对与读取同源；若计划与执行各取各的文件，
    # cat_001 会对不上锚点而重打（calls == 1、skipped == 1）。
    assert report.counters["skipped"] == 2
    assert completer.calls == 0


# --------------------------------------------------------------------------
# retry 模式：快照执行 + 出列
# --------------------------------------------------------------------------


def test_retry_mode_runs_snapshot_and_removes_succeeded_items(batch: Path) -> None:
    """retry 模式：只打快照条目；成功的出列、失败的保留；其他批次条目不受影响。"""
    _put_retry_list(
        batch,
        [
            {"batch": 1, "item": "cat_001"},
            {"batch": 1, "item": "cat_002"},
            {"batch": 2, "item": "其他批条的"},
        ],
    )
    completer = ScriptedCompleter(["好描述", LLMBadRequestError("参数不合法")])

    report = _runner(batch, completer, mode="retry").run()

    assert report.counters == {
        "planned": 2,
        "attempted": 2,
        "succeeded": 1,
        "failed": 1,
        "skipped": 0,
    }
    assert (batch / "s1__cat_001.txt").exists()
    assert not (batch / "s1__cat_002.txt").exists()
    assert read_retry_list(batch, 1) == ["cat_002"]
    assert read_retry_list(batch, 2) == ["其他批条的"]


def test_explicit_retry_preserves_unselected_products_and_retry_entries(
    batch: Path,
) -> None:
    """明确重打只覆盖所选产物，成功项出列且其他批次与未选名单原样保留。"""
    _runner(batch, ScriptedCompleter()).run()
    untouched = (batch / "s1__cat_002.txt").read_bytes()
    _put_retry_list(
        batch,
        [
            {"batch": 1, "item": "cat_001"},
            {"batch": 1, "item": "cat_002"},
            {"batch": 2, "item": "cat_001"},
        ],
    )
    selected = ["cat_001", "cat_001"]
    completer = ScriptedCompleter(["新的描述"])
    runner = BatchRunner(
        batch, 1, completer, mode="retry", trigger="web", retry_items=selected
    )
    selected.append("cat_002")

    runner.run()

    assert completer.calls == 1
    assert (batch / "s1__cat_001.txt").read_text(encoding="utf-8") == "新的描述"
    assert (batch / "s1__cat_002.txt").read_bytes() == untouched
    assert read_retry_list(batch, 1) == ["cat_002"]
    assert read_retry_list(batch, 2) == ["cat_001"]
    assert [row["item"] for row in _read_items(_last_run_dir(batch))] == ["cat_001"]


def test_retry_mode_missing_asset_fails_without_model_call(batch: Path) -> None:
    """名单内素材缺失：照常发车、读取失败按 asset-unreadable 记账，不调模型。"""
    _put_retry_list(batch, [{"batch": 1, "item": "ghost"}])
    completer = ScriptedCompleter()

    report = _runner(batch, completer, mode="retry").run()

    assert report.counters["failed"] == 1
    assert completer.calls == 0
    items = _read_items(_last_run_dir(batch))
    assert items[0]["reason_code"] == "asset-unreadable"
    assert "缺失" in str(items[0]["message"])


# --------------------------------------------------------------------------
# 失败重试（F5）：分类、退避、Retry-After
# --------------------------------------------------------------------------


def test_retryable_error_retries_with_backoff_then_succeeds(batch: Path) -> None:
    """可重试错误：退避后重试至成功；等待秒数按 1s 基数 ±20% 抖动。

    cat_001 第一次失败重试成功，cat_002 走脚本兜底成功——共 3 次调用、1 次退避。
    """
    slept: list[float] = []
    completer = ScriptedCompleter([LLMConnectionError("网络断了"), "好描述"])

    report = _runner(batch, completer, sleeper=slept.append).run()

    assert report.counters["succeeded"] == 2
    assert report.counters["failed"] == 0
    assert completer.calls == 3
    assert len(slept) == 1
    assert 0.8 <= slept[0] <= 1.2
    items = _read_items(_last_run_dir(batch))
    assert items[0]["item"] == "cat_001"
    assert items[0]["attempt"] == 2
    assert items[0]["status"] == "succeeded"


def test_retry_exhausted_after_four_attempts_then_recorded_failed(
    batch: Path,
) -> None:
    """可重试错误 4 次全失败：按失败记账（attempt=4），重试等待共 3 次。

    cat_001 四连败判死，cat_002 兜底成功——共 5 次调用。
    """
    slept: list[float] = []
    completer = ScriptedCompleter([LLMConnectionError("还是断")] * 4)

    report = _runner(batch, completer, sleeper=slept.append).run()

    assert report.counters["failed"] == 1
    assert report.counters["succeeded"] == 1
    assert completer.calls == 5
    assert len(slept) == 3
    items = _read_items(_last_run_dir(batch))
    assert items[0]["status"] == "failed"
    assert items[0]["attempt"] == 4
    assert items[0]["reason_code"] == "network"
    assert not (batch / "s1__cat_001.txt").exists()


def test_non_retryable_error_fails_immediately(batch: Path) -> None:
    """不可重试错误（bad-request）：一次请求即判死，不退避不重试。

    cat_001 判死、cat_002 兜底成功——共 2 次调用。
    """
    completer = ScriptedCompleter([LLMBadRequestError("请求非法")])

    report = _runner(batch, completer, sleeper=lambda _s: pytest.fail("不该退避")).run()

    assert report.counters["failed"] == 1
    assert report.counters["succeeded"] == 1
    assert completer.calls == 2
    items = _read_items(_last_run_dir(batch))
    assert items[0]["reason_code"] == "bad-request"


def test_rate_limit_follows_endpoint_retry_after(batch: Path) -> None:
    """429：优先遵循端点 Retry-After 秒数（原样等待，不乘抖动）。"""
    slept: list[float] = []
    completer = ScriptedCompleter(
        [LLMRateLimitError("限流了", retry_after=7.5), "好描述"]
    )

    _runner(batch, completer, sleeper=slept.append).run()

    assert slept == [7.5]
    items = _read_items(_last_run_dir(batch))
    assert items[0]["attempt"] == 2


def test_blank_caption_treated_as_retryable_content_failure(batch: Path) -> None:
    """模型返回空白描述：按 llm-content 可重试处理，重试成功后正常落产物。

    cat_001 两次空白第三次成功（3 次调用），cat_002 兜底成功——共 4 次。
    """
    slept: list[float] = []
    completer = ScriptedCompleter(["", "   ", "第三次像样了"])

    report = _runner(batch, completer, sleeper=slept.append).run()

    assert report.counters["succeeded"] == 2
    assert completer.calls == 4
    assert (batch / "s1__cat_001.txt").read_text(encoding="utf-8") == "第三次像样了"


# --------------------------------------------------------------------------
# 运行锁与批次状态
# --------------------------------------------------------------------------


def test_occupied_workdir_rejected_with_occupier_info(batch: Path) -> None:
    """工作目录已被占用：拒绝启动并带占用者信息（来自 run-info.json）。"""
    store = WorkdirStore(batch)
    lock = FileLock(store.dsf_path / "run.lock")
    lock.acquire()
    (store.dsf_path / "run-info.json").write_text(
        json.dumps({"pid": 4321, "started_at": "2026-09-16T22:00:00+00:00"}),
        encoding="utf-8",
    )
    try:
        with pytest.raises(RunOccupiedError) as exc_info:
            _runner(batch, ScriptedCompleter()).run()
        occupier = exc_info.value.occupier
        assert occupier is not None
        assert occupier["pid"] == 4321
        assert ScriptedCompleter().calls == 0
    finally:
        lock.release()


def test_inactive_batch_rejected(batch: Path) -> None:
    """停用（隐藏）的批次不允许跑批。"""
    set_batch_active(batch, 1, False)

    with pytest.raises(BatchInactiveError):
        _runner(batch, ScriptedCompleter()).run()


def test_unknown_batch_raises_batch_not_found(batch: Path) -> None:
    """批次不存在：strategies 域异常直接冒泡。"""
    with pytest.raises(BatchNotFoundError):
        _runner(batch, ScriptedCompleter(), seq=99).run()


# --------------------------------------------------------------------------
# 停止与事件流
# --------------------------------------------------------------------------


def test_hidden_batch_interrupts_without_in_process_stop_signal(batch: Path) -> None:
    """另一个入口只写隐藏状态时，执行器在条目边界停止并保留已完成产物。"""
    completer = ScriptedCompleter()
    runner = _runner(batch, completer)

    def hide_after_first(event: RunEvent) -> None:
        if event.kind == "item-updated":
            set_batch_active(batch, 1, False)

    runner.subscribe(hide_after_first)
    report = runner.run()

    assert report.status == "interrupted"
    assert completer.calls == 1
    assert (batch / "s1__cat_001.txt").is_file()
    assert not (batch / "s1__cat_002.txt").exists()


def test_stop_interrupts_between_items_and_keeps_finished_part(batch: Path) -> None:
    """第一条打完置位停止：状态 interrupted、第二条没跑、已完成部分保留。"""
    completer = ScriptedCompleter()
    runner = _runner(batch, completer)

    original_complete = completer.complete

    def _complete_and_stop(messages: object) -> str:
        text = original_complete(messages)
        runner.stop()  # 第一条打完即请求停止
        return text

    completer.complete = _complete_and_stop  # type: ignore[method-assign]

    report = runner.run()

    assert report.status == "interrupted"
    assert report.counters["succeeded"] == 1
    assert report.counters["attempted"] == 1
    assert completer.calls == 1
    assert (batch / "s1__cat_001.txt").exists()
    assert not (batch / "s1__cat_002.txt").exists()
    run_json = _read_json(report.run_dir / "run.json")
    assert run_json["status"] == "interrupted"


def test_events_emitted_in_order_with_payloads(batch: Path) -> None:
    """事件序：run-started → (item started / succeeded)×N → run-finished，载荷可序列化。"""
    received: list[RunEvent] = []
    runner = _runner(batch, ScriptedCompleter())
    runner.subscribe(received.append)

    runner.run()

    kinds = [event.kind for event in received]
    assert kinds == [
        "run-started",
        "item-updated",
        "item-updated",
        "item-updated",
        "item-updated",
        "run-finished",
    ]
    assert received[0].to_payload()["planned"] == 2
    assert received[1].to_payload() == {
        "item": "cat_001",
        "batch": 1,
        "status": "started",
        "attempt": 0,
        "reason_code": None,
        "message": None,
    }
    assert received[-1].to_payload()["status"] == "completed"


def test_subscriber_exception_does_not_break_the_run(batch: Path) -> None:
    """订阅者回调抛异常：只被吞掉记日志，跑批照常完成（消费者不连累生产者）。"""

    def _bad_callback(_event: RunEvent) -> None:
        raise RuntimeError("订阅者炸了")

    runner = _runner(batch, ScriptedCompleter())
    runner.subscribe(_bad_callback)

    report = runner.run()

    assert report.status == "completed"


def test_subscription_after_completion_receives_terminal_event(batch: Path) -> None:
    """订阅与收尾交错时立即获得终态，不会挂起等待已错过的事件。"""
    runner = _runner(batch, ScriptedCompleter())
    report = runner.run()
    received: list[RunEvent] = []

    unsubscribe = runner.subscribe(received.append)
    unsubscribe()

    assert len(received) == 1
    assert received[0].kind == "run-finished"
    assert received[0].to_payload()["run_id"] == report.run_id


# --------------------------------------------------------------------------
# 历史流水损坏：fail loud
# --------------------------------------------------------------------------


def test_corrupt_history_journal_fails_loud_before_anything_runs(batch: Path) -> None:
    """历史 items.jsonl 损坏（中间坏行）：续跑判定 fail loud，且不留新 run 目录。"""
    bad_run = WorkdirStore(batch).dsf_path / "runs" / "20260101T000000Z"
    bad_run.mkdir(parents=True)
    (bad_run / "items.jsonl").write_text(
        '{"item": "x", "batch": 1, "status": "succeeded", "attempt": 1}\n'
        "{broken json}\n",
        encoding="utf-8",
    )

    runner = _runner(batch, ScriptedCompleter())
    received: list[RunEvent] = []
    runner.subscribe(received.append)

    with pytest.raises(RunJournalCorruptedError):
        runner.run()

    runs = list((WorkdirStore(batch).dsf_path / "runs").iterdir())
    assert [path.name for path in runs] == ["20260101T000000Z"]
    assert received[-1].to_payload()["error"] == runner.snapshot()["error"]
    assert received[-1].to_payload()["status"] == "failed"
