"""Skill 库的导入 / 启用停用 / 读取（数据域）——读写只收敛在本模块。

Skill 包按 agentskills.io 开放标准（`<name>/SKILL.md` + references/ 等）导入 = 整目录复制进
`skills/<name>/`（自包含，原包可删）；启用 / 停用状态记在独立清单 `skills/_state.json`（不写
进 skill 目录，保持包「原样」）；重名不合并（导入撞名即 fail loud、不覆盖旧包）；停用不删除。
目录级导入用「复制到临时名 + os.replace 改名」做到崩溃不留半个包；数据根 + 原子写复用共享
`_fs`；本模块禁 import 入口层与 llm（分层契约守）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import cast

from .._fs import atomic_write_text, data_root
from .errors import (
    SkillError,
    SkillExistsError,
    SkillFileNotPreviewableError,
    SkillFilePathError,
    SkillFormatError,
    SkillNameError,
    SkillNotFoundError,
    SkillSourceError,
)
from .model import (
    Skill,
    SkillFileEntry,
    SkillImport,
    parse_skill_frontmatter,
)

_SKILLS_DIRNAME = "skills"
_SKILL_MD = "SKILL.md"
_STATE_FILENAME = "_state.json"

# 名称含路径分隔符、控制字符或 Windows 不允许的字符即非法：名称只能是单段安全目录名。
_FORBIDDEN_NAME_CHARS = re.compile(r'[/\\<>:"|?*\x00-\x1f\x7f]')


def _skills_dir() -> Path:
    """Skill 库根目录 = 数据根下的 skills/。"""
    return data_root() / _SKILLS_DIRNAME


def _skill_dir(name: str) -> Path:
    """某名称对应的 skill 目录 skills/<name>/。"""
    return _skills_dir() / name


def _validate_name(name: str) -> None:
    """校验 skill 名称；不合法即 SkillNameError。

    挡住路径穿越（路径分隔符）、跨平台非法字符、控制字符，以及以点或下划线开头的名字
    （这两个前缀保留给工具内部文件，如临时目录与 _state.json 清单）。

    Args:
        name: 待校验的名称（将作为目录名 skills/<name>）。

    Raises:
        SkillNameError: 名称为空 / 首尾含空白 / 含非法字符 / 以点或下划线开头。
    """
    if not name or not name.strip():
        raise SkillNameError("skill 名称不能为空。")
    if name != name.strip():
        raise SkillNameError(
            f"skill 名称 {name!r} 首尾含空白；请检查 SKILL.md 的 name 字段。"
        )
    if _FORBIDDEN_NAME_CHARS.search(name):
        raise SkillNameError(
            f"skill 名称 {name!r} 含非法字符（路径分隔符、控制字符或 Windows 不允许的 "
            '< > : " | ? *）；名称只能是单段安全目录名。'
        )
    if name.startswith((".", "_")):
        raise SkillNameError(
            f"skill 名称 {name!r} 不能以点或下划线开头（这两个前缀保留给工具内部文件）。"
        )


def _read_text(path: Path) -> str:
    """读文本文件，把底层错误翻译成 skill 域异常。

    Args:
        path: 目标文件路径。

    Returns:
        文件的 UTF-8 文本内容。

    Raises:
        SkillFormatError: 文件不是合法 UTF-8。
        SkillError: 文件不可读（底层 OSError）。
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillFormatError(f"{path} 不是合法 UTF-8 文本；文件可能已损坏。") from exc
    except OSError as exc:
        raise SkillError(f"无法读取 {path}：{exc.strerror or exc}") from exc


