"""单元测试：prompts 提示词库。

覆盖 md/frontmatter 解析、CRUD、_history 滚动备份、32 KiB 护栏、ID 身份与显示名
解耦、旧版数据读时迁移、原子写崩溃安全。全部离线、用 temp_data_root fixture 把
数据根隔离到临时目录，绝不碰真实 ~/.dataset_factory。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from dataset_factory.prompts import (
    PROMPT_ID_RE,
    Prompt,
    PromptError,
    PromptNameError,
    PromptNotFoundError,
    PromptParseError,
    PromptTooLargeError,
    delete_prompt,
    dump_prompt,
    list_prompts,
    parse_prompt,
    read_prompt,
    rename_prompt,
    save_prompt,
    seed_builtin_presets,
)


def _prompts_dir(root: Path) -> Path:
    return root / "prompts"


def _write_raw(root: Path, name: str, text: str) -> Path:
    """按旧版形态摆一个条目文件（文件名 = 显示名，frontmatter 可能只有 description）。"""
    directory = _prompts_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_dump_parse_round_trips() -> None:
    """序列化再解析，Prompt 原样还原（读写闭环）。"""
    original = Prompt(
        id="pabc123xyz", name="cap", description="生成中文描述", body="请描述这张图。"
    )

    parsed = parse_prompt(original.id, dump_prompt(original))

    assert parsed == original


def test_dump_parse_round_trips_tricky_description() -> None:
    """description 含 YAML 特殊字符（冒号、井号）也能安全 round-trip（safe_dump 自动加引号）。"""
    original = Prompt(
        id="pabc123xyz", name="x", description="键: 值 # 注释", body="正文"
    )

    parsed = parse_prompt(original.id, dump_prompt(original), fallback_name="x")

    assert parsed == original


def test_parse_without_frontmatter_is_lenient() -> None:
    """没有 frontmatter（不以 --- 开头）→ description 视为空、全文即正文（宽容手建条目）。"""
    parsed = parse_prompt("pabc123xyz", "就是正文，没有元数据", fallback_name="plain")

    assert parsed == Prompt(
        id="pabc123xyz", name="plain", description="", body="就是正文，没有元数据"
    )


def test_parse_unterminated_frontmatter_raises() -> None:
    """以 --- 开头但没有闭合的 --- → PromptParseError（fail loud）。"""
    with pytest.raises(PromptParseError, match="未闭合"):
        parse_prompt("bad", "---\ndescription: x\n没有结束符")


def test_parse_frontmatter_not_mapping_raises() -> None:
    """frontmatter 顶层不是键值映射（是列表）→ PromptParseError。"""
    with pytest.raises(PromptParseError, match="键值映射"):
        parse_prompt("bad", "---\n- a\n- b\n---\n正文")


def test_parse_frontmatter_bad_yaml_raises() -> None:
    """frontmatter 不是合法 YAML → PromptParseError。"""
    with pytest.raises(PromptParseError, match="YAML"):
        parse_prompt("bad", "---\na: b: c: [\n---\n正文")


def test_parse_unknown_frontmatter_key_raises() -> None:
    """frontmatter 含 name / description 以外的字段 → PromptParseError（挡住手误拼错字段名）。"""
    with pytest.raises(PromptParseError, match="未知字段"):
        parse_prompt("bad", "---\ndescriptions: 拼错了\n---\n正文")


def test_parse_non_string_description_raises() -> None:
    """description 不是字符串（YAML 把 123 解析成整数）→ PromptParseError。"""
    with pytest.raises(PromptParseError, match="description 应是字符串"):
        parse_prompt("bad", "---\ndescription: 123\n---\n正文")


def test_parse_missing_name_falls_back(temp_data_root: Path) -> None:
    """frontmatter 缺 name = 旧版条目：显示名回落 fallback（迁移前 = 旧文件名）。"""
    parsed = parse_prompt(
        "pabc123xyz", "---\ndescription: d\n---\n正文", fallback_name="旧名"
    )

    assert parsed.id == "pabc123xyz"
    assert parsed.name == "旧名"
    assert parsed.description == "d"


def test_save_then_read_round_trips(temp_data_root: Path) -> None:
    """保存后按 ID 读回，字段一致（读写闭环、库目录自动创建、ID 分配）。"""
    pid = save_prompt(Prompt(name="cap", description="描述", body="正文内容"))

    parsed = read_prompt(pid)

    assert parsed == Prompt(id=pid, name="cap", description="描述", body="正文内容")
    assert PROMPT_ID_RE.fullmatch(pid)


def test_read_by_unique_display_name(temp_data_root: Path) -> None:
    """读取入口宽容解析：唯一显示名也能定位（重名不唯一时报错）。"""
    pid = save_prompt(Prompt(name="唯一名", description="", body="正文"))

    assert read_prompt("唯一名").id == pid

    save_prompt(Prompt(name="唯一名", description="", body="另一条"))
    with pytest.raises(PromptNotFoundError):
        read_prompt("唯一名")  # 重名不唯一 → 拒绝按名定位


def test_list_prompts_empty_when_no_dir(temp_data_root: Path) -> None:
    """库目录还不存在 → list_prompts 返回空列表（尚无条目不算错）。"""
    assert list_prompts() == []


def test_list_prompts_sorted_and_skips_non_entries(temp_data_root: Path) -> None:
    """list_prompts 按显示名排序，且只认 prompts/ 下的普通 *.md（跳过 _history/ 与 . 前缀文件）。"""
    save_prompt(Prompt(name="zeta", description="", body="z"))
    save_prompt(Prompt(name="alpha", description="", body="a"))
    directory = _prompts_dir(temp_data_root)
    (directory / ".hidden.md").write_text("不该出现", encoding="utf-8")

    names = [prompt.name for prompt in list_prompts()]

    assert names == ["alpha", "zeta"]


def test_read_missing_raises(temp_data_root: Path) -> None:
    """读不存在的条目 → PromptNotFoundError。"""
    with pytest.raises(PromptNotFoundError, match="未找到"):
        read_prompt("nope")


def test_save_overwrite_by_id_backs_up_old_version(temp_data_root: Path) -> None:
    """按 ID 覆盖保存：旧版被复制进 _history/<ID>.<时间戳>.md，当前条目是新版。"""
    pid = save_prompt(Prompt(name="cap", description="", body="旧正文"))

    save_prompt(Prompt(id=pid, name="cap", description="", body="新正文"))

    assert read_prompt(pid).body == "新正文"
    history = list((_prompts_dir(temp_data_root) / "_history").glob(f"{pid}.*.md"))
    assert len(history) == 1
    assert "旧正文" in history[0].read_text(encoding="utf-8")


def test_save_same_display_name_creates_independent_entry(temp_data_root: Path) -> None:
    """同名再保存（不带 id）= 新建独立条目（身份是 ID，名字不再用于覆盖定位）。"""
    save_prompt(Prompt(name="cap", description="", body="第一条"))
    save_prompt(Prompt(name="cap", description="", body="第二条"))

    prompts = [p for p in list_prompts() if p.name == "cap"]
    assert len(prompts) == 2
    assert {p.body for p in prompts} == {"第一条", "第二条"}


def test_save_oversized_raises_and_writes_nothing(temp_data_root: Path) -> None:
    """超过 32 KiB → PromptTooLargeError，且不落盘、不建条目。"""
    huge = "x" * (32 * 1024 + 1)

    with pytest.raises(PromptTooLargeError, match="超过上限"):
        save_prompt(Prompt(name="big", description="", body=huge))

    assert list_prompts() == []


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "长" * 101,
    ],
)
def test_save_rejects_invalid_name(temp_data_root: Path, bad_name: str) -> None:
    """显示名只挡空、纯空白与超长（首尾空白静默 strip；文件名约束随 ID 化取消）。"""
    with pytest.raises(PromptNameError, match="名称"):
        save_prompt(Prompt(name=bad_name, description="", body="x"))


def test_save_strips_surrounding_whitespace(temp_data_root: Path) -> None:
    """首尾空白静默规整（strip 后保存，不拒绝）。"""
    pid = save_prompt(Prompt(name="  lead  ", description="", body="x"))

    assert read_prompt(pid).name == "lead"


@pytest.mark.parametrize(
    "legal_name", ["a/b", "..", ".hidden", "with.dot", "_history", "长" * 100]
)
def test_save_accepts_display_names_that_look_like_paths(
    temp_data_root: Path, legal_name: str
) -> None:
    """曾是文件名约束的形态现在合法（身份是 ID，显示名只是字符串）。"""
    pid = save_prompt(Prompt(name=legal_name, description="", body="x"))

    assert read_prompt(pid).name == legal_name


def test_delete_removes_entry_and_history(temp_data_root: Path) -> None:
    """删除条目：连同它的 _history/ 备份一起清掉，不留孤儿。"""
    pid = save_prompt(Prompt(name="cap", description="", body="v1"))
    save_prompt(Prompt(id=pid, name="cap", description="", body="v2"))
    history_dir = _prompts_dir(temp_data_root) / "_history"
    assert history_dir.is_dir()

    delete_prompt(pid)

    assert not (_prompts_dir(temp_data_root) / f"{pid}.md").exists()
    assert list(history_dir.glob(f"{pid}.*.md")) == []


def test_delete_by_unique_display_name(temp_data_root: Path) -> None:
    """删除入口宽容解析：唯一显示名也能删。"""
    save_prompt(Prompt(name="cap", description="", body="v1"))

    delete_prompt("cap")

    assert list_prompts() == []


def test_delete_missing_raises(temp_data_root: Path) -> None:
    """删除不存在的条目 → PromptNotFoundError。"""
    with pytest.raises(PromptNotFoundError, match="未找到"):
        delete_prompt("nope")


def test_history_evicts_beyond_keep_limit(temp_data_root: Path) -> None:
    """反复覆盖保存：某条目的历史被裁到最近 N=20 版，不会无限增长。"""
    pid = save_prompt(Prompt(name="cap", description="", body="v0"))
    for i in range(25):
        save_prompt(Prompt(id=pid, name="cap", description="", body=f"v{i + 1}"))

    history = list((_prompts_dir(temp_data_root) / "_history").glob(f"{pid}.*.md"))

    assert len(history) == 20


def test_history_same_stamp_versions_keep_creation_order(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同戳撞名加序号、序号即产生顺序：裁历史时裁掉的是最旧版，而不是字典序靠前的较新版。

    固定时钟让 21 次覆盖备份全部同戳（Windows 时钟粒度粗的真实场景）；历史裁到 N=20 后
    被裁的必须是基础时间戳版（最早那版）——字典序会把 `-1` 版排在基础版前面，按字典序
    裁就会错留旧版、裁掉较新版。
    """
    from dataset_factory.prompts import store

    class _FixedDatetime:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 9, 11, 12, 0, 0, 123456)

    monkeypatch.setattr(store, "datetime", _FixedDatetime)
    pid = save_prompt(Prompt(name="cap", description="", body="v0"))
    for i in range(22):
        save_prompt(Prompt(id=pid, name="cap", description="", body=f"v{i + 1}"))

    history_dir = _prompts_dir(temp_data_root) / "_history"
    remaining = sorted(p.name for p in history_dir.glob(f"{pid}.*.md"))

    assert len(remaining) == 20
    assert f"{pid}.20260911-120000-123456.md" not in remaining
    assert f"{pid}.20260911-120000-123456-1.md" in remaining


