"""提示词库的 CRUD 与滚动备份（数据域）——读写只收敛在本模块。

条目 = 平铺 `prompts/<ID>.md`（**文件名即内部稳定 ID**，创建时分配、不随改名变化）；
frontmatter 的 name = 显示名（可改、允许重名）——「改名 = 改 frontmatter 一个字段」，
文件名永不动，引用（策略 / 会话设置 / 快照）一律存 ID（2026-09-23 ID 化，与策略库同构）。
保存时若已存在，先把旧版**复制**进 `_history/<ID>.<时间戳>.md`（不是移动——保证当前
条目任何时刻都在、崩溃安全），按时间戳命名、并把该条目的历史裁到最近 N 版，再原子写
新版；单条序列化后卡 32 KiB 字节护栏。错误口径：单条读写（保存 / 读取 / 改名 / 删除）
fail loud，给可操作错误；**列表（list_prompts）对单个损坏条目宽容降级**（2026-09-14
用户定夺）——坏文件以「文件损坏：…」条目照常进列表，其余不受影响。
**存量迁移**：读侧发现旧版条目（文件名不合 ID 形状）即惰性升级——补 frontmatter name
字段（旧文件名即显示名）、原子回写、文件改名换 ID、历史前缀跟移；逐条幂等。
原子写与数据根复用共享的 `_fs`；本模块禁 import 入口层与 llm（分层契约守）。
"""

from __future__ import annotations

import secrets
import shutil
from datetime import datetime
from pathlib import Path

from filelock import Timeout

from .._fs import atomic_write_text, data_root
from .._locks import shared_file_lock
from .builtin import BUILTIN_PRESET_VERSION, BUILTIN_PROMPTS
from .errors import (
    PromptError,
    PromptNameError,
    PromptNotFoundError,
    PromptParseError,
    PromptTooLargeError,
)
from .model import PROMPT_ID_RE, Prompt, dump_prompt, parse_prompt

_PROMPTS_DIRNAME = "prompts"
_HISTORY_DIRNAME = "_history"
_SUFFIX = ".md"
_MAX_ENTRY_BYTES = (
    32 * 1024
)  # 单条提示词字节上限，对齐 Codex project_doc_max_bytes；可调。
#: 写者与写者之间的等待上限：临界区只有「复制旧版 + 原子写」，毫秒级，10 秒只用于诊断卡死。
_EDIT_LOCK_TIMEOUT_SECONDS = 10.0

_HISTORY_KEEP = 20  # 每条提示词在 _history/ 保留的最近版本数；可调。
_STAMP_FORMAT = (
    "%Y%m%d-%H%M%S-%f"  # 定宽、字典序即时间序；微秒精度让同秒多次保存不撞名。
)


def _prompts_dir() -> Path:
    """提示词库根目录 = 数据根下的 prompts/。"""
    return data_root() / _PROMPTS_DIRNAME


def _entry_path(pid: str) -> Path:
    """某 ID 对应的条目文件路径 prompts/<id>.md。"""
    return _prompts_dir() / f"{pid}{_SUFFIX}"


def _generate_id() -> str:
    """生成字母开头的随机短 ID（文件名即 ID；字母开头避免被 CLI 解析为选项）。"""
    return "p" + secrets.token_urlsafe(8)[:10]


def _validate_display_name(name: str) -> str:
    """校验显示名：去首尾空白后非空、不超长；返回规整后的名字。

    显示名不再是文件名（文件名是 ID），文件名保留字符约束不再适用——只挡空与离谱长度，
    与策略库同口径。
    """
    cleaned = name.strip()
    if not cleaned:
        raise PromptNameError("提示词名称不能为空。")
    if len(cleaned) > 100:
        raise PromptNameError(
            f"提示词名称过长（{len(cleaned)} 字符，上限 100）；请缩短。"
        )
    return cleaned


def _iter_entry_files() -> list[Path]:
    """列出现有条目文件（跳过点前缀的锁与临时文件；库目录不存在 = 空库）。"""
    directory = _prompts_dir()
    if not directory.is_dir():
        return []
    return [
        path
        for path in directory.glob(f"*{_SUFFIX}")
        if path.is_file() and not path.name.startswith(".")
    ]


