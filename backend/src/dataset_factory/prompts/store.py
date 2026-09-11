"""提示词库的 CRUD 与滚动备份（数据域）——读写只收敛在本模块。

条目 = 平铺 `prompts/<名称>.md`（文件名即名称）；保存时若已存在，先把旧版**复制**进
`_history/`（不是移动——保证当前条目任何时刻都在、崩溃安全），按时间戳命名、并把该条目的
历史裁到最近 N 版，再原子写新版；单条序列化后卡 32 KiB 字节护栏；缺失 / 损坏 / 超限 / 名称
非法一律 fail loud。原子写与数据根复用共享的 `_fs`；本模块禁 import 入口层与 llm（分层契约守）。
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from .._fs import atomic_write_text, data_root
from .errors import (
    PromptError,
    PromptNameError,
    PromptNotFoundError,
    PromptParseError,
    PromptTooLargeError,
)
from .model import Prompt, dump_prompt, parse_prompt

_PROMPTS_DIRNAME = "prompts"
_HISTORY_DIRNAME = "_history"
_SUFFIX = ".md"
_MAX_ENTRY_BYTES = (
    32 * 1024
)  # 单条提示词字节上限，对齐 Codex project_doc_max_bytes；可调。
_HISTORY_KEEP = 20  # 每条提示词在 _history/ 保留的最近版本数；可调。
_STAMP_FORMAT = (
    "%Y%m%d-%H%M%S-%f"  # 定宽、字典序即时间序；微秒精度让同秒多次保存不撞名。
)

# 名称含路径分隔符、控制字符或 Windows 不允许的字符即非法：名称只能是跨平台安全的单段文件名。
_FORBIDDEN_NAME_CHARS = re.compile(r'[/\\<>:"|?*\x00-\x1f\x7f]')


def _prompts_dir() -> Path:
    """提示词库根目录 = 数据根下的 prompts/。"""
    return data_root() / _PROMPTS_DIRNAME


def _entry_path(name: str) -> Path:
    """某名称对应的条目文件路径 prompts/<name>.md。"""
    return _prompts_dir() / f"{name}{_SUFFIX}"


def _validate_name(name: str) -> None:
    """校验提示词名称；不合法即 PromptNameError。

    挡住路径穿越（路径分隔符）、跨平台非法字符（Windows 的 < > : " | ? *、控制字符）、
    点号（留给扩展名与历史版本时间戳分隔，禁掉即消除历史文件名匹配的歧义）与保留名 _history。

    Args:
        name: 待校验的名称（将作为文件名 <name>.md）。

    Raises:
        PromptNameError: 名称为空 / 首尾含空白 / 含非法字符 / 含点号 / 是保留名 _history。
    """
    if not name or not name.strip():
        raise PromptNameError("提示词名称不能为空。")
    if name != name.strip():
        raise PromptNameError(f"提示词名称 {name!r} 首尾含空白；请去掉后再试。")
    if _FORBIDDEN_NAME_CHARS.search(name):
        raise PromptNameError(
            f"提示词名称 {name!r} 含非法字符（路径分隔符、控制字符或 Windows 不允许的 "
            '< > : " | ? *）；名称只能是跨平台安全的单段文件名。'
        )
    if "." in name:
        raise PromptNameError(
            f"提示词名称 {name!r} 不能含点号（.）；点号留给扩展名与历史版本时间戳分隔。"
        )
    if name == _HISTORY_DIRNAME:
        raise PromptNameError(
            f"提示词名称 {name!r} 是保留名（_history 用于滚动备份）；请换一个。"
        )


def _read_entry(path: Path) -> Prompt:
    """读取并解析单个条目文件；不可读 / 损坏 fail loud。

    Args:
        path: 条目文件路径（<name>.md）。

    Returns:
        解析出的 Prompt（name 取自文件名）。

    Raises:
        PromptError: 文件不可读（底层 OSError）。
        PromptParseError: 文件不是合法 UTF-8，或 frontmatter 损坏 / 非法。
    """
    name = path.name[: -len(_SUFFIX)]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PromptParseError(
            f"提示词 {name!r} 不是合法 UTF-8 文本；文件可能已损坏。"
        ) from exc
    except OSError as exc:
        raise PromptError(f"无法读取提示词 {path}：{exc.strerror or exc}") from exc
    return parse_prompt(name, text)


def list_prompts() -> list[Prompt]:
    """列出全部提示词条目（按名称排序）。

    只认 prompts/ 下的普通 `*.md` 文件：跳过 _history/ 子目录、原子写留下的 `.` 前缀临时
    文件与任何非普通文件。库目录不存在时返回空列表（还没有任何条目，不算错）。

    Returns:
        提示词列表，按名称字典序。

    Raises:
        PromptError: 某个条目不可读。
        PromptParseError: 某个条目损坏。
    """
    directory = _prompts_dir()
    if not directory.is_dir():
        return []
    return [
        _read_entry(path)
        for path in sorted(directory.glob(f"*{_SUFFIX}"))
        if path.is_file() and not path.name.startswith(".")
    ]


def read_prompt(name: str) -> Prompt:
    """按名称读取一条提示词。

    Args:
        name: 条目名称（= 文件名去掉 .md）。

    Returns:
        对应的 Prompt。

    Raises:
        PromptNameError: 名称非法。
        PromptNotFoundError: 没有这个名字的条目。
        PromptError: 文件不可读。
        PromptParseError: 文件损坏。
    """
    _validate_name(name)
    path = _entry_path(name)
    if not path.is_file():
        raise PromptNotFoundError(
            f"未找到提示词 {name!r}；用 list_prompts 查看现有条目。"
        )
    return _read_entry(path)


def save_prompt(prompt: Prompt) -> None:
    """保存（新建或覆盖）一条提示词；覆盖前把旧版复制进 _history/ 滚动备份。

    先校验名称、卡 32 KiB 字节上限（超限即拒，不落盘、不建目录）；库目录不存在则创建；若
    目标已存在，先把它复制进 `_history/<name>.<时间戳>.md` 并把该条目历史裁到最近 N 版，再
    原子写新版——原子写保证条目要么是旧版要么是新版，绝不会是写了一半的损坏文件。

    Args:
        prompt: 要保存的提示词（name / description / body）。

    Raises:
        PromptNameError: 名称非法。
        PromptTooLargeError: 序列化后超过 32 KiB。
        PromptError: 目录 / 备份 / 写入等底层失败，或内容含 UTF-8 无法编码的字符。
    """
    _validate_name(prompt.name)
    text = dump_prompt(prompt)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PromptError(
            f"提示词 {prompt.name!r} 含 UTF-8 无法编码的字符（{exc.reason}）；请检查内容。"
        ) from exc
    if size > _MAX_ENTRY_BYTES:
        raise PromptTooLargeError(
            f"提示词 {prompt.name!r} 序列化后有 {size} 字节，超过上限 "
            f"{_MAX_ENTRY_BYTES} 字节（32 KiB）；请精简正文。"
        )
    directory = _prompts_dir()
    path = directory / f"{prompt.name}{_SUFFIX}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PromptError(
            f"无法在 {directory} 准备写入：{exc.strerror or exc}"
        ) from exc
    if path.is_file():
        _backup_to_history(directory, prompt.name, path)
    try:
        atomic_write_text(path, text)
    except OSError as exc:
        raise PromptError(f"无法写入提示词 {path}：{exc.strerror or exc}") from exc


def delete_prompt(name: str) -> None:
    """删除一条提示词及其全部历史备份。

    Args:
        name: 条目名称。

    Raises:
        PromptNameError: 名称非法。
        PromptNotFoundError: 没有这个名字的条目。
        PromptError: 删除失败。
    """
    _validate_name(name)
    directory = _prompts_dir()
    path = directory / f"{name}{_SUFFIX}"
    if not path.is_file():
        raise PromptNotFoundError(f"未找到提示词 {name!r}；无需删除。")
    try:
        path.unlink()
    except OSError as exc:
        raise PromptError(f"无法删除提示词 {path}：{exc.strerror or exc}") from exc
    history_dir = directory / _HISTORY_DIRNAME
    if history_dir.is_dir():
        for old in _history_versions(history_dir, name):
            old.unlink(missing_ok=True)


def _backup_to_history(directory: Path, name: str, current: Path) -> None:
    """把当前版本复制进 _history/<name>.<时间戳>.md，再把该条目历史裁到最近 N 版。

    用复制而非移动：当前条目在原子写新版之前始终存在，任何一步崩溃都不会让条目消失。
    """
    history_dir = directory / _HISTORY_DIRNAME
    stamp = datetime.now().strftime(_STAMP_FORMAT)
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, _history_target(history_dir, name, stamp))
    except OSError as exc:
        raise PromptError(
            f"无法把旧版 {current} 备份进 {history_dir}：{exc.strerror or exc}"
        ) from exc
    _evict_old_history(history_dir, name)


def _history_target(history_dir: Path, name: str, stamp: str) -> Path:
    """给历史备份取一个不撞名的路径：<name>.<时间戳>.md，撞了就加 -<序号>。

    Windows 系统时钟粒度较粗（约 15ms），同一秒内多次保存可能得到相同时间戳；加序号
    保证不覆盖已有备份（宁可多留一版，也不丢一版）。
    """
    candidate = history_dir / f"{name}.{stamp}{_SUFFIX}"
    seq = 1
    while candidate.exists():
        candidate = history_dir / f"{name}.{stamp}-{seq}{_SUFFIX}"
        seq += 1
    return candidate


def _history_versions(history_dir: Path, name: str) -> list[Path]:
    """某条目在 _history/ 里的全部版本，按时间戳（文件名）从旧到新排序。

    历史文件名形如 `<name>.<时间戳>.md`；名称已禁点号，故 `<name>.` 前缀能精确锁定该条目、
    不会误配到别的条目（如 name=foo 不会匹配 foobar 的历史）。
    """
    prefix = f"{name}."
    return sorted(
        path
        for path in history_dir.iterdir()
        if path.is_file()
        and path.name.startswith(prefix)
        and path.name.endswith(_SUFFIX)
    )


def _evict_old_history(history_dir: Path, name: str) -> None:
    """把某条目的历史裁到最近 N 版：删掉最旧的、超出保留数的版本。"""
    versions = _history_versions(history_dir, name)
    overflow = len(versions) - _HISTORY_KEEP
    for stale in versions[: max(0, overflow)]:
        stale.unlink(missing_ok=True)
