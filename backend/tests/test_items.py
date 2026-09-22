"""集成测试：条目视图读模型（左列六分组 / 搜索 / 缺失来源探测 / 按批次隔离）。

条目状态**不落库**，每次现算——所以这里的搭景就是「把磁盘摆成某个样子」：素材与
产物直接写文件，运行流水经真实写入器 RunJournal 落盘，重试列表直接写 state.json
（T38 之前没有端点）。最后一条测试跑真执行器，证明视图读的确实是跑批写下的那份流水。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataset_factory.llm import StreamDelta, create_config
from dataset_factory.llm.errors import LLMBadRequestError
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.runs import (
    GROUP_DONE,
    GROUP_FAILED,
    GROUP_MISSING,
    GROUP_QUEUED,
    GROUP_RETRY,
    GROUP_UNIMPORTED,
    ITEM_GROUPS,
    BatchRunner,
    ItemRow,
    ItemView,
    RunJournal,
    build_item_view,
    load_latest_item_records,
)
from dataset_factory.strategies import BatchNotFoundError, create_batch
from dataset_factory.workdir import (
    WorkdirStore,
    import_assets,
    product_filename,
)

_RUN_OLD = "20260912T143005Z"
_RUN_NEW = "20260912T144005Z"


class ScriptedCompleter:
    """逐次按脚本返回文本或抛异常（耗尽后兜底回「打标结果」）。"""

    def __init__(self, script: list[str | Exception]) -> None:
        """以逐次动作脚本初始化（str = 返回文本，Exception = 抛出）。"""
        self._script = list(script)

    def complete(self, messages: object) -> str:
        """弹出下一个脚本动作执行。"""
        action = self._script.pop(0) if self._script else "打标结果"
        if isinstance(action, Exception):
            raise action
        return action

    def stream(self, messages: object) -> object:
        """批量跑批走流式路径（A2，2026-09-21 起）：按脚本逐段产出正文增量。"""
        action = self._script.pop(0) if self._script else "打标结果"
        if isinstance(action, Exception):
            raise action
        return iter([StreamDelta(kind="content", text=action)])


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """一个真实存在的临时工作目录。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


