"""用户级策略库（数据域，``~/.dataset_factory/strategies/``，与 prompts / skills 平级）。

策略 = **组合清单**：引用端点配置、基础提示词与启用 Skill 三样独立资产
（引用而非内联——资产各有编辑页，改一处影响所有引用它的策略）。库里不编号，
身份 = 内部稳定 ID（随机短 ID，文件名即 ID）；显示名可改、允许重名。

**引用存各资产的稳定 ID**（2026-09-23 ID 化，2026-09-22「端点改名炸引用」事故的根治）：
端点 / 提示词 / Skill 的显示名都可改，名字不再参与引用寻址。存量 JSON 里的旧版
名字引用在读时惰性迁移（按名唯一匹配解析成 ID；解析不到的保留原值 = 继续显示
引用缺失，走「重新指定」恢复）。

引用健康度在读取时**现查**：任一引用（端点配置 / 提示词 / Skill）已不存在 →
``available=False`` + 缺失清单，由界面置灰、禁止应用；处置 = 重新指定
（rebind）/ 删除 / 先放着。创建与更新时则要求引用现存在（fail fast）——
引用缺失的策略没有创建出来的意义，置灰机制管的是「创建之后被删」。

落盘格式：一策略一个 JSON 文件（原子写）；apply 时刻的来源记录
（库 ID + 内容哈希）由批次侧写入快照，本库不存。
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

from .._clock import now_iso
from .._fs import atomic_write_text, canonical_sha256, data_root
from ..llm.endpoints import config_id_by_display_name as _endpoint_id_by_name
from ..llm.endpoints import config_info as _endpoint_info
from ..llm.endpoints import has_config as _endpoint_exists
from ..prompts.store import list_prompts
from ..prompts.store import prompt_id_by_display_name as _prompt_id_by_name
from ..prompts.store import read_prompt as _read_prompt
from ..skills.store import get_skill as _get_skill
from ..skills.store import list_skills
from ..skills.store import skill_id_by_display_name as _skill_id_by_name
from .errors import (
    StrategyError,
    StrategyNameError,
    StrategyNotFoundError,
    StrategyRefsError,
)

__all__ = [
    "LibraryStrategy",
    "copy_strategy",
    "create_strategy",
    "delete_strategy",
    "get_strategy",
    "list_strategies",
    "missing_refs",
    "rebind_strategy",
    "require_refs_exist",
    "strategy_content_hash",
    "update_strategy",
]

#: 库目录名（数据根下，与 prompts / skills 同级）。
_LIBRARY_DIR_NAME = "strategies"

#: 策略 ID 长度（随机短 ID，与 wid / task_id 同一模式）。
_STRATEGY_ID_LENGTH = 11

#: 显示名长度上限（纯显示别名、允许重名，只挡空与离谱长度）。
_MAX_NAME_CHARS = 100


@dataclass
class LibraryStrategy:
    """一条库策略：组合清单 + 显示元数据。

    Attributes:
        id: 内部稳定 ID（文件名，不随改名变化）。
        name: 显示名（可改、允许重名）。
        description: 说明文字（可空）。
        endpoint_id: 端点配置 ID 引用（显示名的当前值在端点库里现查）。
        prompt_id: 基础提示词 ID 引用。
        skill_ids: 启用 Skill ID 引用清单（有序，注入顺序即此序）。
        created_at / updated_at: UTC ISO 8601 时刻。
    """

    id: str
    name: str
    description: str
    endpoint_id: str
    prompt_id: str
    skill_ids: list[str] = field(default_factory=lambda: list[str]())
    created_at: str = ""
    updated_at: str = ""


def _library_dir() -> Path:
    """库目录：数据根下 ``strategies/``（惰性创建交給写入方）。"""
    return data_root() / _LIBRARY_DIR_NAME


def _entry_path(strategy_id: str) -> Path:
    """一条库策略的落盘路径（ID 直来自 URL 参数，先过形状校验杜绝路径穿越）。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", strategy_id):
        raise StrategyNotFoundError(
            "库策略 ID 形状不合法（只允许字母 / 数字 / 下划线 / 连字符）。",
        )
    return _library_dir() / f"{strategy_id}.json"