def _load_entries() -> dict[str, Path]:
    """扫描并返回全部条目 {id: 文件路径}；顺手完成旧版条目的惰性迁移。

    迁移判据：文件名（去 .md）不合 ID 形状 = 旧版条目（文件名即旧显示名）→ 解析出
    内容后补 frontmatter name 字段（原子回写）、文件改名换 ID、历史前缀跟移。已迁移
    条目与损坏文件（解析不了，留给列表降级呈现）零写操作。
    """
    entries: dict[str, Path] = {}
    for path in _iter_entry_files():
        stem = path.name[: -len(_SUFFIX)]
        if PROMPT_ID_RE.fullmatch(stem):
            entries[stem] = path
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            entries[stem] = path  # 损坏文件：不迁移，列表侧降级呈现（id=旧文件名）。
            continue
        try:
            legacy = parse_prompt(stem, text, fallback_name=stem)
        except PromptParseError:
            entries[stem] = path  # 损坏文件：不迁移，列表侧降级呈现（id=旧文件名）。
            continue
        new_id = _generate_id()
        while (_prompts_dir() / f"{new_id}{_SUFFIX}").exists():
            new_id = _generate_id()
        # 顺序保证幂等：先就地补 frontmatter（崩溃后重扫仍是旧名、重做迁移），再改名换 ID。
        atomic_write_text(
            path,
            dump_prompt(
                Prompt(
                    id=new_id,
                    name=legacy.name,
                    description=legacy.description,
                    body=legacy.body,
                )
            ),
        )
        target = _prompts_dir() / f"{new_id}{_SUFFIX}"
        try:
            path.rename(target)
        except OSError as exc:
            raise PromptError(
                f"无法把提示词条目 {path.name} 迁移为 ID「{new_id}」：{exc.strerror or exc}"
            ) from exc
        _move_history_prefix(_prompts_dir() / _HISTORY_DIRNAME, stem, new_id)
        entries[new_id] = target
    return entries


def _read_entry(path: Path) -> Prompt:
    """读取并解析单个条目文件；不可读 / 损坏 fail loud。

    Args:
        path: 条目文件路径（<id>.md）。

    Returns:
        解析出的 Prompt（id 取自文件名，name 取自 frontmatter、缺失回落 ID）。

    Raises:
        PromptError: 文件不可读（底层 OSError）。
        PromptParseError: 文件不是合法 UTF-8，或 frontmatter 损坏 / 非法。
    """
    pid = path.name[: -len(_SUFFIX)]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PromptParseError(
            f"提示词 {pid!r} 不是合法 UTF-8 文本；文件可能已损坏。"
        ) from exc
    except OSError as exc:
        raise PromptError(f"无法读取提示词 {path}：{exc.strerror or exc}") from exc
    return parse_prompt(pid, text)


def list_prompts() -> list[Prompt]:
    """列出全部提示词条目（按显示名排序、不区分大小写）；单个损坏条目**降级呈现**、不拦整库。

    只认 prompts/ 下的普通 `*.md` 文件：跳过 _history/ 子目录、原子写留下的 `.` 前缀临时
    文件与任何非普通文件。库目录不存在时返回空列表（还没有任何条目，不算错）。

    损坏条目的降级口径（2026-09-14 用户定夺，Web 与 CLI 同此）：解析失败的文件仍以
    文件名（ID）进列表，description = 可读的损坏原因（哪里坏、怎么修），body = 文件原始
    全文（可在编辑列直接修复后保存，保存即自愈）；其余条目不受影响。单条读取
    （read_prompt）仍 fail loud 给详细错误——打标装配侧拿到损坏条目会被明确拒绝，
    不会带病使用。

    Returns:
        提示词列表（含降级的损坏条目），按显示名排序。
    """
    prompts: list[Prompt] = []
    for pid, path in _load_entries().items():
        try:
            prompts.append(_read_entry(path))
        except (PromptError, PromptParseError) as exc:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
            prompts.append(
                Prompt(id=pid, name=pid, description=f"文件损坏：{exc}", body=raw)
            )
    return sorted(prompts, key=lambda prompt: prompt.name.casefold())


