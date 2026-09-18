"""单元测试：strategies 数据域（库 CRUD / 引用健康度 / 快照装配 / 批次生命周期）。

库落数据根（temp_data_root 隔离）；批次落 tmp_path 工作目录的 .dsf/。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from dataset_factory.llm import create_config
from dataset_factory.prompts import Prompt, delete_prompt, save_prompt
from dataset_factory.skills import import_skill
from dataset_factory.strategies import (
    BatchEntry,
    BatchNotFoundError,
    StrategyError,
    StrategyNameError,
    StrategyNotFoundError,
    StrategyRefsError,
    add_exclusions,
    apply_library_strategy,
    copy_strategy,
    create_batch,
    create_strategy,
    delete_batch,
    delete_strategy,
    get_batch,
    get_strategy,
    list_batches,
    list_strategies,
    missing_refs,
    parse_seq,
    read_snapshot,
    rebind_strategy,
    remove_exclusions,
    set_batch_active,
    strategy_content_hash,
    update_batch,
    update_strategy,
)
from dataset_factory.workdir import WorkdirStore

_FIXTURE_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
_SKILL_NAME = "example-caption-skill"
_PROMPT_BODY = "你是打标器"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """一个真实存在的临时工作目录。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


@pytest.fixture
def assets(temp_data_root: Path) -> None:
    """预置一套可引用的资产：端点配置 + 提示词 + Skill（golden 包）。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(
        Prompt(name="详细描述", description="描述画面", body=_PROMPT_BODY),
    )
    import_skill(_FIXTURE_PACK)


def _sha(text: str) -> str:
    """测试内独立计算 SHA-256（不读实现代码，防两侧共用同一假设）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 策略库
# --------------------------------------------------------------------------


def test_create_returns_entry_with_stable_identity(assets: None) -> None:
    """新建库策略：ID 随机分配、时间戳就位、Skill 引用保序。"""
    entry = create_strategy(
        name=" 详细描述A ",
        endpoint="main",
        prompt="详细描述",
        skills=[_SKILL_NAME],
        description="第一套",
    )

    assert entry.id
    assert entry.name == "详细描述A"
    assert entry.created_at
    assert entry.updated_at
    assert get_strategy(entry.id).id == entry.id


def test_create_rejects_missing_reference(assets: None) -> None:
    """引用的提示词不存在 → StrategyRefsError（创建时 fail fast）。"""
    with pytest.raises(StrategyRefsError, match="不存在"):
        create_strategy(
            name="坏策略", endpoint="main", prompt="没有的提示词", skills=[]
        )


def test_create_rejects_blank_name(assets: None) -> None:
    """空名字（含纯空白）→ StrategyNameError。"""
    with pytest.raises(StrategyNameError):
        create_strategy(name="  ", endpoint="main", prompt="详细描述", skills=[])


def test_list_sorted_by_name(assets: None) -> None:
    """列表按显示名排序（同名按 ID 稳定序）。"""
    create_strategy(name="B套", endpoint="main", prompt="详细描述", skills=[])
    create_strategy(name="A套", endpoint="main", prompt="详细描述", skills=[])

    assert [entry.name for entry in list_strategies()] == ["A套", "B套"]


def test_get_unknown_id_raises_not_found(assets: None) -> None:
    """查不存在的库策略 → StrategyNotFoundError（404 档）。"""
    with pytest.raises(StrategyNotFoundError):
        get_strategy("no-such-id")


def test_update_replaces_all_fields(assets: None) -> None:
    """整条更新（策略页「保存」）：组合与元数据整体替换。"""
    entry = create_strategy(name="旧名", endpoint="main", prompt="详细描述", skills=[])
    save_prompt(Prompt(name="另一条", description="", body="另一套正文"))

    updated = update_strategy(
        entry.id,
        name="新名",
        endpoint="main",
        prompt="另一条",
        skills=[_SKILL_NAME],
        description="改过",
    )

    assert updated.name == "新名"
    assert updated.prompt == "另一条"
    assert updated.skills == [_SKILL_NAME]
    assert updated.description == "改过"