@pytest.fixture
def batch(temp_data_root: Path, workdir: Path) -> Path:
    """预置完整环境：端点 + 提示词 + 三张已登记素材（两图一视频）+ 批次 s1。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(Prompt(name="详细描述", description="d", body="你是打标助手。"))
    source = workdir.parent / "source"
    source.mkdir()
    (source / "cat_001.jpg").write_bytes(b"image-bytes-1")
    (source / "cat_002.png").write_bytes(b"image-bytes-2")
    (source / "clip_001.mp4").write_bytes(b"video-bytes-1")
    import_assets(workdir, source)
    create_batch(
        workdir,
        name="一号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    return workdir


def _succeeded(item: str, *, attempt: int = 1) -> dict[str, object]:
    """一条成功流水（字段与执行器写下的完全一致）。"""
    return {
        "item": item,
        "status": "succeeded",
        "attempt": attempt,
        "asset_hash": f"hash-of-{item}",
        "elapsed_ms": 120,
    }


def _failed(
    item: str,
    reason_code: str,
    *,
    attempt: int = 4,
    message: str = "端点连续报错",
) -> dict[str, object]:
    """一条失败流水（带原因码与人读消息）。"""
    return {
        "item": item,
        "status": "failed",
        "attempt": attempt,
        "reason_code": reason_code,
        "message": message,
        "elapsed_ms": 3400,
    }


def _write_run(
    workdir: Path, run_id: str, seq: int, records: list[dict[str, object]]
) -> None:
    """经真实写入器落一次运行的条目流水（视图只读 items.jsonl，三件套的其余两件不参与判定）。"""
    journal = RunJournal(WorkdirStore(workdir).runs_dir / run_id)
    for record in records:
        journal.append_item({"batch": seq, **record})


def _put_retry_list(workdir: Path, entries: list[dict[str, object]]) -> None:
    """直接把重试列表写进 state.json（T38 端点本体之前，测试从存储层搭景）。"""
    store = WorkdirStore(workdir)

    def seed(state: dict[str, object]) -> None:
        state["retry_list"] = entries

    store.mutate_state(seed)


def _names(view: ItemView, group: str) -> list[str]:
    """取某个分组里的条目主干（按视图给的顺序）。"""
    return [row.item for row in view.groups[group]]


def _row(view: ItemView, group: str, item: str) -> ItemRow:
    """取某个分组里的指定行。"""
    return next(row for row in view.groups[group] if row.item == item)


# --------------------------------------------------------------------------
# 四个互斥状态位
# --------------------------------------------------------------------------


def test_registered_items_without_products_are_all_queued(batch: Path) -> None:
    """刚导入还没跑批：三张登记素材全在排队中，媒体形态按扩展名分好。"""
    view = build_item_view(batch, 1)

    assert _names(view, GROUP_QUEUED) == ["cat_001", "cat_002", "clip_001"]
    assert _names(view, GROUP_DONE) == []
    assert _names(view, GROUP_FAILED) == []
    assert _names(view, GROUP_MISSING) == []
    first = _row(view, GROUP_QUEUED, "cat_001")
    assert (first.name, first.media, first.can_retry) == ("cat_001.jpg", "image", False)
    assert _row(view, GROUP_QUEUED, "clip_001").media == "video"


def test_non_empty_product_puts_item_in_done(batch: Path) -> None:
    """产物存在且非空白 = 已完成；已完成条目允许重打（可以进重试列表）。"""
    (batch / product_filename(1, "cat_001")).write_text("一段描述", encoding="utf-8")

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_DONE) == ["cat_001"]
    assert _names(view, GROUP_QUEUED) == ["cat_002", "clip_001"]
    assert _row(view, GROUP_DONE, "cat_001").can_retry is True


def test_blank_product_is_not_done(batch: Path) -> None:
    """产物为空 / 全空白属产物异常：不算已完成，回到排队中等重打（PRD F4）。"""
    (batch / product_filename(1, "cat_001")).write_text("  \n ", encoding="utf-8")

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_DONE) == []
    assert "cat_001" in _names(view, GROUP_QUEUED)


def test_failed_record_puts_item_in_failed_with_reason(batch: Path) -> None:
    """最近一次尝试失败的条目进未完成，行内带尝试次数、原因码与人读消息。"""
    _write_run(
        batch,
        _RUN_OLD,
        1,
        [_failed("cat_002", "rate-limit", attempt=3, message="限流")],
    )

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_FAILED) == ["cat_002"]
    row = _row(view, GROUP_FAILED, "cat_002")
    assert (row.attempt, row.reason_code, row.message) == (3, "rate-limit", "限流")
    assert row.can_retry is True


def test_non_retryable_failure_disables_retry(batch: Path) -> None:
    """不可重试类失败（请求非法 / 配置错 / 素材读不出）不允许进重试列表——界面据此置灰。"""
    _write_run(batch, _RUN_OLD, 1, [_failed("cat_002", "bad-request")])

    assert _row(build_item_view(batch, 1), GROUP_FAILED, "cat_002").can_retry is False


def test_failed_record_wins_over_stale_product(batch: Path) -> None:
    """盘上留着旧产物、但最近一次尝试失败 → 未完成（报「已完成」会让用户以为新素材已打好）。"""
    (batch / product_filename(1, "cat_001")).write_text(
        "旧素材的描述", encoding="utf-8"
    )
    _write_run(batch, _RUN_NEW, 1, [_failed("cat_001", "asset-unreadable")])

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_DONE) == []
    assert _names(view, GROUP_FAILED) == ["cat_001"]


def test_latest_record_is_per_item_not_per_run(batch: Path) -> None:
    """逐条取最近记录：窄运行（只跑重试名单）不会把上一轮全量跑批的失败账整体抹掉。"""
    _write_run(
        batch,
        _RUN_OLD,
        1,
        [_failed("cat_001", "network"), _failed("cat_002", "network")],
    )
    (batch / product_filename(1, "cat_002")).write_text("补打成功", encoding="utf-8")
    _write_run(batch, _RUN_NEW, 1, [_succeeded("cat_002")])

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_FAILED) == ["cat_001"]
    assert _names(view, GROUP_DONE) == ["cat_002"]


def test_success_after_failure_returns_to_done(batch: Path) -> None:
    """先失败后成功：条目回到已完成（最近一次尝试说了算）。"""
    _write_run(batch, _RUN_OLD, 1, [_failed("cat_001", "timeout")])
    (batch / product_filename(1, "cat_001")).write_text("重打成功", encoding="utf-8")
    _write_run(batch, _RUN_NEW, 1, [_succeeded("cat_001", attempt=2)])

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_DONE) == ["cat_001"]
    assert _names(view, GROUP_FAILED) == []


def test_other_batch_records_do_not_leak(batch: Path) -> None:
    """runs/ 是工作目录级共享：s2 的失败流水不能出现在 s1 的视图里（行内 batch 字段做过滤）。"""
    create_batch(
        batch,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    _write_run(batch, _RUN_OLD, 2, [_failed("cat_001", "network")])

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_FAILED) == []
    assert "cat_001" in _names(view, GROUP_QUEUED)


def test_second_batch_view_is_independent(batch: Path) -> None:
    """条目状态按「策略 × 素材」算：同一份素材在 s1 已完成、在 s2 仍是排队中。"""
    create_batch(
        batch,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    (batch / product_filename(1, "cat_001")).write_text(
        "一号批的描述", encoding="utf-8"
    )

    assert _names(build_item_view(batch, 1), GROUP_DONE) == ["cat_001"]
    assert _names(build_item_view(batch, 2), GROUP_DONE) == []
    assert "cat_001" in _names(build_item_view(batch, 2), GROUP_QUEUED)


def test_unknown_batch_raises(batch: Path) -> None:
    """批次不存在当场报错，不算一份没人要的视图。"""
    with pytest.raises(BatchNotFoundError):
        build_item_view(batch, 9)


# --------------------------------------------------------------------------
# 缺失（第四个状态位）与来源探测
# --------------------------------------------------------------------------


def test_missing_asset_grouped_with_recoverable_source(batch: Path) -> None:
    """登记在册但素材被删 → 缺失；来源那儿还有这份文件，「重新导入」可点。"""
    (batch / "cat_001.jpg").unlink()
    source_file = batch.parent / "source" / "cat_001.jpg"

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_MISSING) == ["cat_001"]
    row = _row(view, GROUP_MISSING, "cat_001")
    assert row.recoverable is True
    assert row.source == str(source_file)
    assert row.can_retry is False
    assert _names(view, GROUP_QUEUED) == ["cat_002", "clip_001"]


def test_missing_asset_with_gone_source_not_recoverable(batch: Path) -> None:
    """来源那份也没了 → 不可找回，界面据此置灰「重新导入」并改走「从别处导入」。"""
    (batch / "cat_001.jpg").unlink()
    (batch.parent / "source" / "cat_001.jpg").unlink()

    row = _row(build_item_view(batch, 1), GROUP_MISSING, "cat_001")

    assert row.recoverable is False


def test_missing_asset_applies_to_every_batch(batch: Path) -> None:
    """素材缺失对所有策略同时成立（素材共享，它没了各策略的产物同失）。"""
    create_batch(
        batch,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    (batch / "clip_001.mp4").unlink()

    assert _names(build_item_view(batch, 1), GROUP_MISSING) == ["clip_001"]
    assert _names(build_item_view(batch, 2), GROUP_MISSING) == ["clip_001"]


# --------------------------------------------------------------------------
# 重试列表（叠加标记）与未导入
# --------------------------------------------------------------------------


def test_retry_group_follows_list_order_and_marks_source_rows(batch: Path) -> None:
    """重试列表按名单顺序成组（名单顺序即重试顺序），条目仍留在自己的分组并标「已排重试」。"""
    (batch / product_filename(1, "cat_001")).write_text("描述", encoding="utf-8")
    _put_retry_list(
        batch,
        [{"batch": 1, "item": "cat_002"}, {"batch": 1, "item": "cat_001"}],
    )

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_RETRY) == ["cat_002", "cat_001"]
    assert _row(view, GROUP_RETRY, "cat_001").status == GROUP_DONE
    assert _row(view, GROUP_DONE, "cat_001").in_retry is True
    assert _row(view, GROUP_QUEUED, "cat_002").in_retry is True
    assert _row(view, GROUP_QUEUED, "clip_001").in_retry is False


def test_other_batch_retry_entries_are_ignored(batch: Path) -> None:
    """名单条目带批次序号：s2 的名单不出现在 s1 的重试分组里。"""
    create_batch(
        batch,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    _put_retry_list(
        batch,
        [{"batch": 2, "item": "cat_001"}, {"batch": 1, "item": "cat_002"}],
    )

    assert _names(build_item_view(batch, 1), GROUP_RETRY) == ["cat_002"]
    assert _names(build_item_view(batch, 2), GROUP_RETRY) == ["cat_001"]


def test_stale_retry_entry_is_skipped(batch: Path) -> None:
    """名单里出现视图没有的主干（手工改过 state.json 才会发生）：跳过，不为它现造一个状态。"""
    _put_retry_list(
        batch,
        [{"batch": 1, "item": "ghost_999"}, {"batch": 1, "item": "cat_001"}],
    )

    assert _names(build_item_view(batch, 1), GROUP_RETRY) == ["cat_001"]


def test_unimported_group_lists_unregistered_files_with_reasons(batch: Path) -> None:
    """工作目录里没登记过的文件进未导入（带原因），产物 txt 不列入。"""
    (batch / "scene.heic").write_bytes(b"unsupported")
    (batch / "stray.jpg").write_bytes(b"dropped-by-hand")
    (batch / product_filename(1, "cat_001")).write_text("描述", encoding="utf-8")

    view = build_item_view(batch, 1)

    assert _names(view, GROUP_UNIMPORTED) == ["scene", "stray"]
    reasons = {row.item: row.reason for row in view.groups[GROUP_UNIMPORTED]}
    assert reasons == {"scene": "扩展名不支持", "stray": "未登记"}
    assert all(row.can_retry is False for row in view.groups[GROUP_UNIMPORTED])


def test_all_six_group_keys_always_present(batch: Path) -> None:
    """六个分组恒在（空组给空列表），界面不必先判键存不存在。"""
    view = build_item_view(batch, 1)

    assert set(view.groups) == set(ITEM_GROUPS)
    assert view.batch == 1
    assert view.query == ""


# --------------------------------------------------------------------------
# 搜索
# --------------------------------------------------------------------------


def test_query_filters_rows_across_groups(batch: Path) -> None:
    """搜索按文件名做子串匹配、跨全部六个分组生效，命中数即该组计数。"""
    (batch / product_filename(1, "cat_001")).write_text("描述", encoding="utf-8")
    (batch / "clip_001.mp4").unlink()

    view = build_item_view(batch, 1, query="cat")

    assert _names(view, GROUP_DONE) == ["cat_001"]
    assert _names(view, GROUP_QUEUED) == ["cat_002"]
    assert _names(view, GROUP_MISSING) == []
    assert view.query == "cat"


def test_query_is_case_insensitive_and_ignores_padding(batch: Path) -> None:
    """搜索大小写不敏感、首尾空白不算（与原型输入框的行为同口径）。"""
    upper = build_item_view(batch, 1, query="  CAT_001 ")
    lower = build_item_view(batch, 1, query="cat_001")

    assert _names(upper, GROUP_QUEUED) == ["cat_001"]
    assert _names(upper, GROUP_QUEUED) == _names(lower, GROUP_QUEUED)


def test_query_without_hit_leaves_every_group_empty(batch: Path) -> None:
    """搜不到就六组全空（不是报错，也不是回退成全量）。"""
    view = build_item_view(batch, 1, query="不存在的名字")

    assert all(rows == [] for rows in view.groups.values())


# --------------------------------------------------------------------------
# 与真执行器的衔接
# --------------------------------------------------------------------------


def test_view_reads_what_the_runner_actually_wrote(batch: Path) -> None:
    """跑一次真执行器（一条不可重试失败 + 一条成功），视图如实反映流水与产物。"""
    slept: list[float] = []
    runner = BatchRunner(
        batch,
        1,
        ScriptedCompleter([LLMBadRequestError("参数不合法")]),
        mode="full",
        trigger="web",
        sleeper=slept.append,
    )

    report = runner.run()
    view = build_item_view(batch, 1)

    assert (report.counters["succeeded"], report.counters["failed"]) == (2, 1)
    assert _names(view, GROUP_FAILED) == ["cat_001"]
    assert _row(view, GROUP_FAILED, "cat_001").reason_code == "bad-request"
    assert _row(view, GROUP_FAILED, "cat_001").can_retry is False
    assert _names(view, GROUP_DONE) == ["cat_002", "clip_001"]


def test_load_latest_item_records_returns_empty_without_runs(batch: Path) -> None:
    """从没跑过批：没有流水可读，映射为空（条目一律按排队中处理）。"""
    assert load_latest_item_records(WorkdirStore(batch).runs_dir, 1) == {}