def _generate_id() -> str:
    """生成字母开头的随机短 ID，避免 CLI 将它解析为选项。"""
    return "s" + secrets.token_urlsafe(8)[: _STRATEGY_ID_LENGTH - 1]


def _validate_name(name: str) -> str:
    """校验显示名：去首尾空白后非空、不超长；返回规整后的名字。"""
    cleaned = name.strip()
    if not cleaned:
        raise StrategyNameError("策略名不能为空——请给策略起个名字再保存。")
    if len(cleaned) > _MAX_NAME_CHARS:
        raise StrategyNameError(
            f"策略名过长（{len(cleaned)} 字符，上限 {_MAX_NAME_CHARS}）——请缩短后重试。",
        )
    return cleaned


def _read_entry(path: Path) -> LibraryStrategy:
    """读一个库策略文件（损坏 fail loud——坏文件不静默藏起来）。"""
    try:
        raw = path.read_text(encoding="utf-8")
        data: object = json.loads(raw) if raw.strip() else None
    except (json.JSONDecodeError, OSError) as exc:
        raise StrategyError(
            f"库策略文件损坏（{path.name}）——请删除该文件后重建策略。",
        ) from exc
    if not isinstance(data, dict):
        raise StrategyError(
            f"库策略文件形状不对（{path.name}）——请删除该文件后重建策略。",
        )
    record = cast("dict[str, object]", data)
    if "skill_ids" in record:
        skill_ids = [
            _resolve_ref(item, item, _skill_id_by_name, "skills")
            for item in _read_list_field(record, "skill_ids")
        ]
    else:
        skill_ids = [
            _resolve_ref(None, item, _skill_id_by_name, "skills")
            for item in _read_list_field(record, "skills")
        ]
    return LibraryStrategy(
        id=_read_str_field(record, "id"),
        name=_read_str_field(record, "name"),
        description=_read_str_field(record, "description"),
        endpoint_id=_resolve_ref(
            record.get("endpoint_id"),
            record.get("endpoint"),
            _endpoint_id_by_name,
            "endpoint",
        ),
        prompt_id=_resolve_ref(
            record.get("prompt_id"), record.get("prompt"), _prompt_id_by_name, "prompt"
        ),
        skill_ids=skill_ids,
        created_at=_read_str_field(record, "created_at"),
        updated_at=_read_str_field(record, "updated_at"),
    )


def _resolve_ref(
    new_value: object,
    legacy_value: object,
    by_display_name: Callable[[str], str | None],
    field_label: str,
) -> str:
    """取一个引用字段：优先新键（ID）；缺失 = 旧版条目，按显示名解析成 ID。

    旧版名字解析不到（资产已被改名或删除）时**保留旧值原样**——该引用随后在健康度
    现查里显示缺失、走「重新指定」恢复；迁移绝不静默丢弃用户的组合清单。

    Raises:
        StrategyError: 新旧键都没有可用值（文件缺字段，按损坏处理）。
    """
    if isinstance(new_value, str) and new_value:
        return new_value
    if not isinstance(legacy_value, str) or not legacy_value:
        raise StrategyError(
            f"库策略文件缺少引用字段 {field_label}——请删除该文件后重建策略。",
        )
    resolved = by_display_name(legacy_value)
    return resolved if resolved is not None else legacy_value


def _read_str_field(data: dict[str, object], key: str) -> str:
    """取字典里的字符串字段（缺失或类型不对按文件损坏处理）。"""
    value = data.get(key)
    if not isinstance(value, str):
        raise StrategyError(
            f"库策略文件缺少字段 {key} 或类型不对——请删除该文件后重建策略。",
        )
    return value


def _read_list_field(data: dict[str, object], key: str) -> list[object]:
    """取字典里的列表字段（缺失允许、类型不对按文件损坏处理）。"""
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise StrategyError(
            f"库策略文件字段 {key} 类型不对——请删除该文件后重建策略。",
        )
    return list(cast("list[object]", value))