def _dir_bytes(directory: Path) -> int:
    """目录树里所有文件的字节总和（供导入体积提示）。"""
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def _atomic_copytree(source: Path, dest: Path) -> None:
    """把 source 目录树原子复制到 dest：先复制到同目录临时名、再 os.replace 改名。

    dest 必须尚不存在（调用方已查重）。改名成功后临时目录已不存在（finally 的 rmtree 即
    no-op）；任何失败路径都清掉临时目录，崩溃也不留下半个包冒充成品。

    Args:
        source: 源 skill 目录。
        dest: 库内目标目录（skills/<name>）。

    Raises:
        SkillError: 复制或改名失败（底层 OSError，含 shutil.Error）。
    """
    tmp = dest.parent / f".{dest.name}.tmp{os.urandom(4).hex()}"
    try:
        shutil.copytree(source, tmp)
        os.replace(tmp, dest)
    except OSError as exc:
        raise SkillError(f"无法把 {source} 复制进库：{exc.strerror or exc}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_disabled() -> set[str]:
    """读启用状态清单里的「停用」集合；清单不存在 = 没有停用的（默认全启用）。

    Returns:
        被停用的 skill 名称集合。

    Raises:
        SkillError: 清单读取失败 / 损坏 / 结构非法。
    """
    state_path = _skills_dir() / _STATE_FILENAME
    if not state_path.is_file():
        return set()
    try:
        parsed: object = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError(f"启用状态清单 {state_path} 读取失败或损坏：{exc}") from exc
    if not isinstance(parsed, dict):
        raise SkillError(f"启用状态清单 {state_path} 顶层应是 JSON 对象。")
    data = cast(dict[str, object], parsed)
    disabled = data.get("disabled", [])
    if not isinstance(disabled, list):
        raise SkillError(f"启用状态清单 {state_path} 的 disabled 字段应是数组。")
    return {str(item) for item in cast(list[object], disabled)}


def _write_disabled(disabled: set[str]) -> None:
    """原子写启用状态清单（只记停用的名字，启用的不记——默认即启用）。

    Args:
        disabled: 被停用的 skill 名称集合。

    Raises:
        SkillError: 目录 / 写入失败，或内容含 UTF-8 无法编码的字符。
    """
    skills_root = _skills_dir()
    payload = (
        json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2) + "\n"
    )
    try:
        skills_root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(skills_root / _STATE_FILENAME, payload)
    except OSError as exc:
        raise SkillError(
            f"无法写入启用状态清单 {skills_root / _STATE_FILENAME}：{exc.strerror or exc}"
        ) from exc
    except UnicodeEncodeError as exc:
        raise SkillError(
            f"无法写入启用状态清单：内容含 UTF-8 无法编码的字符（{exc.reason}）"
        ) from exc


def import_skill(source: Path) -> SkillImport:
    """从一个 agentskills.io skill 目录导入：校验 → 整目录复制进库（自包含）→ 默认启用。

    源必须是一个含 SKILL.md 的目录；skill 名称取自 SKILL.md frontmatter 的 name，据此存到
    `skills/<name>/`。重名不合并——库里已有同名 skill 即 SkillExistsError，绝不覆盖旧包。
    复制走「临时名 + os.replace」的目录级原子导入，崩溃不留半个包。

    Args:
        source: 源 skill 目录（含 SKILL.md，可含 references/ 等子目录）。

    Returns:
        SkillImport：导入的 skill（enabled=True）+ 整包字节数（供体积提示）。

    Raises:
        SkillSourceError: 源不是目录（路径填错）。
        SkillFormatError: 源缺 SKILL.md，或 SKILL.md frontmatter 非法。
        SkillNameError: frontmatter 的 name 不是合法目录名。
        SkillExistsError: 库里已有同名 skill。
        SkillError: 库目录 / 复制准备失败。
    """
    if not source.is_dir():
        raise SkillSourceError(
            f"导入源 {source} 不是目录；请指向一个包含 {_SKILL_MD} 的 skill 目录。"
        )
    skill_md = source / _SKILL_MD
    if not skill_md.is_file():
        raise SkillFormatError(
            f"导入源 {source} 缺少 {_SKILL_MD}；不是合法的 agentskills.io skill 包。"
        )
    name, description = parse_skill_frontmatter(_read_text(skill_md))
    _validate_name(name)
    skills_root = _skills_dir()
    dest = skills_root / name
    if dest.exists():
        raise SkillExistsError(
            f"skill {name!r} 已在库中；重名不合并——请先删除旧的，或改 SKILL.md 的 name 再导入。"
        )
    try:
        skills_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillError(
            f"无法在 {skills_root} 准备导入：{exc.strerror or exc}"
        ) from exc
    _atomic_copytree(source, dest)
    return SkillImport(
        skill=Skill(name=name, description=description, enabled=True),
        total_bytes=_dir_bytes(dest),
    )