def test_rebind_updates_only_provided_refs(assets: None) -> None:
    """重新指定：只动提供的引用位，其余保持不变。"""
    save_prompt(Prompt(name="替补", description="", body="替补正文"))
    entry = create_strategy(
        name="策略", endpoint="main", prompt="详细描述", skills=[_SKILL_NAME]
    )

    rebound = rebind_strategy(entry.id, prompt="替补")

    assert rebound.prompt == "替补"
    assert rebound.endpoint == "main"
    assert rebound.skills == [_SKILL_NAME]


def test_copy_derives_new_id_same_content(assets: None) -> None:
    """复制一份：新 ID、组合原样（允许重名所以名字不变）。"""
    source = create_strategy(
        name="原版", endpoint="main", prompt="详细描述", skills=[_SKILL_NAME]
    )

    clone = copy_strategy(source.id)

    assert clone.id != source.id
    assert clone.name == source.name
    assert clone.prompt == source.prompt
    assert clone.skills == source.skills


def test_delete_removes_entry(assets: None) -> None:
    """删除库策略；重复删除报不存在。"""
    entry = create_strategy(name="待删", endpoint="main", prompt="详细描述", skills=[])

    delete_strategy(entry.id)

    assert list_strategies() == []
    with pytest.raises(StrategyNotFoundError):
        delete_strategy(entry.id)


def test_health_turns_unavailable_when_prompt_deleted(assets: None) -> None:
    """引用的提示词被删 → 健康度现查为不可用，missing_refs 给可读原因。"""
    entry = create_strategy(
        name="策略", endpoint="main", prompt="详细描述", skills=[_SKILL_NAME]
    )
    delete_prompt("详细描述")

    problems = missing_refs(get_strategy(entry.id))

    assert problems == ["基础提示词「详细描述」不存在"]


def test_rebind_restores_health(assets: None) -> None:
    """置灰 → 重新指定 → 恢复可用（PRD 验收 18 的处置闭环）。"""
    save_prompt(Prompt(name="替补", description="", body="替补正文"))
    entry = create_strategy(name="策略", endpoint="main", prompt="详细描述", skills=[])
    delete_prompt("详细描述")
    assert missing_refs(get_strategy(entry.id))

    rebind_strategy(entry.id, prompt="替补")

    assert missing_refs(get_strategy(entry.id)) == []


def test_corrupted_library_file_fails_loud(temp_data_root: Path, assets: None) -> None:
    """库文件损坏 → StrategyError（fail loud，不静默藏起来）。"""
    strategies_dir = temp_data_root / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(StrategyError, match="损坏"):
        list_strategies()


# --------------------------------------------------------------------------
# 快照装配 + 批次生命周期
# --------------------------------------------------------------------------


def test_scratch_batch_creates_snapshot_and_state(workdir: Path, assets: None) -> None:
    """从零配置新建批次：快照全文落盘、state.json 记录、产物计数为 0。"""
    entry = create_batch(
        workdir,
        name="第一套",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[_SKILL_NAME],
    )

    assert entry.seq == 1
    assert entry.snapshot == "s1.json"
    assert list_batches(workdir) == [entry]
    assert not list(workdir.glob("s1__*.txt"))

    snapshot = read_snapshot(workdir, 1)
    assert snapshot.prompt["name"] == "详细描述"
    assert snapshot.prompt["body"] == _PROMPT_BODY
    assert snapshot.prompt["sha256"] == _sha(_PROMPT_BODY)
    assert snapshot.endpoint["name"] == "main"
    assert snapshot.endpoint["base_url"] == "https://api.example.com/v1"
    assert snapshot.skills[0]["name"] == _SKILL_NAME
    assert snapshot.source is None