def _write_entry(entry: LibraryStrategy) -> None:
    """原子写一个库策略文件（目录惰性创建）。"""
    _library_dir().mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        _entry_path(entry.id),
        json.dumps(asdict(entry), ensure_ascii=False, indent=2),
    )


def list_strategies() -> list[LibraryStrategy]:
    """列出全部库策略（按显示名排序、同名按 ID 稳定序）。

    损坏文件 fail loud（坏数据不该被列表悄悄藏起来，与端点配置列表同纪律）。
    """
    entries = [
        _read_entry(path) for path in _library_dir().glob("*.json") if path.is_file()
    ]
    entries.sort(key=lambda entry: (entry.name, entry.id))
    return entries


def get_strategy(strategy_id: str) -> LibraryStrategy:
    """按 ID 查库策略；不存在抛 StrategyNotFoundError。"""
    path = _entry_path(strategy_id)
    if not path.is_file():
        raise StrategyNotFoundError(
            f"库策略 {strategy_id} 不存在——可能已被删除，请刷新策略库后重试。",
        )
    return _read_entry(path)


def missing_refs(entry: LibraryStrategy) -> list[str]:
    """现查引用健康度，返回缺失引用的可读描述（空列表 = 健康）。

    读取时现查而非落盘状态：删提示词 / Skill / 端点配置的动作不该反过来
    改写策略库文件（单一事实源是各资产库本身，健康度是派生视图）。
    """
    problems: list[str] = []
    if not _ref_exists(entry.endpoint_id, _endpoint_exists, _endpoint_id_by_name):
        problems.append(f"端点配置「{entry.endpoint_id}」不存在")
    if not _ref_exists(
        entry.prompt_id,
        lambda ref: any(prompt.id == ref for prompt in list_prompts()),
        _prompt_id_by_name,
    ):
        problems.append(f"基础提示词「{entry.prompt_id}」不存在")
    for sid in entry.skill_ids:
        if not _ref_exists(
            sid, lambda ref: any(s.id == ref for s in list_skills()), _skill_id_by_name
        ):
            problems.append(f"Skill「{sid}」不存在")
    return problems


def _ref_exists(
    ref: str,
    by_id: Callable[[str], bool],
    by_display_name: Callable[[str], str | None],
) -> bool:
    """引用健康度的宽容判定：ID 直接命中，或显示名唯一命中（都算存在）。"""
    if by_id(ref):
        return True
    return by_display_name(ref) is not None


def _canonical_endpoint(ref: str) -> str:
    """端点引用（ID 或唯一显示名）→ 规范 ID（落盘引用一律存稳定 ID）。"""
    return _endpoint_info(ref).id


def _canonical_prompt(ref: str) -> str:
    """提示词引用（ID 或唯一显示名）→ 规范 ID。"""
    return _read_prompt(ref).id


def _canonical_skill(ref: str) -> str:
    """skill 引用（ID 或唯一显示名）→ 规范 ID。"""
    return _get_skill(ref).id


def require_refs_exist(endpoint_id: str, prompt_id: str, skill_ids: list[str]) -> None:
    """创建 / 更新 / 应用前的引用存在性校验（fail fast，缺失即 400）。"""
    entry = LibraryStrategy(
        id="",
        name="",
        description="",
        endpoint_id=endpoint_id,
        prompt_id=prompt_id,
        skill_ids=skill_ids,
    )
    problems = missing_refs(entry)
    if problems:
        raise StrategyRefsError(
            "引用的资产不存在："
            + "；".join(problems)
            + "。请先在对应库中创建或改选其他资产。"
        )