def list_skills() -> list[Skill]:
    """列出库里全部 skill（按名称排序），带各自的库级启用状态。

    只认 skills/ 下含 SKILL.md 的普通子目录：跳过 `_state.json` 等文件、`.`/`_` 前缀的内部
    目录、以及不含 SKILL.md 的杂目录。含 SKILL.md 但 frontmatter 损坏的 → fail loud。

    Returns:
        skill 列表，按名称字典序；库目录不存在时返回空列表。

    Raises:
        SkillFormatError: 某个 skill 的 SKILL.md 损坏。
        SkillError: 启用状态清单损坏，或某个 SKILL.md 不可读。
    """
    directory = _skills_dir()
    if not directory.is_dir():
        return []
    disabled = _read_disabled()
    skills: list[Skill] = []
    for entry in directory.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if not (entry / _SKILL_MD).is_file():
            continue
        name, description = parse_skill_frontmatter(_read_text(entry / _SKILL_MD))
        skills.append(
            Skill(name=name, description=description, enabled=name not in disabled)
        )
    return sorted(skills, key=lambda skill: skill.name)


def read_skill(name: str) -> str:
    """读某个 skill 的 SKILL.md 全文（供打标时按 <skill> 标记包裹注入）。

    Args:
        name: skill 名称。

    Returns:
        SKILL.md 全文。

    Raises:
        SkillNameError: 名称非法。
        SkillNotFoundError: 没有这个名字的 skill。
        SkillFormatError: SKILL.md 不是合法 UTF-8。
        SkillError: SKILL.md 不可读。
    """
    _validate_name(name)
    skill_md = _skill_dir(name) / _SKILL_MD
    if not skill_md.is_file():
        raise SkillNotFoundError(
            f"未找到 skill {name!r}；用 list_skills 查看已导入的。"
        )
    return _read_text(skill_md)


def set_enabled(name: str, enabled: bool) -> None:
    """启用 / 停用某个 skill（停用不删除：只改 _state.json 清单，skill 目录原样保留）。

    Args:
        name: skill 名称。
        enabled: True 启用、False 停用。

    Raises:
        SkillNameError: 名称非法。
        SkillNotFoundError: 没有这个名字的 skill。
        SkillError: 启用状态清单读写失败。
    """
    _validate_name(name)
    if not (_skill_dir(name) / _SKILL_MD).is_file():
        raise SkillNotFoundError(
            f"未找到 skill {name!r}；用 list_skills 查看已导入的。"
        )
    disabled = _read_disabled()
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    _write_disabled(disabled)


def delete_skill(name: str) -> None:
    """删除某个 skill：移除整目录 + 从启用状态清单里清掉（显式删除；只想收起请改用停用）。

    Args:
        name: skill 名称。

    Raises:
        SkillNameError: 名称非法。
        SkillNotFoundError: 没有这个名字的 skill。
        SkillError: 删除失败，或启用状态清单读写失败。
    """
    _validate_name(name)
    target = _skill_dir(name)
    if not (target / _SKILL_MD).is_file():
        raise SkillNotFoundError(f"未找到 skill {name!r}；无需删除。")
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise SkillError(f"无法删除 skill {target}：{exc.strerror or exc}") from exc
    disabled = _read_disabled()
    if name in disabled:
        disabled.discard(name)
        _write_disabled(disabled)


def _require_skill_dir(name: str) -> Path:
    """校验名称并要求 skill 存在，返回其目录（包内容预览共用的小闸门）。

    Raises:
        SkillNameError: 名称非法。
        SkillNotFoundError: 没有这个名字的 skill。
    """
    _validate_name(name)
    directory = _skill_dir(name)
    if not (directory / _SKILL_MD).is_file():
        raise SkillNotFoundError(
            f"未找到 skill {name!r}；用 list_skills 查看已导入的。"
        )
    return directory


def _classify_file(parts: tuple[str, ...]) -> SkillFileEntry:
    """按包内相对路径段判定文件角色与可预览性。

    注入范围成文（design「技能双栏模式」）：仅 SKILL.md 注入请求；references/ 供查阅、
    不自动注入；assets / scripts 与其他文件不参与注入。可预览 = SKILL.md 与 references/
    下文件——预览服务「导入 → 核对 → 启用」闭环，不开放整包任意读。

    Args:
        parts: 包内相对路径段（已过安全解析）。

    Returns:
        带角色与可预览标注的文件条目。
    """
    posix = "/".join(parts)
    top = parts[0] if parts else ""
    if parts == (_SKILL_MD,):
        return SkillFileEntry(path=posix, role="skill", previewable=True)
    if top == "references":
        return SkillFileEntry(path=posix, role="reference", previewable=True)
    if top == "assets":
        return SkillFileEntry(path=posix, role="asset", previewable=False)
    if top == "scripts":
        return SkillFileEntry(path=posix, role="script", previewable=False)
    return SkillFileEntry(path=posix, role="other", previewable=False)