def test_read_corrupt_frontmatter_raises(temp_data_root: Path) -> None:
    """磁盘上的条目 frontmatter 损坏 → read_prompt 抛 PromptParseError（fail loud，不静默）。"""
    _write_raw(temp_data_root, "bad", "---\ndescription: x\n没有闭合")

    with pytest.raises(PromptParseError, match="未闭合"):
        read_prompt("bad")


def test_read_non_utf8_raises(temp_data_root: Path) -> None:
    """条目文件不是合法 UTF-8（写了非法字节）→ PromptParseError。"""
    directory = _prompts_dir(temp_data_root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bin.md").write_bytes(b"\xff\xfe\x00bad bytes")

    with pytest.raises(PromptParseError, match="UTF-8"):
        read_prompt("bin")


def test_save_atomic_write_failure_keeps_old_and_cleans_up(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """崩溃安全：覆盖时改名失败 → PromptError，旧版仍在、不留 .tmp（当前条目绝不损坏或丢失）。"""
    pid = save_prompt(Prompt(name="cap", description="", body="旧正文"))

    def _boom(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(PromptError, match="无法写入"):
        save_prompt(Prompt(id=pid, name="cap", description="", body="新正文"))

    assert read_prompt(pid).body == "旧正文"
    assert [
        p for p in _prompts_dir(temp_data_root).iterdir() if p.suffix == ".tmp"
    ] == []


def test_save_unencodable_body_raises(temp_data_root: Path) -> None:
    """正文含 UTF-8 无法编码的字符（孤立代理项）→ PromptError，不落盘。"""
    with pytest.raises(PromptError, match="无法编码"):
        save_prompt(Prompt(name="bad", description="", body="x" + chr(0xD800)))

    assert list_prompts() == []


def test_rename_writes_display_name_only(temp_data_root: Path) -> None:
    """改名 = 写 frontmatter 的 name 字段：文件（ID）、正文与历史全部不动。"""
    pid = save_prompt(Prompt(name="old", description="d", body="v1"))
    save_prompt(
        Prompt(id=pid, name="old", description="d", body="v2")
    )  # v1 进 _history
    assert len(list((_prompts_dir(temp_data_root) / "_history").iterdir())) == 1

    rename_prompt(pid, "new")

    assert [prompt.name for prompt in list_prompts()] == ["new"]
    assert read_prompt(pid).body == "v2"
    # 文件名（ID）与历史前缀都不动。
    assert (_prompts_dir(temp_data_root) / f"{pid}.md").is_file()
    history = list((_prompts_dir(temp_data_root) / "_history").iterdir())
    assert len(history) == 1
    assert history[0].name.startswith(f"{pid}.")
    # 旧显示名不再能解析（已无此名）。
    with pytest.raises(PromptNotFoundError):
        read_prompt("old")


def test_rename_duplicate_display_name_allowed(temp_data_root: Path) -> None:
    """改成已有显示名：允许重名（身份是 ID），两条内容各自不变。"""
    pid_a = save_prompt(Prompt(name="a", description="", body="A"))
    pid_b = save_prompt(Prompt(name="b", description="", body="B"))

    rename_prompt(pid_a, "b")

    assert read_prompt(pid_a).body == "A"
    assert read_prompt(pid_b).body == "B"
    assert {prompt.name for prompt in list_prompts()} == {"b"}


def test_rename_missing_source_raises(temp_data_root: Path) -> None:
    """条目不存在 → PromptNotFoundError，且不产生任何文件。"""
    with pytest.raises(PromptNotFoundError):
        rename_prompt("ghost", "new")

    assert list_prompts() == []


def test_rename_same_name_is_noop(temp_data_root: Path) -> None:
    """新旧同名 = no-op：不报错、不产生历史。"""
    pid = save_prompt(Prompt(name="a", description="", body="A"))

    rename_prompt(pid, "a")

    assert read_prompt(pid).body == "A"
    assert not (_prompts_dir(temp_data_root) / "_history").exists()


def test_seed_builtin_presets_writes_once(temp_data_root: Path) -> None:
    """首次播种写内置条目 + 标记；再调 no-op（不覆盖用户改过的同名条目）。"""
    seed_builtin_presets()
    assert [prompt.name for prompt in list_prompts()] == ["详细描述"]

    builtin_pid = list_prompts()[0].id
    save_prompt(
        Prompt(id=builtin_pid, name="详细描述", description="用户改的", body="用户正文")
    )
    seed_builtin_presets()

    assert read_prompt(builtin_pid).body == "用户正文"


def test_seed_builtin_presets_keeps_user_deletion(temp_data_root: Path) -> None:
    """用户删除内置条目后不再复活（标记文件已落盘）。"""
    seed_builtin_presets()
    builtin_pid = list_prompts()[0].id
    delete_prompt(builtin_pid)

    seed_builtin_presets()

    assert list_prompts() == []


def test_list_prompts_degrades_corrupt_entry(temp_data_root: Path) -> None:
    """列表对单个损坏条目宽容降级：description = 可读原因、body = 原始全文，其余条目不受影响。"""
    save_prompt(Prompt(name="good", description="好的", body="正文"))
    _write_raw(temp_data_root, "bad", "---\ndescription: x\n没有闭合")

    prompts = list_prompts()

    assert [p.name for p in prompts] == ["bad", "good"]
    broken = prompts[0]
    assert broken.description.startswith("文件损坏：")
    assert "未闭合" in broken.description  # 原因可读：哪里坏
    assert broken.body == "---\ndescription: x\n没有闭合"  # 原始全文：可在编辑列修复
    assert prompts[1].description == "好的"


def test_list_prompts_broken_entry_heals_after_fix(temp_data_root: Path) -> None:
    """损坏条目修复（保存合法内容）后，列表恢复健康形态（保存即自愈路径）。"""
    _write_raw(temp_data_root, "bad", "---\ndescription: x\n没有闭合")
    assert list_prompts()[0].description.startswith("文件损坏：")

    broken_pid = list_prompts()[0].id
    save_prompt(Prompt(id=broken_pid, name="bad", description="已修", body="完整正文"))

    prompts = list_prompts()
    assert len(prompts) == 1
    assert prompts[0].description == "已修"
    assert prompts[0].body == "完整正文"


def test_list_prompts_broken_entry_body_uses_replacement_chars(
    temp_data_root: Path,
) -> None:
    """损坏条目 body 读原始文本时容忍非法 UTF-8 字节（errors=replace，不二次抛错）。"""
    _prompts_dir(temp_data_root).mkdir(parents=True, exist_ok=True)
    path = _prompts_dir(temp_data_root) / "bin.md"
    path.write_bytes(b"---\nname: bin\n\xff\xfe not utf8")

    prompts = list_prompts()

    assert len(prompts) == 1
    assert prompts[0].name == "bin"


def test_legacy_entry_migrates_to_id(temp_data_root: Path) -> None:
    """旧版条目（文件名=显示名、frontmatter 只有 description）：读时惰性迁移。

    迁移 = 补 frontmatter name、文件改名换 ID、历史前缀跟移；内容一个字不变。
    """
    _write_raw(
        temp_data_root,
        "旧条目",
        "---\ndescription: 旧描述\n---\n旧正文\n",
    )
    history_dir = _prompts_dir(temp_data_root) / "_history"
    history_dir.mkdir()
    (history_dir / "旧条目.20260101-000000-000000.md").write_text(
        "---\ndescription: 旧描述\n---\n更旧正文\n", encoding="utf-8"
    )

    prompt = read_prompt("旧条目")  # 按旧显示名读 = 触发迁移

    assert prompt.name == "旧条目"
    assert prompt.description == "旧描述"
    assert prompt.body == "旧正文\n"
    assert PROMPT_ID_RE.fullmatch(prompt.id)
    # 文件已换 ID 名、frontmatter 带 name、历史前缀跟移；旧文件消失。
    new_path = _prompts_dir(temp_data_root) / f"{prompt.id}.md"
    assert new_path.is_file()
    assert not (_prompts_dir(temp_data_root) / "旧条目.md").exists()
    assert "name: 旧条目" in new_path.read_text(encoding="utf-8")
    migrated_history = list(history_dir.glob(f"{prompt.id}.*.md"))
    assert len(migrated_history) == 1
    assert "更旧正文" in migrated_history[0].read_text(encoding="utf-8")
    # 幂等：再次读取形态稳定。
    assert read_prompt(prompt.id).id == prompt.id
