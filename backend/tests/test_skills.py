"""单元测试：skills Skill 库（frontmatter 解析、导入复制自包含、启用/停用、读全文、格式校验、重名不合并）。

全部离线、用 temp_data_root fixture 隔离数据根；导入源用 tests/fixtures/skill-pack（手写标准包，兼作 golden）。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from dataset_factory.skills import (
    SkillError,
    SkillExistsError,
    SkillFileNotPreviewableError,
    SkillFilePathError,
    SkillFormatError,
    SkillNameError,
    SkillNotFoundError,
    delete_skill,
    import_skill,
    import_skill_files,
    list_skill_files,
    list_skills,
    parse_skill_frontmatter,
    read_skill,
    read_skill_file,
    rename_skill,
    set_enabled,
)
from dataset_factory.skills import store as skill_store
from dataset_factory.skills.store import save_skill_file

_FIXTURE_PACK = Path(__file__).parent / "fixtures" / "skill-pack"
_FIXTURE_NAME = "example-caption-skill"


def _skills_dir(root: Path) -> Path:
    return root / "skills"


def _make_source(tmp_path: Path, skill_md: str, dirname: str = "src") -> Path:
    source = tmp_path / dirname
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (source / "references" / "detail.md").write_text("补充参考", encoding="utf-8")
    return source


def test_parse_golden_fixture() -> None:
    """契约测试：解析手写标准 SKILL.md（含多余的 license 字段），取出 name + description。"""
    text = (_FIXTURE_PACK / "SKILL.md").read_text(encoding="utf-8")

    name, description = parse_skill_frontmatter(text)

    assert name == _FIXTURE_NAME
    assert description.startswith("示例 skill")


def test_concurrent_disables_preserve_both_changes(
    temp_data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个技能同时停用时，第二个写者在锁内读取第一份更新而不丢失状态。"""
    for name in ("first", "second"):
        import_skill(
            _make_source(tmp_path, f"---\nname: {name}\ndescription: test\n---\n", name)
        )
    first_writing = threading.Event()
    second_waiting = threading.Event()
    write_disabled = skill_store._write_disabled  # pyright: ignore[reportPrivateUsage]
    mutation_lock = skill_store._mutation_lock  # pyright: ignore[reportPrivateUsage]

    def write(disabled: set[str]) -> None:
        if disabled == {"first"}:
            first_writing.set()
            assert second_waiting.wait(timeout=5)
        write_disabled(disabled)

    @contextmanager
    def lock(path: Path) -> Generator[None]:
        if path.name == ".state.lock" and first_writing.is_set():
            second_waiting.set()
        with mutation_lock(path):
            yield

    monkeypatch.setattr(skill_store, "_write_disabled", write)
    monkeypatch.setattr(skill_store, "_mutation_lock", lock)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(set_enabled, "first", False)
        assert first_writing.wait(timeout=5)
        second = executor.submit(set_enabled, "second", False)
        first.result(timeout=5)
        second.result(timeout=5)

    assert {skill.name for skill in list_skills() if not skill.enabled} == {
        "first",
        "second",
    }


