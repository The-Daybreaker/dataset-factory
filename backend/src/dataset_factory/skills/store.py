"""Skill 库的导入 / 启用停用 / 读取（数据域）——读写只收敛在本模块。

Skill 包按 agentskills.io 开放标准（`<name>/SKILL.md` + references/ 等）导入 = 整目录复制进
`skills/<ID>/`（**目录名即内部稳定 ID**，创建时分配、不随改名变化；frontmatter 的 name =
显示名，可改、允许重名——「改名 = 改 frontmatter 一个字段」，目录永不动，引用一律存 ID，
2026-09-23 ID 化、与策略库同构）。同显示名的包可并存（导入不再拒绝重名）。启用 / 停用
状态记在独立清单 `skills/_state.json`（存 ID；不写进 skill 目录，保持包「原样」）；停用不删除。
错误口径：单条操作 fail loud，**列表（list_skills）对单个损坏包宽容降级**（2026-09-15 用户
定夺）——坏包以「文件损坏：…」条目照常进列表，其余不受影响。目录级导入用「复制到临时名 +
os.replace 改名」做到崩溃不留半个包；**存量迁移**：读侧发现目录名不合 ID 形状（旧版以
frontmatter name 当目录名）即惰性升级——目录改名为新 ID、`_state.json` 的旧名条目映射到
新 ID；逐包原子、幂等。数据根 + 原子写复用共享 `_fs`；本模块禁 import 入口层与 llm
（分层契约守）。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import cast

import yaml
from filelock import Timeout

from .._fs import atomic_write_text, data_root
from .._locks import shared_file_lock
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
_REFERENCES_DIRNAME = "references"
_STATE_FILENAME = "_state.json"
_DELIMITER = "---"
_LF = "\n"
_CRLF = "\r\n"
_CR = "\r"

# Skill ID 形状（目录名即 ID；与策略 / 端点 ID 同一模式，字母开头避免被 CLI 解析为选项）。
SKILL_ID_RE = re.compile(r"^[a-z][A-Za-z0-9_-]{10}$")

# 显示名长度上限（纯显示别名、允许重名，对齐策略库口径）。
_MAX_DISPLAY_NAME_CHARS = 100


def _generate_id() -> str:
    """生成字母开头的随机短 ID（目录名即 ID；字母开头避免被 CLI 解析为选项）。"""
    return "k" + secrets.token_urlsafe(8)[:10]


def _skills_dir() -> Path:
    """Skill 库根目录 = 数据根下的 skills/。"""
    return data_root() / _SKILLS_DIRNAME


def _skill_dir(sid: str) -> Path:
    """某 ID 的 skill 目录路径 skills/<id>（调用方负责先确认存在）。"""
    return _skills_dir() / sid


def _validate_display_name(name: str) -> str:
    """校验显示名（改名入口）：去首尾空白后非空、不超长；返回规整后的名字。

    显示名不再是目录名（目录名是 ID），文件名保留字符约束不再适用——只挡空与离谱长度。
    """
    cleaned = name.strip()
    if not cleaned:
        raise SkillNameError("skill 显示名不能为空。")
    if len(cleaned) > _MAX_DISPLAY_NAME_CHARS:
        raise SkillNameError(
            f"skill 显示名过长（{len(cleaned)} 字符，上限 {_MAX_DISPLAY_NAME_CHARS}）；请缩短。"
        )
    return cleaned


def _read_text(path: Path) -> str:
    """读 UTF-8 文本；不可读 / 非文本统一转成技能域错误（统一小闸门）。"""
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

    dest 必须尚不存在（ID 由调用方新生成）。改名成功后临时目录已不存在（finally 的
    rmtree 即 no-op）；任何失败路径都清掉临时目录，崩溃也不留下半个包冒充成品。

    Args:
        source: 源 skill 目录。
        dest: 库内目标目录（skills/<id>）。

    Raises:
        SkillError: 复制或改名失败（底层 OSError，含 shutil.Error）。
    """
    tmp = dest.parent / f".{dest.name}.tmp{os.urandom(4).hex()}"
    try:
        shutil.copytree(source, tmp)
        _publish_skill(tmp, dest)
    except OSError as exc:
        raise SkillError(f"无法把 {source} 复制进库：{exc.strerror or exc}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_disabled() -> set[str]:
    """读启用状态清单里的「停用」集合；清单不存在 = 没有停用的（默认全启用）。

    Returns:
        被停用的 skill ID 集合（旧版清单存显示名，由迁移映射为 ID）。

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
    """原子写启用状态清单（只记停用的 ID，启用的不记——默认即启用）。

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


@contextmanager
def _mutation_lock(path: Path) -> Generator[None]:
    """将跨进程写锁的等待失败统一转换为技能域异常。"""
    try:
        with shared_file_lock(path).acquire(timeout=10):
            yield
    except Timeout as exc:
        raise SkillExistsError("技能库正在修改，请稍后重试。") from exc
    except OSError as exc:
        raise SkillError(f"无法修改技能库：{exc.strerror or exc}") from exc


def _load_entries() -> dict[str, Path]:
    """扫描并返回全部 skill 包 {id: 目录路径}；顺手完成旧版目录的惰性迁移。

    迁移判据：目录名不合 ID 形状 = 旧版条目（目录名即 frontmatter name）→ 解析
    frontmatter 取显示名（解析失败的损坏包不迁移，键仍用目录名，列表侧降级呈现）、
    目录改名为新 ID；迁移完成后把 `_state.json` 停用清单里的旧名映射成新 ID（解析
    不到的旧名——包早已被删——直接剔除）。已迁移条目零写操作。
    """
    directory = _skills_dir()
    if not directory.is_dir():
        return {}
    entries: dict[str, Path] = {}
    name_map: dict[str, str] = {}
    migrated = False
    for entry in directory.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if not (entry / _SKILL_MD).is_file():
            continue
        if SKILL_ID_RE.fullmatch(entry.name):
            entries[entry.name] = entry
            continue
        try:
            parse_skill_frontmatter(_read_text(entry / _SKILL_MD))
        except SkillError:
            entries[entry.name] = (
                entry  # 损坏包：不迁移，列表侧降级呈现（id=旧目录名）。
            )
            continue
        new_id = _generate_id()
        while (directory / new_id).exists():
            new_id = _generate_id()
        try:
            entry.rename(directory / new_id)
        except OSError as exc:
            raise SkillError(
                f"无法把 skill 包 {entry.name!r} 迁移为 ID「{new_id}」：{exc.strerror or exc}"
            ) from exc
        name_map[entry.name] = new_id
        entries[new_id] = directory / new_id
        migrated = True
    if migrated and name_map:
        disabled = _read_disabled()
        remapped = {name_map.get(name, name) for name in disabled}
        remapped = {sid for sid in remapped if sid in entries}
        if remapped != disabled:
            _write_disabled(remapped)
    return entries


def import_skill(source: Path) -> SkillImport:
    """从一个 agentskills.io skill 目录导入：校验 → 整目录复制进库（自包含）→ 默认启用。

    源必须是一个含 SKILL.md 的目录；显示名与说明取自 SKILL.md frontmatter，包落到
    新分配的 `skills/<ID>/`（同显示名可并存——身份是 ID，不再拒绝重名）。复制走
    「临时名 + os.replace」的目录级原子导入，崩溃不留半个包。

    Args:
        source: 源 skill 目录（含 SKILL.md，可含 references/ 等子目录）。

    Returns:
        SkillImport：导入的 skill（enabled=True）+ 整包字节数（供体积提示）。

    Raises:
        SkillSourceError: 源不是目录（路径填错）。
        SkillFormatError: 源缺 SKILL.md，或 SKILL.md frontmatter 非法。
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
    skills_root = _skills_dir()
    sid = _generate_id()
    while (skills_root / sid).exists():
        sid = _generate_id()
    _atomic_copytree(source, skills_root / sid)
    return SkillImport(
        skill=Skill(id=sid, name=name, description=description, enabled=True),
        total_bytes=_dir_bytes(skills_root / sid),
    )


def import_skill_files(files: Mapping[str, bytes]) -> SkillImport:
    """从「相对路径 → 内容」的文件集导入 skill 包（浏览器文件夹选择上传路线）。

    与 import_skill 共用同一套校验与原子落库：包根必须有 SKILL.md、frontmatter 合法；
    包落到新分配的 `skills/<ID>/`（同显示名可并存）。相对路径的卫生（拒绝绝对路径与
    ``..`` 穿越）由 HTTP 入口层在收包时清洗，本函数按可信输入对待。

    Args:
        files: 包内相对路径（POSIX 风格，如 SKILL.md、references/x.md）→ 文件字节内容。

    Returns:
        SkillImport：导入的 skill（enabled=True）+ 整包字节数（供体积提示）。

    Raises:
        SkillFormatError: 文件集缺 SKILL.md / SKILL.md 不是合法 UTF-8 / frontmatter 非法。
        SkillError: 库目录准备 / 写入失败。
    """
    skill_md_bytes = files.get(_SKILL_MD)
    if skill_md_bytes is None:
        raise SkillFormatError(
            f"上传内容缺少 {_SKILL_MD}；请选择包含 {_SKILL_MD} 的 skill 文件夹导入。"
        )
    try:
        skill_md_text = skill_md_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillFormatError(
            f"{_SKILL_MD} 不是合法 UTF-8 文本；文件可能已损坏。"
        ) from exc
    name, description = parse_skill_frontmatter(skill_md_text)
    skills_root = _skills_dir()
    sid = _generate_id()
    dest = skills_root / sid
    tmp = dest.parent / f".{dest.name}.tmp{os.urandom(4).hex()}"
    try:
        skills_root.mkdir(parents=True, exist_ok=True)
        tmp.mkdir()
        for rel, content in files.items():
            target = tmp.joinpath(*[p for p in rel.split("/") if p])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _publish_skill(tmp, dest)
    except OSError as exc:
        raise SkillError(f"无法把上传的 skill 包写入库：{exc.strerror or exc}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return SkillImport(
        skill=Skill(id=sid, name=name, description=description, enabled=True),
        total_bytes=sum(len(content) for content in files.values()),
    )


def _publish_skill(temporary: Path, destination: Path) -> None:
    """发布完整包时与编辑、删除互斥（ID 由调用方新生成，无同名冲突）。"""
    with (
        _mutation_lock(destination.parent / f".{destination.name}.edit.lock"),
        _mutation_lock(destination.parent / ".state.lock"),
    ):
        os.replace(temporary, destination)


def list_skills() -> list[Skill]:
    """列出库里全部 skill（按显示名排序、不区分大小写），带各自的库级启用状态。

    单个损坏包**降级呈现**、不拦整库。只认 skills/ 下含 SKILL.md 的普通子目录：跳过
    `_state.json` 等文件、`.`/`_` 前缀的内部目录、以及不含 SKILL.md 的杂目录。库目录
    不存在时返回空列表（还没有任何包，不算错）。

    损坏包的降级口径（2026-09-15 用户定夺，与提示词列表同款，Web 与 CLI 同此）：SKILL.md
    损坏（非 UTF-8 / frontmatter 非法 / 包体组装失败）的包仍以 ID 进列表，description =
    可读的损坏原因（哪里坏、怎么修），body_chars = 0；其余条目不受影响。单条读取
    （read_skill）与打标装配侧仍 fail loud——被勾选的坏包会被明确拒绝，不会带病使用。

    Returns:
        skill 列表（含降级的损坏包），按显示名排序。

    Raises:
        SkillError: 启用状态清单损坏。
    """
    disabled = _read_disabled()
    skills: list[Skill] = []
    for sid, directory in _load_entries().items():
        # 两级降级：frontmatter 损坏 = 连显示名都拿不到（name 回落 ID）；frontmatter
        # 合法但正文 / references 组装失败 = 保留显示名、body_chars 按 0 计。
        try:
            skill_text = _read_text(directory / _SKILL_MD)
            name, description = parse_skill_frontmatter(skill_text)
        except SkillError as exc:
            skills.append(
                Skill(
                    id=sid,
                    name=sid,
                    description=f"文件损坏：{exc}",
                    enabled=sid not in disabled,
                    body_chars=0,
                )
            )
            continue
        try:
            body_chars = len(_assemble_skill_body(directory, skill_text))
        except SkillError as exc:
            skills.append(
                Skill(
                    id=sid,
                    name=name,
                    description=f"文件损坏：{exc}",
                    enabled=sid not in disabled,
                    body_chars=0,
                )
            )
            continue
        skills.append(
            Skill(
                id=sid,
                name=name,
                description=description,
                enabled=sid not in disabled,
                body_chars=body_chars,
            )
        )
    return sorted(skills, key=lambda skill: skill.name.casefold())


def skill_id_by_display_name(name: str) -> str | None:
    """按显示名查唯一 skill ID；不存在或重名（不唯一）返回 None。

    供存量迁移（策略 JSON / 会话设置里的旧版名字引用 → ID）与 CLI 的名称便利解析。
    """
    matches = [skill.id for skill in list_skills() if skill.name == name]
    return matches[0] if len(matches) == 1 else None


def _resolve_read_ref(ref: str) -> str | None:
    """读取入口的宽容解析：ID 优先；唯一显示名次之；解析不到返回 None。"""
    skills = list_skills()
    if any(skill.id == ref for skill in skills):
        return ref
    by_name = [skill for skill in skills if skill.name == ref]
    return by_name[0].id if len(by_name) == 1 else None


def get_skill(ref: str) -> Skill:
    """按 ID 或唯一显示名读单个 skill 的元数据（frontmatter 的显示名与说明；不组装正文）。

    Raises:
        SkillNotFoundError: 没有这个 ID / 显示名的 skill。
        SkillFormatError: SKILL.md frontmatter 损坏。
    """
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    for skill in list_skills():
        if skill.id == sid:
            return skill
    raise SkillNotFoundError(f"未找到 skill {sid!r}；用 list_skills 查看已导入的。")


def read_skill(ref: str) -> str:
    """读某个 skill 的注入全文：SKILL.md 正文 + references/ 全部文件（供打标时包裹注入）。

    接受 skill ID 或唯一显示名。注入范围成文（2026-09-14 用户定夺，见 ADR）：
    SKILL.md 之外，references/ 下全部文件一并注入——agentskills.io 标准里 references
    靠模型运行时读文件（渐进披露），而本项目不做工具调用 / agent 循环，模型没有第二条
    路看到它们；references 每份以 ``<skill-file path="…">`` 标记包裹，让模型与复盘者都
    知道每段内容来自哪个文件。assets / scripts 不参与注入。没有 references/ 时返回值
    就是 SKILL.md 原文本身。

    Args:
        ref: skill ID 或唯一显示名。

    Returns:
        注入全文（SKILL.md 在前，references/ 按路径排序逐份跟随）。

    Raises:
        SkillNotFoundError: 没有这个 ID / 显示名的 skill。
        SkillFormatError: SKILL.md 非法 UTF-8 / frontmatter 损坏，或某个 reference 文件不是合法 UTF-8。
        SkillError: 文件不可读。
    """
    resolved = _resolve_read_ref(ref)
    if resolved is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    skill_dir = _skill_dir(resolved)
    skill_md = skill_dir / _SKILL_MD
    if not skill_md.is_file():
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    skill_text = _read_text(skill_md)
    # frontmatter 在此复检：列表降级后这里是坏包进注入流程的唯一闸门（不把损坏包带病注入）。
    parse_skill_frontmatter(skill_text)
    return _assemble_skill_body(skill_dir, skill_text)


def _assemble_skill_body(skill_dir: Path, skill_md_text: str) -> str:
    """组装注入全文：SKILL.md 正文在前，references/ 全部文件按路径排序逐份跟随。

    Args:
        skill_dir: skill 包目录（skills/<id>/）。
        skill_md_text: 已读出的 SKILL.md 全文。

    Returns:
        注入全文；references 文件每份用 ``<skill-file path>`` 标记包裹，段间空行分隔。

    Raises:
        SkillFormatError: 某个 reference 文件不是合法 UTF-8。
        SkillError: 某个 reference 文件不可读。
    """
    sections = [skill_md_text]
    refs_dir = skill_dir / _REFERENCES_DIRNAME
    if refs_dir.is_dir():
        for path in sorted(
            (p for p in refs_dir.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(refs_dir).as_posix().casefold(),
        ):
            rel = path.relative_to(skill_dir).as_posix()
            sections.append(
                f'<skill-file path="{rel}">\n{_read_text(path)}\n</skill-file>'
            )
    return "\n\n".join(sections)


def set_enabled(ref: str, enabled: bool) -> None:
    """启用 / 停用某个 skill（停用不删除：只改 _state.json 清单，skill 目录原样保留）。

    Args:
        ref: skill ID 或唯一显示名。
        enabled: True 启用、False 停用。

    Raises:
        SkillNotFoundError: 没有这个 ID 的 skill。
        SkillError: 启用状态清单读写失败。
    """
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    with (
        _mutation_lock(_skills_dir() / f".{sid}.edit.lock"),
        _mutation_lock(_skills_dir() / ".state.lock"),
    ):
        disabled = _read_disabled()
        if enabled:
            disabled.discard(sid)
        else:
            disabled.add(sid)
        _write_disabled(disabled)


def delete_skill(ref: str) -> None:
    """删除某个 skill：移除整目录 + 从启用状态清单里清掉（显式删除；只想收起请改用停用）。

    Args:
        ref: skill ID 或唯一显示名。

    Raises:
        SkillNotFoundError: 没有这个 ID / 显示名的 skill。
        SkillError: 删除失败，或启用状态清单读写失败。
    """
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；无需删除。")
    target = _skill_dir(sid)
    with (
        _mutation_lock(_skills_dir() / f".{sid}.edit.lock"),
        _mutation_lock(_skills_dir() / ".state.lock"),
    ):
        disabled = _read_disabled()
        shutil.rmtree(target)
        if sid in disabled:
            disabled.discard(sid)
            _write_disabled(disabled)


def rename_skill(ref: str, new_name: str) -> None:
    """改 skill 的显示名：只写 SKILL.md frontmatter 的 name 字段（目录名是 ID，永不动）。

    与 ID 化前的「目录改名 + frontmatter 跟写 + 失败回滚」三步舞不同：现在只有一个
    原子写，无劈叉窗口、无回滚路径。启用状态清单存 ID，不受改名影响。

    Args:
        ref: skill ID 或唯一显示名。
        new_name: 目标显示名（允许重名；只挡空与离谱长度）。

    Raises:
        SkillNameError: 显示名非法。
        SkillNotFoundError: 没有这个 ID 的 skill。
        SkillFormatError: SKILL.md 缺 frontmatter / name 字段不唯一 / 非 UTF-8。
        SkillError: 写入失败。
    """
    display = _validate_display_name(new_name)
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    skill_dir = _skill_dir(sid)
    skill_md = skill_dir / _SKILL_MD
    text = _read_text(skill_md)
    updated = _replace_frontmatter_name(text, display)
    try:
        atomic_write_text(skill_md, updated)
    except OSError as exc:
        raise SkillError(f"无法写入 {skill_md}：{exc.strerror or exc}") from exc


def _replace_frontmatter_name(text: str, new_name: str) -> str:
    """把 SKILL.md frontmatter 里的 name 字段改写成新值，其余内容原样保留。

    与 save_skill_file 的描述改写同一技术：yaml compose 拿到标量的精确字节位置，
    只替换该标量本身——frontmatter 其余字段、注释与正文一个字都不动。入参先按
    parse 的同一套归一化处理 BOM 与 CRLF，因此回写会把 CRLF 包归一成 LF。

    Args:
        text: SKILL.md 全文。
        new_name: 要写入的 name 值（显示名，可作 YAML 纯量）。

    Returns:
        改写后的 SKILL.md 全文。

    Raises:
        SkillFormatError: 缺 frontmatter / 未闭合 / 顶层非映射 / name 字段不唯一。
    """
    normalized = text.lstrip(chr(0xFEFF)).replace(_CRLF, _LF).replace(_CR, _LF)
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].strip() != _DELIMITER:
        raise SkillFormatError("SKILL.md 缺少 YAML frontmatter，无法改名。")
    end = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == _DELIMITER), None
    )
    if end is None:
        raise SkillFormatError("SKILL.md 的 frontmatter 未闭合，无法改名。")
    front = "".join(lines[1:end])
    node = cast(
        object,
        yaml.compose(front, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
    )
    if not isinstance(node, yaml.MappingNode):
        raise SkillFormatError("SKILL.md 的 frontmatter 顶层应是映射。")
    values = [value for key, value in node.value if key.value == "name"]
    if len(values) != 1:
        raise SkillFormatError("SKILL.md 必须包含唯一的 name 字段。")
    value = values[0]
    replacement = json.dumps(new_name, ensure_ascii=False)
    suffix = front[value.end_mark.index :]
    if not suffix.startswith((_LF, _CR, " ", "\t")):
        replacement += _LF
    front = front[: value.start_mark.index] + replacement + suffix
    return "---" + _LF + front + "---" + _LF + "".join(lines[end + 1 :])


def _classify_file(parts: tuple[str, ...]) -> SkillFileEntry:
    """按包内相对路径段判定文件角色与可预览性。

    注入范围成文（2026-09-14，见 ADR）：SKILL.md 与 references/ 下全部文件注入请求
    （references 每份带路径标记）；assets / scripts 与其他文件不参与注入。可预览 =
    SKILL.md 与 references/ 下文件——预览服务「导入 → 核对 → 启用」闭环，不开放整包
    任意读。

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


def list_skill_files(ref: str) -> list[SkillFileEntry]:
    """列出技能包内全部文件（角色标注），SKILL.md 恒排最前、其余按路径排序。

    Args:
        ref: skill ID 或唯一显示名。

    Returns:
        文件条目列表（含 assets / scripts——它们被列出但不开放内容预览，供界面灰显）。

    Raises:
        SkillNotFoundError: 没有这个 ID 的 skill。
    """
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    directory = _skill_dir(sid)
    entries = [
        _classify_file(path.relative_to(directory).parts)
        for path in directory.rglob("*")
        if path.is_file()
    ]
    return sorted(
        entries, key=lambda entry: (entry.path != _SKILL_MD, entry.path.casefold())
    )


def read_skill_file(ref: str, path: str) -> str:
    """读技能包内一个可预览文件的 UTF-8 文本（只读；仅 SKILL.md 与 references/ 开放）。

    路径安全三层：段级校验（拒绝 ``..`` / 绝对路径 / 反斜杠）→ 逐段拼接（拼不出包外
    路径）→ resolve 后核对仍在包目录内（防符号链接逃逸）。

    Args:
        ref: skill ID 或唯一显示名。
        path: 包内相对路径（POSIX 风格）。

    Returns:
        文件的 UTF-8 文本内容。

    Raises:
        SkillFilePathError: 路径形态不合法（穿越企图等）。
        SkillFileNotPreviewableError: 文件不参与预览（assets / scripts 等），或内容不是 UTF-8 文本。
        SkillNotFoundError: skill 不存在，或包内无此文件。
        SkillError: 文件不可读。
    """
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    directory = _skill_dir(sid)
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
        raise SkillNotFoundError(f"技能包 {ref!r} 中不存在文件 {entry.path}。")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillFileNotPreviewableError(
            f"{entry.path} 不是 UTF-8 文本（可能是二进制文件）；无法预览。"
        ) from exc
    except OSError as exc:
        raise SkillError(f"无法读取 {entry.path}：{exc.strerror or exc}") from exc


def save_skill_file(
    ref: str,
    path: str,
    content: str,
    *,
    original_content: str,
    description: str | None = None,
) -> str:
    """校验并原子写回现有文本文件，拒绝覆盖读取后已被修改的内容。

    Args:
        ref: skill ID 或唯一显示名。
        path: 包内可预览文件的相对路径。
        content: 待保存的完整 UTF-8 文本。
        original_content: 编辑器读取时的文本，用于检测并发修改。
        description: 可选的描述修改，仅适用于 SKILL.md。

    Returns:
        保存后回读的文件内容。

    Raises:
        SkillExistsError: 读取之后文件已被其他写者修改。
        SkillFormatError: 文本无法编码，或 SKILL.md 元数据不合法。
        SkillFilePathError: 目标不在技能包内。
        SkillError: 文件不存在、不可预览或写入失败。
    """
    sid = _resolve_read_ref(ref)
    if sid is None:
        raise SkillNotFoundError(f"未找到 skill {ref!r}；用 list_skills 查看已导入的。")
    directory = _skill_dir(sid)
    parts = _safe_package_parts(path)
    entry = _classify_file(parts)
    if not entry.previewable:
        raise SkillFileNotPreviewableError(
            f"{entry.path} 不参与预览（仅 SKILL.md 与 references/ 下文件可预览；"
            "assets / scripts 不参与注入）。"
        )
    if description is not None:
        if parts != (_SKILL_MD,):
            raise SkillFormatError("描述只能通过 SKILL.md 保存。")
        parse_skill_frontmatter(content)
        lines = (
            content.lstrip(chr(0xFEFF))
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .splitlines(keepends=True)
        )
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        front = "".join(lines[1:end])
        node = cast(
            object,
            yaml.compose(front, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
        )
        if not isinstance(node, yaml.MappingNode):
            raise SkillFormatError("SKILL.md 的 frontmatter 顶层应是映射。")
        values = [value for key, value in node.value if key.value == "description"]
        if len(values) != 1:
            raise SkillFormatError("SKILL.md 必须包含唯一的 description 字段。")
        value = values[0]
        replacement = json.dumps(description, ensure_ascii=False)
        suffix = front[value.end_mark.index :]
        if not suffix.startswith(("\n", "\r", " ", "\t")):
            replacement += "\n"
        front = front[: value.start_mark.index] + replacement + suffix
        content = "---\n" + front + "---\n" + "".join(lines[end + 1 :])
    if parts == (_SKILL_MD,):
        # frontmatter 必须保持合法（name = 显示名、description 用途说明）；正文随便改。
        parse_skill_frontmatter(content)
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SkillFormatError("内容含 UTF-8 无法编码的字符，无法保存。") from exc
    try:
        with shared_file_lock(directory.parent / f".{sid}.edit.lock").acquire(
            timeout=10
        ):
            current = read_skill_file(sid, path)
            if current != original_content:
                raise SkillExistsError("文件已被其他写者修改；请重新读取后合并修改。")
            target = directory.joinpath(*parts).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise SkillFilePathError("文件不在技能包内，无法保存。")
            atomic_write_text(target, content)
            return read_skill_file(sid, path)
    except Timeout as exc:
        raise SkillExistsError("文件正在保存，请稍后重试。") from exc
    except OSError as exc:
        raise SkillError(f"无法保存 {path}：{exc.strerror or exc}") from exc
