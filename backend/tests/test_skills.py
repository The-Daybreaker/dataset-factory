"""单元测试：skills Skill 库（frontmatter 解析、导入复制自包含、启用/停用、读全文、格式校验、重名不合并）。

全部离线、用 temp_data_root fixture 隔离数据根；导入源用 tests/fixtures/skill-pack（手写标准包，兼作 golden）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    list_skill_files,
    list_skills,
    parse_skill_frontmatter,
    read_skill,
    read_skill_file,
    set_enabled,
)

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


def test_parse_tolerates_extra_fields() -> None:
    """外部标准宽容：frontmatter 含 license / metadata 等多余字段不报错，只取 name + description。"""
    text = "---\nname: s\ndescription: d\nlicense: MIT\nmetadata:\n  x: 1\n---\n正文\n"

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

    names = [p.name for p in _skills_dir(temp_data_root).iterdir()]

    assert names == [_FIXTURE_NAME]


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


def test_list_skips_state_file_and_non_skill_dirs(temp_data_root: Path) -> None:
    """list 跳过 _state.json 与不含 SKILL.md 的杂目录，只列真 skill。"""
    import_skill(_FIXTURE_PACK)
    set_enabled(_FIXTURE_NAME, False)
    (_skills_dir(temp_data_root) / "junk-dir").mkdir()

    skills = list_skills()

    assert [s.name for s in skills] == [_FIXTURE_NAME]


def test_read_skill_returns_full_text(temp_data_root: Path) -> None:
    """read_skill 返回 SKILL.md 全文（供打标注入）。"""
    import_skill(_FIXTURE_PACK)

    text = read_skill(_FIXTURE_NAME)

    assert text.startswith("---")
    assert "Example Caption Skill" in text


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