def _dedupe_keep_order(names: list[str]) -> list[str]:
    """Skill 引用去重（保序）——重复勾选同一 Skill 没有意义。"""
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def create_strategy(
    name: str,
    endpoint_id: str,
    prompt_id: str,
    skill_ids: list[str],
    description: str = "",
) -> LibraryStrategy:
    """新建一条库策略（ID 随机分配；引用必须现存在）。

    Raises:
        StrategyNameError: 显示名为空或超长。
        StrategyRefsError: 任一引用不存在。
    """
    cleaned = _validate_name(name)
    skill_refs = _dedupe_keep_order(list(skill_ids))
    require_refs_exist(endpoint_id, prompt_id, skill_refs)
    entry = LibraryStrategy(
        id=_generate_id(),
        name=cleaned,
        description=description,
        endpoint_id=_canonical_endpoint(endpoint_id),
        prompt_id=_canonical_prompt(prompt_id),
        skill_ids=[_canonical_skill(sid) for sid in skill_refs],
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    while _entry_path(entry.id).exists():  # ID 撞号重摇（概率极低）
        entry.id = _generate_id()
    _write_entry(entry)
    return entry


def update_strategy(
    strategy_id: str,
    name: str,
    endpoint_id: str,
    prompt_id: str,
    skill_ids: list[str],
    description: str = "",
) -> LibraryStrategy:
    """整条更新库策略（策略页「保存」的落点；引用必须现存在）。"""
    _validate_name(name)
    existing = get_strategy(strategy_id)
    skill_refs = _dedupe_keep_order(list(skill_ids))
    require_refs_exist(endpoint_id, prompt_id, skill_refs)
    existing.name = _validate_name(name)
    existing.description = description
    existing.endpoint_id = _canonical_endpoint(endpoint_id)
    existing.prompt_id = _canonical_prompt(prompt_id)
    existing.skill_ids = [_canonical_skill(sid) for sid in skill_refs]
    existing.updated_at = now_iso()
    _write_entry(existing)
    return existing


def rebind_strategy(
    strategy_id: str,
    endpoint_id: str | None = None,
    prompt_id: str | None = None,
    skill_ids: list[str] | None = None,
) -> LibraryStrategy:
    """重新指定缺失引用（失效处置的「重新指定」动作；只更新提供的引用位）。

    与整条更新（update_strategy）的区别：这里允许只动坏掉的引用位、其余
    保持不变——对置灰策略来说，健康的引用没有理由被 UI 一起重交一遍。
    """
    existing = get_strategy(strategy_id)
    new_endpoint = endpoint_id if endpoint_id is not None else existing.endpoint_id
    new_prompt = prompt_id if prompt_id is not None else existing.prompt_id
    new_skills = (
        _dedupe_keep_order(list(skill_ids))
        if skill_ids is not None
        else existing.skill_ids
    )
    require_refs_exist(new_endpoint, new_prompt, new_skills)
    existing.endpoint_id = _canonical_endpoint(new_endpoint)
    existing.prompt_id = _canonical_prompt(new_prompt)
    existing.skill_ids = [_canonical_skill(sid) for sid in new_skills]
    existing.updated_at = now_iso()
    _write_entry(existing)
    return existing


def copy_strategy(strategy_id: str) -> LibraryStrategy:
    """复制一份（派生变体：新 ID，内容原样；允许重名所以名字不变）。"""
    source = get_strategy(strategy_id)
    clone = LibraryStrategy(
        id=_generate_id(),
        name=source.name,
        description=source.description,
        endpoint_id=source.endpoint_id,
        prompt_id=source.prompt_id,
        skill_ids=list(source.skill_ids),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    while _entry_path(clone.id).exists():
        clone.id = _generate_id()
    _write_entry(clone)
    return clone


def delete_strategy(strategy_id: str) -> None:
    """删除一条库策略；不存在抛 StrategyNotFoundError。

    已应用到工作目录的批次不受影响（copy-on-apply，批次持有内容副本）。
    """
    path = _entry_path(strategy_id)
    if not path.is_file():
        raise StrategyNotFoundError(
            f"库策略 {strategy_id} 不存在——可能已被删除，请刷新策略库后重试。",
        )
    path.unlink()


def strategy_content_hash(entry: LibraryStrategy) -> str:
    """库策略内容的规范哈希（apply 时刻记进快照来源，「从库更新」比对用）。

    哈希对象 = 组合清单的规范 JSON（键排序、去显示元数据——名字改了不算
    内容变了，组合才是可复现的实质）。
    """
    payload = {
        "endpoint_id": entry.endpoint_id,
        "prompt_id": entry.prompt_id,
        "skill_ids": entry.skill_ids,
    }
    return canonical_sha256(payload)