def _safe_package_parts(raw: str) -> tuple[str, ...]:
    """把请求提供的包内相对路径解析成安全路径段；任何可疑形态直接拒绝。

    拒绝：空 / 纯空白、绝对路径（POSIX 形态或 Windows 盘符形态）、反斜杠（Windows
    分隔符不作为包内路径语法）、``..`` 段（目录上跳）。包内真实文件名来自导入时的
    文件系统，不会呈现这些形态——它们只可能出自构造请求，fail-fast。

    Args:
        raw: 请求提供的包内路径原文。

    Returns:
        逐段校验后的路径段元组（每段都是不含分隔符的单个名字）。

    Raises:
        SkillFilePathError: 路径为空 / 含反斜杠 / 是绝对路径 / 含 ``..`` 段。
    """
    text = raw.strip()
    if not text:
        raise SkillFilePathError(
            "包内文件路径为空；请提供 SKILL.md 或 references/ 下的相对路径。"
        )
    if "\\" in text:
        raise SkillFilePathError(f"包内路径 {raw!r} 含反斜杠；请用正斜杠分隔。")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise SkillFilePathError(f"包内路径 {raw!r} 应是包内相对路径；拒绝绝对路径。")
    parts = PurePosixPath(text).parts
    if any(part == ".." for part in parts):
        raise SkillFilePathError(f"包内路径 {raw!r} 不合法；不允许目录上跳（..）。")
    return parts


def list_skill_files(name: str) -> list[SkillFileEntry]:
    """列出技能包内全部文件（角色标注），SKILL.md 恒排最前、其余按路径排序。

    Args:
        name: skill 名称。

    Returns:
        文件条目列表（含 assets / scripts——它们被列出但不开放内容预览，供界面灰显）。

    Raises:
        SkillNameError: 名称非法。
        SkillNotFoundError: 没有这个名字的 skill。
    """
    directory = _require_skill_dir(name)
    entries = [
        _classify_file(path.relative_to(directory).parts)
        for path in directory.rglob("*")
        if path.is_file()
    ]
    return sorted(
        entries, key=lambda entry: (entry.path != _SKILL_MD, entry.path.casefold())
    )


def read_skill_file(name: str, path: str) -> str:
    """读技能包内一个可预览文件的 UTF-8 文本（只读；仅 SKILL.md 与 references/ 开放）。

    路径安全三层：段级校验（拒绝 ``..`` / 绝对路径 / 反斜杠）→ 逐段拼接（拼不出包外
    路径）→ resolve 后核对仍在包目录内（防符号链接逃逸）。

    Args:
        name: skill 名称。
        path: 包内相对路径（POSIX 风格）。

    Returns:
        文件的 UTF-8 文本内容。

    Raises:
        SkillFilePathError: 路径形态不合法（穿越企图等）。
        SkillFileNotPreviewableError: 文件不参与预览（assets / scripts 等），或内容不是 UTF-8 文本。
        SkillNotFoundError: skill 不存在，或包内无此文件。
        SkillNameError: 名称非法。
        SkillError: 文件不可读。
    """
    directory = _require_skill_dir(name)
    parts = _safe_package_parts(path)
    entry = _classify_file(parts)
    if not entry.previewable:
        raise SkillFileNotPreviewableError(
            f"{entry.path} 不参与预览（仅 SKILL.md 与 references/ 下文件可预览；"
            "assets / scripts 不参与注入）。"
        )
    root = directory.resolve()
    target = directory.joinpath(*parts).resolve()
    if not target.is_relative_to(root):
        raise SkillFilePathError(f"包内路径 {path!r} 不合法；拒绝读取。")
    if not target.is_file():
        raise SkillNotFoundError(f"技能包 {name!r} 中不存在文件 {entry.path}。")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillFileNotPreviewableError(
            f"{entry.path} 不是 UTF-8 文本（可能是二进制文件）；无法预览。"
        ) from exc
    except OSError as exc:
        raise SkillError(f"无法读取 {entry.path}：{exc.strerror or exc}") from exc
