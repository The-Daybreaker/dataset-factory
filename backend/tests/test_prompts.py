"""单元测试：prompts 提示词库（md/frontmatter 解析、CRUD、_history 滚动备份、32 KiB 护栏、原子写崩溃安全）。

全部离线、用 temp_data_root fixture 把数据根隔离到临时目录，绝不碰真实 ~/.dataset_factory。
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from dataset_factory.prompts import (
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
    save_prompt,
)


def _prompts_dir(root: Path) -> Path:
    return root / "prompts"


def _write_raw(root: Path, name: str, text: str) -> Path:
    directory = _prompts_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_dump_parse_round_trips() -> None:
    """序列化再解析，Prompt 原样还原（读写闭环）。"""
    original = Prompt(name="cap", description="生成中文描述", body="请描述这张图。")

    parsed = parse_prompt("cap", dump_prompt(original))

    assert parsed == original


def test_dump_parse_round_trips_tricky_description() -> None:
    """description 含 YAML 特殊字符（冒号、井号）也能安全 round-trip（safe_dump 自动加引号）。"""
    original = Prompt(name="x", description="键: 值 # 注释", body="正文")

    parsed = parse_prompt("x", dump_prompt(original))

    assert parsed == original


def test_parse_without_frontmatter_is_lenient() -> None:
    """没有 frontmatter（不以 --- 开头）→ description 视为空、全文即正文（宽容手建条目）。"""
    parsed = parse_prompt("plain", "就是正文，没有元数据")

    assert parsed == Prompt(name="plain", description="", body="就是正文，没有元数据")


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
    """frontmatter 含 description 以外的字段 → PromptParseError（挡住手误拼错字段名）。"""
    with pytest.raises(PromptParseError, match="未知字段"):
        parse_prompt("bad", "---\ndescriptions: 拼错了\n---\n正文")


def test_parse_non_string_description_raises() -> None:
    """description 不是字符串（YAML 把 123 解析成整数）→ PromptParseError。"""
    with pytest.raises(PromptParseError, match="description 应是字符串"):
        parse_prompt("bad", "---\ndescription: 123\n---\n正文")


def test_parse_golden_fixture(temp_data_root: Path) -> None:
    """契约测试：read_prompt 正确解析一份手写标准 md（把磁盘格式钉成独立样例，防读写两侧一起漂移）。"""
    golden = Path(__file__).parent / "fixtures" / "prompt.md"
    directory = _prompts_dir(temp_data_root)
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(golden, directory / "golden.md")

    parsed = read_prompt("golden")

    assert (
        parsed.description == "为单张图片生成简洁准确的中文描述，适合 LoRA 训练打标。"
    )
    assert (
        parsed.body.strip()
        == "请用一句话描述图片的主体、动作与场景，只讲画面里确实有的内容，不要臆造。"
    )


def test_save_then_read_round_trips(temp_data_root: Path) -> None:
    """保存后按名读回，三字段一致（读写闭环、库目录自动创建）。"""
    save_prompt(Prompt(name="cap", description="描述", body="正文内容"))

    parsed = read_prompt("cap")

    assert parsed == Prompt(name="cap", description="描述", body="正文内容")


def test_list_prompts_empty_when_no_dir(temp_data_root: Path) -> None:
    """库目录还不存在 → list_prompts 返回空列表（尚无条目不算错）。"""
    assert list_prompts() == []


def test_list_prompts_sorted_and_skips_non_entries(temp_data_root: Path) -> None:
    """list_prompts 按名称排序，且只认 prompts/ 下的普通 *.md（跳过 _history/ 与 . 前缀文件）。"""
    save_prompt(Prompt(name="zeta", description="", body="z"))
    save_prompt(Prompt(name="alpha", description="", body="a"))
    save_prompt(Prompt(name="alpha", description="", body="a2"))
    directory = _prompts_dir(temp_data_root)
    (directory / ".hidden.md").write_text("不该出现", encoding="utf-8")

    names = [prompt.name for prompt in list_prompts()]

    assert names == ["alpha", "zeta"]
    assert (directory / "_history").is_dir()


def test_read_missing_raises(temp_data_root: Path) -> None:
    """读不存在的条目 → PromptNotFoundError。"""
    with pytest.raises(PromptNotFoundError, match="未找到"):
        read_prompt("nope")


def test_save_overwrite_backs_up_old_version(temp_data_root: Path) -> None:
    """覆盖保存：旧版被复制进 _history/，当前条目是新版。"""
    save_prompt(Prompt(name="cap", description="", body="旧正文"))

    save_prompt(Prompt(name="cap", description="", body="新正文"))

    assert read_prompt("cap").body == "新正文"
    history = list((_prompts_dir(temp_data_root) / "_history").glob("cap.*.md"))
    assert len(history) == 1
    assert "旧正文" in history[0].read_text(encoding="utf-8")


def test_save_oversized_raises_and_writes_nothing(temp_data_root: Path) -> None:
    """超过 32 KiB → PromptTooLargeError，且不落盘、不建条目。"""
    huge = "x" * (32 * 1024 + 1)

    with pytest.raises(PromptTooLargeError, match="超过上限"):
        save_prompt(Prompt(name="big", description="", body=huge))

    assert not (_prompts_dir(temp_data_root) / "big.md").exists()


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "a/b",
        "a\\b",
        "..",
        ".hidden",
        "with.dot",
        "_history",
        "bad\x00name",
        "lead ",
        "trail\t",
    ],
)
def test_save_rejects_invalid_name(temp_data_root: Path, bad_name: str) -> None:
    """非法名称（空 / 空白 / 路径分隔符 / 点号 / 保留名 / 控制字符 / 首尾空白）→ PromptNameError。"""
    with pytest.raises(PromptNameError, match="名称"):
        save_prompt(Prompt(name=bad_name, description="", body="x"))


def test_delete_removes_entry_and_history(temp_data_root: Path) -> None:
    """删除条目：连同它的 _history/ 备份一起清掉，不留孤儿。"""
    save_prompt(Prompt(name="cap", description="", body="v1"))
    save_prompt(Prompt(name="cap", description="", body="v2"))
    history_dir = _prompts_dir(temp_data_root) / "_history"
    assert history_dir.is_dir()

    delete_prompt("cap")

    assert not (_prompts_dir(temp_data_root) / "cap.md").exists()
    assert list(history_dir.glob("cap.*.md")) == []


def test_delete_missing_raises(temp_data_root: Path) -> None:
    """删除不存在的条目 → PromptNotFoundError。"""
    with pytest.raises(PromptNotFoundError, match="未找到"):
        delete_prompt("nope")


def test_history_evicts_beyond_keep_limit(temp_data_root: Path) -> None:
    """反复覆盖保存：某条目的历史被裁到最近 N=20 版，不会无限增长。"""
    for i in range(25):
        save_prompt(Prompt(name="cap", description="", body=f"v{i}"))

    history = list((_prompts_dir(temp_data_root) / "_history").glob("cap.*.md"))

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
    for i in range(22):
        save_prompt(Prompt(name="cap", description="", body=f"v{i}"))

    history_dir = _prompts_dir(temp_data_root) / "_history"
    remaining = sorted(p.name for p in history_dir.glob("cap.*.md"))

    assert len(remaining) == 20
    assert "cap.20260911-120000-123456.md" not in remaining
    assert "cap.20260911-120000-123456-1.md" in remaining


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
    save_prompt(Prompt(name="cap", description="", body="旧正文"))

    def _boom(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(PromptError, match="无法写入"):
        save_prompt(Prompt(name="cap", description="", body="新正文"))

    assert read_prompt("cap").body == "旧正文"
    assert [
        p for p in _prompts_dir(temp_data_root).iterdir() if p.suffix == ".tmp"
    ] == []


def test_save_unencodable_body_raises(temp_data_root: Path) -> None:
    """正文含 UTF-8 无法编码的字符（孤立代理项）→ PromptError，不落盘。"""
    with pytest.raises(PromptError, match="无法编码"):
        save_prompt(Prompt(name="bad", description="", body="x" + chr(0xD800)))

    assert not (_prompts_dir(temp_data_root) / "bad.md").exists()