def _resolve_read_ref(ref: str) -> str | None:
    """读取入口的宽容解析：ID 优先；唯一显示名次之；解析不到返回 None。"""
    prompts = list_prompts()
    if any(prompt.id == ref for prompt in prompts):
        return ref
    by_name = [prompt for prompt in prompts if prompt.name == ref]
    return by_name[0].id if len(by_name) == 1 else None


def read_prompt(pid: str) -> Prompt:
    """读一条提示词：接受 ID 或唯一显示名（显示名重名不唯一时报错）。

    Args:
        pid: 条目 ID（= 文件名去掉 .md）或唯一显示名。

    Returns:
        对应的 Prompt。

    Raises:
        PromptNotFoundError: 没有这个 ID / 显示名的条目。
        PromptError: 文件不可读。
        PromptParseError: 文件损坏。
    """
    resolved = _resolve_read_ref(pid)
    path = _load_entries().get(resolved or "")
    if path is None:
        raise PromptNotFoundError(
            f"未找到提示词 {pid!r}；用 list_prompts 查看现有条目。"
        )
    return _read_entry(path)


def save_prompt(prompt: Prompt) -> str:
    """保存（新建或覆盖）一条提示词；覆盖前把旧版复制进 _history/ 滚动备份。

    id 缺省（空串）或不是 ID 形状（损坏条目经编辑器自愈的路径）时分配新 ID；显示名
    非空即可（允许重名）。卡 32 KiB 字节上限（超限即拒，不落盘、不建目录）；库目录
    不存在则创建；若目标已存在，先把它复制进 `_history/<ID>.<时间戳>.md` 并把该条目
    历史裁到最近 N 版，再原子写新版——原子写保证条目要么是旧版要么是新版，绝不会是
    写了一半的损坏文件。

    Args:
        prompt: 要保存的提示词（id / name / description / body）。

    Returns:
        落盘条目的 ID（新建 / 自愈路径会分配新 ID，覆盖路径 = 原样返回）。

    Raises:
        PromptNameError: 显示名非法。
        PromptTooLargeError: 序列化后超过 32 KiB。
        PromptError: 目录 / 备份 / 写入等底层失败，或内容含 UTF-8 无法编码的字符。
    """
    display = _validate_display_name(prompt.name)
    if PROMPT_ID_RE.fullmatch(prompt.id):
        pid = prompt.id
    elif prompt.id and (_prompts_dir() / f"{prompt.id}{_SUFFIX}").is_file():
        # 损坏旧条目的自愈路径：列表给它的 id 是旧文件名——就地覆盖修复，
        # 迁移在下次读取时把它换上正式 ID。
        pid = prompt.id
    else:
        pid = _generate_id()
    text = dump_prompt(
        Prompt(id=pid, name=display, description=prompt.description, body=prompt.body)
    )
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PromptError(
            f"提示词 {display!r} 含 UTF-8 无法编码的字符（{exc.reason}）；请检查内容。"
        ) from exc
    if size > _MAX_ENTRY_BYTES:
        raise PromptTooLargeError(
            f"提示词 {display!r} 序列化后有 {size} 字节，超过上限 "
            f"{_MAX_ENTRY_BYTES} 字节（32 KiB）；请精简正文。"
        )
    directory = _prompts_dir()
    path = directory / f"{pid}{_SUFFIX}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PromptError(
            f"无法在 {directory} 准备写入：{exc.strerror or exc}"
        ) from exc
    # 备份 + 换版是一个整体：不加锁时两个写者会互相吞掉历史（各自的 copyfile 与
    # 原子写交错，裁历史还会删掉对方刚写进去的那一份）。锁名沿用 skills 的约定。
    try:
        with shared_file_lock(_edit_lock_path(directory, pid)).acquire(
            timeout=_EDIT_LOCK_TIMEOUT_SECONDS
        ):
            if path.is_file():
                _backup_to_history(directory, pid, path)
            atomic_write_text(path, text)
    except Timeout as exc:
        raise PromptError(
            f"提示词 {display!r} 正在被另一个进程修改；请稍后重试。"
        ) from exc
    except OSError as exc:
        raise PromptError(f"无法写入提示词 {path}：{exc.strerror or exc}") from exc
    return pid