def test_delete_holds_file_edit_and_state_locks(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除包及状态记录时，两把锁均被持有，其他保存或启用操作无法交错。"""
    import_skill(_FIXTURE_PACK)
    set_enabled(_FIXTURE_NAME, False)
    remove = skill_store.shutil.rmtree
    checked: list[Path] = []

    def remove_locked(target: Path) -> None:
        for name in (f".{_FIXTURE_NAME}.edit.lock", ".state.lock"):
            with pytest.raises(Timeout), FileLock(str(target.parent / name), timeout=0):
                pytest.fail("删除期间写锁没有被持有")
        checked.append(target)
        remove(target)

    monkeypatch.setattr(skill_store.shutil, "rmtree", remove_locked)

    delete_skill(_FIXTURE_NAME)

    assert checked == [temp_data_root / "skills" / _FIXTURE_NAME]
    state = (temp_data_root / "skills" / "_state.json").read_text(encoding="utf-8")
    assert json.loads(state)["disabled"] == []


@pytest.mark.parametrize("path", ["SKILL.md", "references/detail.md"])
def test_save_skill_file_roundtrip(temp_data_root: Path, path: str) -> None:
    """保存主文件和参考文件后，磁盘与注入内容同步更新。"""
    import_skill(_FIXTURE_PACK)
    original = read_skill_file(_FIXTURE_NAME, path)
    updated = original + "\n新增写作要求\n"

    result = save_skill_file(_FIXTURE_NAME, path, updated, original_content=original)

    assert result == updated
    assert (_skills_dir(temp_data_root) / _FIXTURE_NAME / path).read_text(
        encoding="utf-8"
    ) == updated
    assert "新增写作要求" in read_skill(_FIXTURE_NAME)


def test_save_skill_file_rejects_stale_draft(temp_data_root: Path) -> None:
    """迟到保存不能覆盖另一编辑器已写入的内容。"""
    import_skill(_FIXTURE_PACK)
    original = read_skill_file(_FIXTURE_NAME, "SKILL.md")
    save_skill_file(
        _FIXTURE_NAME, "SKILL.md", original + "\n先保存", original_content=original
    )

    with pytest.raises(SkillExistsError, match="其他写者"):
        save_skill_file(
            _FIXTURE_NAME, "SKILL.md", original + "\n迟到", original_content=original
        )

    assert read_skill_file(_FIXTURE_NAME, "SKILL.md") == original + "\n先保存"


@pytest.mark.parametrize(
    "bad_path", ["/etc/passwd", "references\\..\\x.md", "../escape.md"]
)
def test_save_skill_file_rejects_unsafe_paths(
    temp_data_root: Path, tmp_path: Path, bad_path: str
) -> None:
    """写回与读取共用包内路径闸门：绝对路径、反斜杠与目录上跳都不能保存。"""
    import_skill(_make_full_source(tmp_path))
    escape_target = temp_data_root / "escape.md"
    escape_target.write_text("不该被覆盖", encoding="utf-8")

    with pytest.raises(SkillFilePathError):
        save_skill_file(
            "full-pack",
            bad_path,
            "恶意内容",
            original_content="",
        )

    assert escape_target.read_text(encoding="utf-8") == "不该被覆盖"


def test_save_skill_file_waits_for_concurrent_writer(
    temp_data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """另一写者持锁改写后，保存等待锁释放并按最新内容拒绝旧草稿。"""
    import_skill(_make_full_source(tmp_path))
    original = read_skill_file("full-pack", "SKILL.md")
    writer_ready = threading.Event()
    saver_entered = threading.Event()
    target = temp_data_root / "skills" / "full-pack" / "SKILL.md"

    def hold_lock() -> None:
        with FileLock(str(temp_data_root / "skills" / ".full-pack.edit.lock")):
            writer_ready.set()
            assert saver_entered.wait(timeout=5)
            target.write_text(original + "\n另一写者\n", encoding="utf-8")

    real_shared_lock = skill_store.shared_file_lock

    def saving_lock(lock_file: Path) -> FileLock:
        """经共享注册表取实例，并先探一次「对方还持着」，再交给被测代码去等。"""
        probe = real_shared_lock(lock_file)
        with pytest.raises(Timeout), probe.acquire(timeout=0):
            pytest.fail("竞争写者应仍持有编辑锁")
        saver_entered.set()
        return probe

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(hold_lock)
        try:
            assert writer_ready.wait(timeout=5)
            monkeypatch.setattr(skill_store, "shared_file_lock", saving_lock)
            with pytest.raises(SkillExistsError, match="其他写者"):
                save_skill_file(
                    "full-pack",
                    "SKILL.md",
                    original + "\n旧草稿\n",
                    original_content=original,
                )
        finally:
            saver_entered.set()
        worker.result(timeout=5)
    assert "另一写者" in target.read_text(encoding="utf-8")
    assert "旧草稿" not in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("description", ["old\n", "|\n  old\n", ">\n  old\n"])
def test_save_skill_description_preserves_other_metadata(
    temp_data_root: Path, tmp_path: Path, description: str
) -> None:
    """描述支持多行 YAML，修改时保留其他字段、注释和正文。"""
    original = (
        "---\n# 保留注释\nname: s\ndescription: "
        + description
        + "license: MIT\nmetadata: {owner: team}\n---\n\n正文\n"
    )
    import_skill(_make_source(tmp_path, original))

    result = save_skill_file(
        "s", "SKILL.md", original, original_content=original, description="新描述\n次行"
    )

    assert parse_skill_frontmatter(result) == ("s", "新描述\n次行")
    assert "# 保留注释" in result
    assert "license: MIT\nmetadata: {owner: team}\n---\n\n正文\n" in result


@pytest.mark.parametrize("updated", ["正文", "---\nname: other\ndescription: d\n---\n"])
def test_save_skill_file_rejects_invalid_metadata(
    temp_data_root: Path, updated: str
) -> None:
    """无效元数据与包名变更不能损坏原文件。"""
    import_skill(_FIXTURE_PACK)
    original = read_skill_file(_FIXTURE_NAME, "SKILL.md")

    with pytest.raises(SkillFormatError):
        save_skill_file(_FIXTURE_NAME, "SKILL.md", updated, original_content=original)

    assert read_skill_file(_FIXTURE_NAME, "SKILL.md") == original


def test_parse_tolerates_extra_fields() -> None:
    """外部标准宽容：frontmatter 含 license / metadata 等多余字段不报错，只取 name + description。"""
    text = "---\nname: s\ndescription: d\nlicense: MIT\nmetadata:\n  x: 1\n---\n正文\n"

    assert parse_skill_frontmatter(text) == ("s", "d")


def test_parse_tolerates_crlf_line_endings() -> None:
    """CRLF 行尾不误报（实锤 2026-09-13：WorkBuddy 生态的包是 CRLF，被误判「缺少 frontmatter」）。"""
    text = "---\r\nname: h3-prompt-writing\r\ndescription: d\r\n---\r\n正文\r\n"

    assert parse_skill_frontmatter(text) == ("h3-prompt-writing", "d")


def test_parse_tolerates_bom() -> None:
    """UTF-8 BOM 开头的文件不误报（Windows 编辑器常见形状）。"""
    text = chr(0xFEFF) + "---\nname: s\ndescription: d\n---\n正文\n"

    assert parse_skill_frontmatter(text) == ("s", "d")


def test_parse_missing_frontmatter_raises() -> None:
    """没有 frontmatter（不以 --- 开头）→ SkillFormatError（agentskills.io 要求必须有）。"""
    with pytest.raises(SkillFormatError, match="frontmatter"):
        parse_skill_frontmatter("# 只有正文，没有元数据\n")


def test_parse_unterminated_raises() -> None:
    """frontmatter 未闭合 → SkillFormatError。"""
    with pytest.raises(SkillFormatError, match="未闭合"):
        parse_skill_frontmatter("---\nname: s\ndescription: d\n没有结束符\n")


def test_parse_bad_yaml_raises() -> None:
    """frontmatter 非法 YAML → SkillFormatError。"""
    with pytest.raises(SkillFormatError, match="YAML"):
        parse_skill_frontmatter("---\na: b: c: [\n---\n正文\n")


def test_parse_not_mapping_raises() -> None:
    """frontmatter 顶层不是映射（是列表）→ SkillFormatError。"""
    with pytest.raises(SkillFormatError, match="键值映射"):
        parse_skill_frontmatter("---\n- a\n- b\n---\n正文\n")


def test_parse_missing_name_raises() -> None:
    """缺 name 字段 → SkillFormatError。"""
    with pytest.raises(SkillFormatError, match="name"):
        parse_skill_frontmatter("---\ndescription: d\n---\n正文\n")


def test_parse_missing_description_raises() -> None:
    """缺 description 字段 → SkillFormatError。"""
    with pytest.raises(SkillFormatError, match="description"):
        parse_skill_frontmatter("---\nname: s\n---\n正文\n")


def test_import_copies_whole_pack_self_contained(temp_data_root: Path) -> None:
    """导入 = 整目录复制进库（含 references/ 子目录）自包含；返回默认启用的 skill + 体积。"""
    result = import_skill(_FIXTURE_PACK)

    dest = _skills_dir(temp_data_root) / _FIXTURE_NAME
    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "detail.md").is_file()
    assert result.skill.name == _FIXTURE_NAME
    assert result.skill.enabled is True
    assert result.skill.description.startswith("示例 skill")
    assert result.total_bytes > 0


def test_import_leaves_no_temp_dir(temp_data_root: Path) -> None:
    """原子导入收尾干净：库里只有 skill 目录，没有 . 前缀的临时目录残留。"""
    import_skill(_FIXTURE_PACK)

    names = [p.name for p in _skills_dir(temp_data_root).iterdir() if p.is_dir()]

    assert names == [_FIXTURE_NAME]


@pytest.mark.parametrize("uploaded", [False, True])
def test_import_publication_holds_edit_and_state_locks(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch, uploaded: bool
) -> None:
    """两种导入入口发布新包时持有同一组锁，清理旧停用记录后默认启用。"""
    root = _skills_dir(temp_data_root)
    root.mkdir()
    (root / "_state.json").write_text(
        json.dumps({"disabled": [_FIXTURE_NAME]}), encoding="utf-8"
    )
    replace = skill_store.os.replace
    checked: list[Path] = []

    def replace_locked(source: Path, destination: Path) -> None:
        if destination == root / _FIXTURE_NAME:
            for name in (f".{_FIXTURE_NAME}.edit.lock", ".state.lock"):
                with pytest.raises(Timeout), FileLock(str(root / name), timeout=0):
                    pytest.fail("发布期间写锁没有被持有")
            checked.append(destination)
        replace(source, destination)

    monkeypatch.setattr(skill_store.os, "replace", replace_locked)

    if uploaded:
        import_skill_files({"SKILL.md": (_FIXTURE_PACK / "SKILL.md").read_bytes()})
    else:
        import_skill(_FIXTURE_PACK)

    assert checked == [root / _FIXTURE_NAME]
    assert list_skills()[0].enabled


def test_import_duplicate_name_raises_and_keeps_original(temp_data_root: Path) -> None:
    """重名不合并：再导入同名 → SkillExistsError，原包原样不动、不被覆盖。"""
    import_skill(_FIXTURE_PACK)
    before = read_skill(_FIXTURE_NAME)

    with pytest.raises(SkillExistsError, match="重名不合并"):
        import_skill(_FIXTURE_PACK)

    assert read_skill(_FIXTURE_NAME) == before


def test_import_source_not_dir_raises(tmp_path: Path, temp_data_root: Path) -> None:
    """导入源不是目录 → SkillError。"""
    a_file = tmp_path / "not-a-dir.md"
    a_file.write_text("x", encoding="utf-8")

    with pytest.raises(SkillError, match="不是目录"):
        import_skill(a_file)


def test_import_missing_skill_md_raises(tmp_path: Path, temp_data_root: Path) -> None:
    """源目录缺 SKILL.md → SkillFormatError。"""
    source = tmp_path / "empty-pack"
    source.mkdir()

    with pytest.raises(SkillFormatError, match=r"SKILL\.md"):
        import_skill(source)


def test_import_invalid_name_raises_and_copies_nothing(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """frontmatter 的 name 含路径分隔符 → SkillNameError，且不建库目录、不复制。"""
    source = _make_source(tmp_path, "---\nname: bad/name\ndescription: d\n---\n正文\n")

    with pytest.raises(SkillNameError, match="非法字符"):
        import_skill(source)

    assert not _skills_dir(temp_data_root).exists()


def test_list_empty_when_no_dir(temp_data_root: Path) -> None:
    """库目录不存在 → list_skills 返回空列表。"""
    assert list_skills() == []


def test_list_sorted_with_enabled_state(tmp_path: Path, temp_data_root: Path) -> None:
    """导入多个后按名称排序列出，默认都启用。"""
    import_skill(_FIXTURE_PACK)
    import_skill(
        _make_source(
            tmp_path,
            "---\nname: alpha-skill\ndescription: a\n---\n正文\n",
            "alpha-src",
        )
    )

    skills = list_skills()

    assert [s.name for s in skills] == ["alpha-skill", _FIXTURE_NAME]
    assert all(s.enabled for s in skills)
    # body_chars = 注入全文字符数（SKILL.md + references/ 全部文件，即实际注入量）。
    chars = {s.name: s.body_chars for s in skills}
    alpha_md = "---\nname: alpha-skill\ndescription: a\n---\n正文\n"
    expected = (
        alpha_md
        + "\n\n"
        + '<skill-file path="references/detail.md">\n补充参考\n</skill-file>'
    )
    assert chars["alpha-skill"] == len(expected)
    assert chars[_FIXTURE_NAME] > 0


def test_list_skips_state_file_and_non_skill_dirs(temp_data_root: Path) -> None:
    """list 跳过 _state.json 与不含 SKILL.md 的杂目录，只列真 skill。"""
    import_skill(_FIXTURE_PACK)
    set_enabled(_FIXTURE_NAME, False)
    (_skills_dir(temp_data_root) / "junk-dir").mkdir()

    skills = list_skills()

    assert [s.name for s in skills] == [_FIXTURE_NAME]


def test_list_skills_degrades_corrupt_package(temp_data_root: Path) -> None:
    """列表对单个损坏包宽容降级：目录名兜底进列表、description = 可读原因，其余不受影响。"""
    import_skill(_FIXTURE_PACK)
    bad_dir = _skills_dir(temp_data_root) / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\ndescription: x\n没有闭合", encoding="utf-8")

    skills = list_skills()

    assert [s.name for s in skills] == ["bad", _FIXTURE_NAME]
    broken = skills[0]
    assert broken.description.startswith("文件损坏：")
    assert "未闭合" in broken.description  # 原因可读：哪里坏
    assert broken.body_chars == 0
    assert skills[1].description.startswith("示例 skill")  # 好包不受影响


def test_list_skills_corrupt_package_heals_after_fix(temp_data_root: Path) -> None:
    """损坏包修复（SKILL.md 恢复合法内容）后，列表恢复健康形态。"""
    bad_dir = _skills_dir(temp_data_root) / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\ndescription: x\n没有闭合", encoding="utf-8")
    assert list_skills()[0].description.startswith("文件损坏：")

    (bad_dir / "SKILL.md").write_text(
        "---\nname: bad\ndescription: 已修\n---\n正文\n", encoding="utf-8"
    )

    skills = list_skills()
    assert len(skills) == 1
    assert skills[0].description == "已修"


def test_read_skill_corrupt_package_fails_loud(temp_data_root: Path) -> None:
    """单条读取对损坏包仍 fail loud（降级只在列表，读全文与打标装配拒绝带病使用）。"""
    bad_dir = _skills_dir(temp_data_root) / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\ndescription: x\n没有闭合", encoding="utf-8")

    with pytest.raises(SkillFormatError, match="未闭合"):
        read_skill("bad")


def test_read_skill_returns_full_text(temp_data_root: Path) -> None:
    """read_skill 返回 SKILL.md 全文（供打标注入）。"""
    import_skill(_FIXTURE_PACK)

    text = read_skill(_FIXTURE_NAME)

    assert text.startswith("---")
    assert "Example Caption Skill" in text


def test_read_skill_includes_references_with_markers(temp_data_root: Path) -> None:
    """注入范围 = SKILL.md + references/ 全部文件：每份带路径标记、SKILL.md 在前。"""
    import_skill(_FIXTURE_PACK)

    text = read_skill(_FIXTURE_NAME)

    assert text.startswith("---")
    assert '<skill-file path="references/detail.md">' in text
    assert text.index("Example Caption Skill") < text.index("<skill-file")
    assert "</skill-file>" in text


def test_read_skill_without_references_is_exact_skill_md(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """没有 references/ 的包：注入全文就是 SKILL.md 原文（一字不差）。"""
    source = tmp_path / "bare"
    source.mkdir()
    skill_md = "---\nname: bare-skill\ndescription: d\n---\n正文\n"
    (source / "SKILL.md").write_text(skill_md, encoding="utf-8")
    import_skill(source)

    assert read_skill("bare-skill") == skill_md


def test_read_skill_reference_subdir_sorted_by_path(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """references/ 子目录递归收录、按路径排序（多份文件的段序确定）。"""
    source = tmp_path / "multi"
    (source / "references" / "sub").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: multi-skill\ndescription: d\n---\n正文\n", encoding="utf-8"
    )
    (source / "references" / "b.md").write_text("B", encoding="utf-8")
    (source / "references" / "a.md").write_text("A", encoding="utf-8")
    (source / "references" / "sub" / "c.md").write_text("C", encoding="utf-8")
    import_skill(source)

    text = read_skill("multi-skill")

    assert text.index('path="references/a.md"') < text.index('path="references/b.md"')
    assert text.index('path="references/b.md"') < text.index(
        'path="references/sub/c.md"'
    )
    assert "\nA\n</skill-file>" in text
    assert "\nB\n</skill-file>" in text
    assert "\nC\n</skill-file>" in text


def test_read_skill_non_utf8_reference_raises(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """references/ 里混入非 UTF-8 文件 → SkillFormatError（fail loud，不静默截断注入）。"""
    source = tmp_path / "broken-ref"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: broken-ref\ndescription: d\n---\n正文\n", encoding="utf-8"
    )
    (source / "references" / "blob.bin").write_bytes(b"\xff\xfe\x00binary")
    import_skill(source)

    with pytest.raises(SkillFormatError, match="UTF-8"):
        read_skill("broken-ref")


def test_read_missing_raises(temp_data_root: Path) -> None:
    """读不存在的 skill → SkillNotFoundError。"""
    with pytest.raises(SkillNotFoundError, match="未找到"):
        read_skill("nope")


def test_disable_keeps_dir_pristine_and_persists(temp_data_root: Path) -> None:
    """停用：list 显示 enabled=False，但 skill 目录原样（状态记在库级 _state.json，不往包里塞文件）。"""
    import_skill(_FIXTURE_PACK)

    set_enabled(_FIXTURE_NAME, False)

    skill = list_skills()[0]
    dest_contents = {
        p.name for p in (_skills_dir(temp_data_root) / _FIXTURE_NAME).iterdir()
    }
    assert skill.enabled is False
    assert dest_contents == {"SKILL.md", "references"}
    assert (_skills_dir(temp_data_root) / "_state.json").is_file()


def test_reenable_flips_back(temp_data_root: Path) -> None:
    """停用后再启用 → enabled=True。"""
    import_skill(_FIXTURE_PACK)
    set_enabled(_FIXTURE_NAME, False)

    set_enabled(_FIXTURE_NAME, True)

    assert list_skills()[0].enabled is True


def test_set_enabled_missing_raises(temp_data_root: Path) -> None:
    """启用 / 停用不存在的 skill → SkillNotFoundError。"""
    with pytest.raises(SkillNotFoundError, match="未找到"):
        set_enabled("nope", False)


def test_delete_removes_dir_and_state(temp_data_root: Path) -> None:
    """删除：整目录移除 + 从启用状态清单清掉，list 不再出现。"""
    import_skill(_FIXTURE_PACK)
    set_enabled(_FIXTURE_NAME, False)

    delete_skill(_FIXTURE_NAME)

    assert not (_skills_dir(temp_data_root) / _FIXTURE_NAME).exists()
    assert list_skills() == []


def test_delete_missing_raises(temp_data_root: Path) -> None:
    """删除不存在的 skill → SkillNotFoundError。"""
    with pytest.raises(SkillNotFoundError, match="未找到"):
        delete_skill("nope")


def test_rename_moves_dir_and_rewrites_frontmatter(temp_data_root: Path) -> None:
    """改名：目录换名 + SKILL.md frontmatter 的 name 同步改写，描述与正文原样保留。"""
    import_skill(_FIXTURE_PACK)

    rename_skill(_FIXTURE_NAME, "renamed-skill")

    assert not (_skills_dir(temp_data_root) / _FIXTURE_NAME).exists()
    text = (_skills_dir(temp_data_root) / "renamed-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    name, description = parse_skill_frontmatter(text)
    assert name == "renamed-skill"
    assert description.startswith("示例 skill")
    assert "# Example Caption Skill" in text
    listing = list_skills()
    assert [skill.name for skill in listing] == ["renamed-skill"]
    assert listing[0].body_chars > 0


def test_rename_carries_disabled_state(temp_data_root: Path) -> None:
    """停用中的 skill 改名后仍是停用（启用状态清单同步换名）。"""
    import_skill(_FIXTURE_PACK)
    set_enabled(_FIXTURE_NAME, False)

    rename_skill(_FIXTURE_NAME, "renamed-skill")

    listing = list_skills()
    assert [skill.name for skill in listing] == ["renamed-skill"]
    assert listing[0].enabled is False


def test_rename_conflict_raises(temp_data_root: Path) -> None:
    """目标名称已被占用 → SkillExistsError（重名不覆盖）。"""
    import_skill(_FIXTURE_PACK)
    other_md = "---\nname: other\ndescription: x\n---\nbody"
    import_skill_files({"SKILL.md": other_md.encode()})

    with pytest.raises(SkillExistsError, match="已存在"):
        rename_skill(_FIXTURE_NAME, "other")


def test_rename_missing_raises(temp_data_root: Path) -> None:
    """改名不存在的 skill → SkillNotFoundError。"""
    with pytest.raises(SkillNotFoundError, match="未找到"):
        rename_skill("nope", "renamed")


def test_rename_invalid_name_raises(temp_data_root: Path) -> None:
    """目标名称含路径分隔符 → SkillNameError（穿越防御）。"""
    import_skill(_FIXTURE_PACK)

    with pytest.raises(SkillNameError, match="非法字符"):
        rename_skill(_FIXTURE_NAME, "../escape")


def test_read_skill_non_utf8_raises(temp_data_root: Path) -> None:
    """skill 的 SKILL.md 不是合法 UTF-8（写了非法字节）→ SkillFormatError（损坏 fail loud）。"""
    dest = _skills_dir(temp_data_root) / "broken"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(SkillFormatError, match="UTF-8"):
        read_skill("broken")


def test_list_corrupt_state_raises(temp_data_root: Path) -> None:
    """启用状态清单损坏 → list_skills 抛 SkillError（fail loud，不静默当作全启用）。"""
    import_skill(_FIXTURE_PACK)
    (_skills_dir(temp_data_root) / "_state.json").write_text(
        "{ 坏掉的 json", encoding="utf-8"
    )

    with pytest.raises(SkillError, match="损坏"):
        list_skills()


def _make_full_source(tmp_path: Path) -> Path:
    """造一个结构完整的 skill 包：SKILL.md + references/ + assets/ + scripts/ + 根级杂物。"""
    source = tmp_path / "full-src"
    (source / "references").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "scripts").mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: full-pack\ndescription: 完整结构包\n---\n正文", encoding="utf-8"
    )
    (source / "references" / "h3.md").write_text("参考资料内容", encoding="utf-8")
    (source / "assets" / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (source / "scripts" / "run.py").write_text("print('hi')", encoding="utf-8")
    (source / "README.md").write_text("readme", encoding="utf-8")
    return source


def test_list_skill_files_classifies_roles(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """清单按路径段判角色：SKILL.md / references 可预览；assets / scripts / 根级杂物不可。"""
    import_skill(_make_full_source(tmp_path))

    entries = {entry.path: entry for entry in list_skill_files("full-pack")}

    assert entries["SKILL.md"].role == "skill"
    assert entries["SKILL.md"].previewable is True
    assert entries["references/h3.md"].role == "reference"
    assert entries["references/h3.md"].previewable is True
    assert entries["assets/cover.png"].role == "asset"
    assert entries["assets/cover.png"].previewable is False
    assert entries["scripts/run.py"].role == "script"
    assert entries["scripts/run.py"].previewable is False
    assert entries["README.md"].role == "other"
    assert entries["README.md"].previewable is False
    # SKILL.md 恒排最前（界面包文件 chips 的展示顺序）。
    assert list_skill_files("full-pack")[0].path == "SKILL.md"


def test_read_skill_file_returns_text(temp_data_root: Path, tmp_path: Path) -> None:
    """可预览文件能读出原文：SKILL.md 与 references/ 下的参考文件。"""
    import_skill(_make_full_source(tmp_path))

    assert read_skill_file("full-pack", "SKILL.md").endswith("正文")
    assert read_skill_file("full-pack", "references/h3.md") == "参考资料内容"


def test_read_skill_file_rejects_non_previewable(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """assets / scripts / 根级杂物一律拒绝预览（不开放内容的角色）。"""
    import_skill(_make_full_source(tmp_path))

    for path in ("assets/cover.png", "scripts/run.py", "README.md"):
        with pytest.raises(SkillFileNotPreviewableError):
            read_skill_file("full-pack", path)


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "   ",
        "/etc/passwd",
        "C:/evil.md",
        "references\\..\\x.md",
        "../escape.md",
        "a/../../escape.md",
        "./../escape.md",
    ],
)
def test_read_skill_file_rejects_unsafe_paths(
    temp_data_root: Path, tmp_path: Path, bad_path: str
) -> None:
    """路径穿越与可疑形态（绝对路径 / 反斜杠 / .. 上跳 / 空）一律 SkillFilePathError。"""
    import_skill(_make_full_source(tmp_path))
    escape_target = temp_data_root / "escape.md"
    escape_target.write_text("不该被读到", encoding="utf-8")

    with pytest.raises(SkillFilePathError):
        read_skill_file("full-pack", bad_path)

    # 包外文件原样无损。
    assert escape_target.read_text(encoding="utf-8") == "不该被读到"


def test_read_skill_file_missing_file_is_not_found(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """skill 存在但包内无此文件 → SkillNotFoundError（接口层映射 404）。"""
    import_skill(_make_full_source(tmp_path))

    with pytest.raises(SkillNotFoundError, match="不存在文件"):
        read_skill_file("full-pack", "references/nope.md")


def test_read_skill_file_missing_skill_is_not_found(temp_data_root: Path) -> None:
    """skill 不存在 → SkillNotFoundError。"""
    with pytest.raises(SkillNotFoundError):
        read_skill_file("ghost", "SKILL.md")


def test_read_skill_file_binary_reference_rejected(
    temp_data_root: Path, tmp_path: Path
) -> None:
    """references/ 下的二进制内容（非法 UTF-8）→ SkillFileNotPreviewableError，不吐乱码。"""
    source = _make_full_source(tmp_path)
    (source / "references" / "blob.bin").write_bytes(b"\xff\xfe\x00\x81")
    import_skill(source)

    with pytest.raises(SkillFileNotPreviewableError, match="UTF-8"):
        read_skill_file("full-pack", "references/blob.bin")