def test_seq_increments_and_never_reused(workdir: Path, assets: None) -> None:
    """序号只增不复用：删掉 s2 后新建的是 s3（历史记录里的 s2 不混淆）。"""
    create_batch(
        workdir,
        name="一",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    second = create_batch(
        workdir,
        name="二",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    delete_batch(workdir, second.seq)

    create_batch(
        workdir,
        name="三",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )

    assert [batch.seq for batch in list_batches(workdir)] == [1, 3]


def test_concurrent_create_batches_allocate_distinct_seqs(
    workdir: Path, assets: None
) -> None:
    """并发建批（Web 与 CLI 各建一个）：序号不撞、两个批次都登记在册（状态锁收口）。"""
    barrier = threading.Barrier(2)
    results: list[BatchEntry] = []
    errors: list[Exception] = []

    def create(name: str) -> None:
        try:
            barrier.wait(5)
            results.append(
                create_batch(
                    workdir,
                    name=name,
                    description="",
                    endpoint="main",
                    prompt="详细描述",
                    skills=[],
                )
            )
        except Exception as exc:  # noqa: BLE001 — 线程内兜底收集，主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(n,)) for n in ("甲", "乙")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert errors == []
    assert sorted(entry.seq for entry in results) == [1, 2]
    assert [entry.seq for entry in list_batches(workdir)] == [1, 2]
    assert {entry.name for entry in list_batches(workdir)} == {"甲", "乙"}


def test_concurrent_update_batches_keep_both_changes(
    workdir: Path, assets: None
) -> None:
    """并发改两个批次的名字（丢更新场景）：两个改动都落盘，谁也不覆盖谁。"""
    first = create_batch(
        workdir,
        name="一",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    second = create_batch(
        workdir,
        name="二",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def rename(seq: int, name: str) -> None:
        try:
            barrier.wait(5)
            update_batch(workdir, seq, name=name)
        except Exception as exc:  # noqa: BLE001 — 线程内兜底收集，主线程断言
            errors.append(exc)

    threads = [
        threading.Thread(target=rename, args=(first.seq, "一改")),
        threading.Thread(target=rename, args=(second.seq, "二改")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert errors == []
    names = {entry.seq: entry.name for entry in list_batches(workdir)}
    assert names == {first.seq: "一改", second.seq: "二改"}


def test_apply_library_records_copy_on_apply_source(
    workdir: Path, assets: None
) -> None:
    """应用库策略：快照记来源（库 ID + 应用时刻组合哈希），名称沿用库策略。"""
    library_entry = create_strategy(
        name="库里的策略",
        endpoint="main",
        prompt="详细描述",
        skills=[_SKILL_NAME],
        description="说明文字",
    )

    entry = apply_library_strategy(workdir, library_entry.id)

    assert entry.name == "库里的策略"
    assert entry.description == "说明文字"
    snapshot = read_snapshot(workdir, entry.seq)
    assert snapshot.source is not None
    assert snapshot.source["strategy_id"] == library_entry.id
    expected_hash = strategy_content_hash(get_strategy(library_entry.id))
    assert snapshot.source["strategy_sha256"] == expected_hash


def test_apply_library_with_missing_ref_rejected(workdir: Path, assets: None) -> None:
    """置灰策略不可应用（引用缺失 → StrategyRefsError）。"""
    library_entry = create_strategy(
        name="会失效的策略", endpoint="main", prompt="详细描述", skills=[]
    )
    delete_prompt("详细描述")

    with pytest.raises(StrategyRefsError, match="不可应用"):
        apply_library_strategy(workdir, library_entry.id)


def test_library_edits_do_not_affect_applied_batch(workdir: Path, assets: None) -> None:
    """copy-on-apply 隔离：库端换引用 / 删提示词，已应用批次快照原样。"""
    library_entry = create_strategy(
        name="策略", endpoint="main", prompt="详细描述", skills=[]
    )
    entry = apply_library_strategy(workdir, library_entry.id)
    before = read_snapshot(workdir, entry.seq)

    save_prompt(Prompt(name="详细描述", description="", body="库端改了正文"))
    update_strategy(
        library_entry.id, name="策略", endpoint="main", prompt="详细描述", skills=[]
    )

    after = read_snapshot(workdir, entry.seq)
    assert after.prompt["body"] == before.prompt["body"] == _PROMPT_BODY


def test_update_batch_metadata_keeps_snapshot(workdir: Path, assets: None) -> None:
    """只改名称 / 描述：快照不动（改显示别名不牵动内容）。"""
    entry = create_batch(
        workdir,
        name="旧名",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    before = read_snapshot(workdir, entry.seq)

    updated = update_batch(workdir, entry.seq, name="新名", description="备注")

    assert updated.name == "新名"
    assert updated.description == "备注"
    after = read_snapshot(workdir, entry.seq)
    assert after.built_at == before.built_at


def test_hide_and_unhide_batch(workdir: Path, assets: None) -> None:
    """停用 / 召回：active 翻转，列表仍含停用批次（设置页要能召回）。"""
    entry = create_batch(
        workdir,
        name="策略",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )

    hidden = set_batch_active(workdir, entry.seq, active=False)
    assert hidden.active is False
    assert [batch.seq for batch in list_batches(workdir)] == [entry.seq]

    restored = set_batch_active(workdir, entry.seq, active=True)
    assert restored.active is True


def test_delete_batch_removes_products_and_snapshot(
    workdir: Path, assets: None
) -> None:
    """删除批次：产物 txt + 快照 + state 记录 + 排除名单一并移除，返回产物数。"""
    entry = create_batch(
        workdir,
        name="策略",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    (workdir / "s1__cat_001.txt").write_text("产物", encoding="utf-8")
    (workdir / "s1__clip_001.txt").write_text("产物2", encoding="utf-8")
    add_exclusions(workdir, entry.seq, ["cat_001"])

    count = delete_batch(workdir, entry.seq)

    assert count == 2
    assert list_batches(workdir) == []
    assert not (workdir / ".dsf" / "strategies" / "s1.json").exists()
    state = WorkdirStore(workdir).read_state()
    assert state.get("exclusions", {}) == {}
    with pytest.raises(BatchNotFoundError):
        get_batch(workdir, entry.seq)


def test_delete_batch_commits_state_before_removing_files(
    workdir: Path, assets: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删批先提交状态、再删文件：删文件失败留下的是无主产物，不是「批次还在、产物少一半」。"""
    entry = create_batch(
        workdir,
        name="策略",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    product = workdir / "s1__cat_001.txt"
    product.write_text("产物", encoding="utf-8")
    original_unlink = Path.unlink

    def _busy_product(self: Path, missing_ok: bool = False) -> None:
        if self.suffix == ".txt":
            raise PermissionError("产物被别的程序占用")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _busy_product)

    with pytest.raises(PermissionError):
        delete_batch(workdir, entry.seq)

    monkeypatch.undo()

    assert list_batches(workdir) == []
    assert product.exists()


def test_parse_seq_strict_format() -> None:
    """sN 解析：严格 s + 正整数；不合法一律按不存在处理（404 档）。"""
    assert parse_seq("s12") == 12
    for bad in ("1", "s", "sx", "s0", "s-1", "s 1"):
        with pytest.raises(BatchNotFoundError):
            parse_seq(bad)


def test_library_id_shape_guard(assets: None) -> None:
    """ID 形状校验：路径穿越形状（../、分隔符等）一律按不存在处理（404 档）。"""
    for bad in ("../x", "a/b", "a\\b", ".", "..", "a b"):
        with pytest.raises(StrategyNotFoundError):
            get_strategy(bad)


def test_seq_never_wraps_after_deletes(workdir: Path, assets: None) -> None:
    """序号计数只增不减：连删高序号批次后新建不复用（删除路径不把计数写回去）。"""
    for name in ("一", "二", "三"):
        create_batch(
            workdir,
            name=name,
            description="",
            endpoint="main",
            prompt="详细描述",
            skills=[],
        )
    delete_batch(workdir, 3)
    delete_batch(workdir, 2)

    fourth = create_batch(
        workdir,
        name="四",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )

    assert fourth.seq == 4


def test_exclusions_dedupe_and_remove(workdir: Path, assets: None) -> None:
    """排除名单：加入幂等去重、撤销移出、批次间互不干扰。"""
    first = create_batch(
        workdir,
        name="一",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )
    second = create_batch(
        workdir,
        name="二",
        description="",
        endpoint="main",
        prompt="详细描述",
        skills=[],
    )

    added = add_exclusions(workdir, first.seq, ["cat_001", "cat_001", "dog_001"])

    assert added == ["cat_001", "dog_001"]
    assert add_exclusions(workdir, second.seq, ["clip_001"]) == ["clip_001"]

    assert remove_exclusions(workdir, first.seq, ["cat_001"]) == ["dog_001"]
    assert remove_exclusions(workdir, second.seq, ["clip_001"]) == []