def prompt_id_by_display_name(name: str) -> str | None:
    """按显示名查唯一提示词 ID；不存在或重名（不唯一）返回 None。

    供存量迁移（策略 JSON / 会话设置里的旧版名字引用 → ID）与 CLI 的名称便利解析。
    """
    matches = [prompt.id for prompt in list_prompts() if prompt.name == name]
    return matches[0] if len(matches) == 1 else None


def delete_prompt(ref: str) -> None:
    """删除一条提示词及其全部历史备份（接受 ID 或唯一显示名）。

    Raises:
        PromptNotFoundError: 没有这个 ID / 显示名的条目。
        PromptError: 删除失败。
    """
    resolved = _resolve_read_ref(ref)
    pid = resolved or ""
    path = _load_entries().get(pid)
    if path is None:
        raise PromptNotFoundError(f"未找到提示词 {pid!r}；无需删除。")
    directory = _prompts_dir()
    try:
        with shared_file_lock(_edit_lock_path(directory, pid)).acquire(
            timeout=_EDIT_LOCK_TIMEOUT_SECONDS
        ):
            path.unlink()
    except Timeout as exc:
        raise PromptError(f"提示词 {pid!r} 正在被另一个进程修改；请稍后重试。") from exc
    except OSError as exc:
        raise PromptError(f"无法删除提示词 {path}：{exc.strerror or exc}") from exc
    history_dir = directory / _HISTORY_DIRNAME
    if history_dir.is_dir():
        for old in _history_versions(history_dir, pid):
            old.unlink(missing_ok=True)


def rename_prompt(ref: str, new_name: str) -> Prompt:
    """改一条提示词的显示名：只写 frontmatter 的 name 字段（文件名是 ID，永不动）。

    Args:
        ref: 条目 ID 或唯一显示名。
        new_name: 新显示名。

    Returns:
        改名后的条目。

    Raises:
        PromptNameError: 显示名非法。
        PromptNotFoundError: 条目不存在。
        PromptError: 写入失败。
    """
    display = _validate_display_name(new_name)
    current = read_prompt(ref)
    pid = current.id
    updated = Prompt(
        id=current.id, name=display, description=current.description, body=current.body
    )
    atomic_write_text(_entry_path(pid), dump_prompt(updated))
    return updated


def _edit_lock_path(directory: Path, pid: str) -> Path:
    """条目级写锁的文件路径（点前缀：列目录时天然被跳过，与原子写的临时文件同类）。"""
    return directory / f".{pid}.edit.lock"


def _backup_to_history(directory: Path, pid: str, current: Path) -> None:
    """把当前版本复制进 _history/<ID>.<时间戳>.md，再把该条目历史裁到最近 N 版。

    用复制而非移动：当前条目在原子写新版之前始终存在，任何一步崩溃都不会让条目消失。
    """
    history_dir = directory / _HISTORY_DIRNAME
    stamp = datetime.now().strftime(_STAMP_FORMAT)
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, _history_target(history_dir, pid, stamp))
    except OSError as exc:
        raise PromptError(
            f"无法把旧版 {current} 备份进 {history_dir}：{exc.strerror or exc}"
        ) from exc
    _evict_old_history(history_dir, pid)


def _history_target(history_dir: Path, pid: str, stamp: str) -> Path:
    """给历史备份取一个不撞名的路径：<ID>.<时间戳>.md，撞了就加 -<序号>。

    Windows 系统时钟粒度较粗（约 15ms），同一秒内多次保存可能得到相同时间戳；加序号
    保证不覆盖已有备份（宁可多留一版，也不丢一版）。
    """
    candidate = history_dir / f"{pid}.{stamp}{_SUFFIX}"
    seq = 1
    while candidate.exists():
        candidate = history_dir / f"{pid}.{stamp}-{seq}{_SUFFIX}"
        seq += 1
    return candidate


# _STAMP_FORMAT 产出的时间戳定宽（8+1+6+1+6 = 22）：历史排序按它切出时间戳与同戳序号。
_STAMP_WIDTH = len(datetime(2026, 12, 31, 23, 59, 59, 999999).strftime(_STAMP_FORMAT))


def _history_versions(history_dir: Path, pid: str) -> list[Path]:
    """某条目在 _history/ 里的全部版本，按产生顺序（时间戳 + 同戳序号）从旧到新排序。

    历史文件名形如 `<ID>.<时间戳>.md`（ID 不含点号，`<ID>.` 前缀能精确锁定该条目）；
    同戳撞名加 `-<序号>`，序号即产生顺序。排序不能用字典序——`-` 排在 `.` 之前，会把
    同戳的 `-1` 版排到基础版前面、颠倒新旧；按「定宽时间戳 + 序号」解析出真实顺序。
    """
    prefix = f"{pid}."
    versions = [
        path
        for path in history_dir.iterdir()
        if path.is_file()
        and path.name.startswith(prefix)
        and path.name.endswith(_SUFFIX)
    ]

    def sort_key(path: Path) -> tuple[str, int]:
        middle = path.name[len(prefix) : -len(_SUFFIX)]
        tail = middle[_STAMP_WIDTH + 1 :]
        if (
            len(middle) > _STAMP_WIDTH
            and middle[_STAMP_WIDTH] == "-"
            and tail.isdigit()
        ):
            return middle[:_STAMP_WIDTH], int(tail)
        return middle, 0

    versions.sort(key=sort_key)
    return versions


def _evict_old_history(history_dir: Path, pid: str) -> None:
    """把某条目的历史裁到最近 N 版：删掉最旧的、超出保留数的版本。"""
    versions = _history_versions(history_dir, pid)
    overflow = len(versions) - _HISTORY_KEEP
    for stale in versions[: max(0, overflow)]:
        stale.unlink(missing_ok=True)


def _move_history_prefix(history_dir: Path, old_prefix: str, new_id: str) -> None:
    """迁移时把旧显示名的历史版本改前缀到新 ID 下；个别改不动跳过（备份不阻塞主操作）。"""
    if not history_dir.is_dir():
        return
    for old in _history_versions(history_dir, old_prefix):
        tail = old.name[len(old_prefix) :]  # 形如 .<时间戳>[-序号].md
        candidate = history_dir / f"{new_id}{tail}"
        seq = 1
        while candidate.exists():
            stem = tail[: -len(_SUFFIX)]
            candidate = history_dir / f"{new_id}{stem}-{seq}{_SUFFIX}"
            seq += 1
        try:
            old.rename(candidate)
        except OSError:
            continue


_SEED_MARKER_NAME = ".builtin-presets-seeded"


def seed_builtin_presets() -> None:
    """一次性播种产品内置预置提示词：首次使用时把内置条目写进提示词库。

    标记文件 `prompts/.builtin-presets-seeded`（内容 = 内置集合版本号）记录「播种过」；
    标记在即直接返回（常态零开销）。播种只写「显示名不存在」的条目（不覆盖用户自建的
    同名条目），写完落标记——此后用户可自由修改 / 删除内置条目，不会被覆盖或复活。

    Raises:
        PromptError: 目录创建 / 条目写入 / 标记写入失败。
    """
    directory = _prompts_dir()
    marker = directory / _SEED_MARKER_NAME
    if marker.is_file():
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PromptError(
            f"无法在 {directory} 准备写入：{exc.strerror or exc}"
        ) from exc
    existing_names = {prompt.name for prompt in list_prompts()}
    for preset in BUILTIN_PROMPTS:
        if preset.name in existing_names:
            continue
        new_id = _generate_id()
        try:
            atomic_write_text(
                _entry_path(new_id),
                dump_prompt(
                    Prompt(
                        id=new_id,
                        name=preset.name,
                        description=preset.description,
                        body=preset.body,
                    )
                ),
            )
        except OSError as exc:
            raise PromptError(
                f"无法写入内置提示词 {preset.name!r}：{exc.strerror or exc}"
            ) from exc
    try:
        atomic_write_text(marker, BUILTIN_PRESET_VERSION)
    except OSError as exc:
        raise PromptError(f"无法写入播种标记 {marker}：{exc.strerror or exc}") from exc
