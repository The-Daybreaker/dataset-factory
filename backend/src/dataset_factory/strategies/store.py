"""用户级策略库（数据域，``~/.dataset_factory/strategies/``，与 prompts / skills 平级）。

策略 = **组合清单**：引用端点配置、基础提示词与启用 Skill 三样独立资产
（引用而非内联——资产各有编辑页，改一处影响所有引用它的策略）。库里不编号，
身份 = 内部稳定 ID（随机短 ID，文件名即 ID）；显示名可改、允许重名。

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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .._fs import atomic_write_text, canonical_sha256, data_root
from ..llm.endpoints import has_config as _endpoint_exists
from ..prompts.store import list_prompts
from ..skills.store import list_skills
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
        endpoint: 端点配置名引用（endpoints/<name>/ 的目录名）。
        prompt: 基础提示词名引用。
        skills: 启用 Skill 名引用清单（有序，注入顺序即此序）。
        created_at / updated_at: UTC ISO 8601 时刻。
    """

    id: str
    name: str
    description: str
    endpoint: str
    prompt: str
    skills: list[str] = field(default_factory=lambda: list[str]())
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


def _now_iso() -> str:
    """当前 UTC 时刻（ISO 8601，与导入记录同一时刻格式）。"""
    return datetime.now(UTC).isoformat()


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
    return LibraryStrategy(
        id=_read_str_field(record, "id"),
        name=_read_str_field(record, "name"),
        description=_read_str_field(record, "description"),
        endpoint=_read_str_field(record, "endpoint"),
        prompt=_read_str_field(record, "prompt"),
        skills=[str(item) for item in _read_list_field(record, "skills")],
        created_at=_read_str_field(record, "created_at"),
        updated_at=_read_str_field(record, "updated_at"),
    )


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
    if not _endpoint_exists(entry.endpoint):
        problems.append(f"端点配置「{entry.endpoint}」不存在")
    if all(prompt.name != entry.prompt for prompt in list_prompts()):
        problems.append(f"基础提示词「{entry.prompt}」不存在")
    skill_names = {skill.name for skill in list_skills()}
    for name in entry.skills:
        if name not in skill_names:
            problems.append(f"Skill「{name}」不存在")
    return problems


def require_refs_exist(endpoint: str, prompt: str, skills: list[str]) -> None:
    """创建 / 更新 / 应用前的引用存在性校验（fail fast，缺失即 400）。"""
    entry = LibraryStrategy(
        id="", name="", description="", endpoint=endpoint, prompt=prompt, skills=skills
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
    endpoint: str,
    prompt: str,
    skills: list[str],
    description: str = "",
) -> LibraryStrategy:
    """新建一条库策略（ID 随机分配；引用必须现存在）。

    Raises:
        StrategyNameError: 显示名为空或超长。
        StrategyRefsError: 任一引用不存在。
    """
    cleaned = _validate_name(name)
    skill_refs = _dedupe_keep_order(list(skills))
    require_refs_exist(endpoint, prompt, skill_refs)
    entry = LibraryStrategy(
        id=_generate_id(),
        name=cleaned,
        description=description,
        endpoint=endpoint,
        prompt=prompt,
        skills=skill_refs,
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    while _entry_path(entry.id).exists():  # ID 撞号重摇（概率极低）
        entry.id = _generate_id()
    _write_entry(entry)
    return entry


def update_strategy(
    strategy_id: str,
    name: str,
    endpoint: str,
    prompt: str,
    skills: list[str],
    description: str = "",
) -> LibraryStrategy:
    """整条更新库策略（策略页「保存」的落点；引用必须现存在）。"""
    _validate_name(name)
    existing = get_strategy(strategy_id)
    skill_refs = _dedupe_keep_order(list(skills))
    require_refs_exist(endpoint, prompt, skill_refs)
    existing.name = _validate_name(name)
    existing.description = description
    existing.endpoint = endpoint
    existing.prompt = prompt
    existing.skills = skill_refs
    existing.updated_at = _now_iso()
    _write_entry(existing)
    return existing


def rebind_strategy(
    strategy_id: str,
    endpoint: str | None = None,
    prompt: str | None = None,
    skills: list[str] | None = None,
) -> LibraryStrategy:
    """重新指定缺失引用（失效处置的「重新指定」动作；只更新提供的引用位）。

    与整条更新（update_strategy）的区别：这里允许只动坏掉的引用位、其余
    保持不变——对置灰策略来说，健康的引用没有理由被 UI 一起重交一遍。
    """
    existing = get_strategy(strategy_id)
    new_endpoint = endpoint if endpoint is not None else existing.endpoint
    new_prompt = prompt if prompt is not None else existing.prompt
    new_skills = (
        _dedupe_keep_order(list(skills)) if skills is not None else existing.skills
    )
    require_refs_exist(new_endpoint, new_prompt, new_skills)
    existing.endpoint = new_endpoint
    existing.prompt = new_prompt
    existing.skills = new_skills
    existing.updated_at = _now_iso()
    _write_entry(existing)
    return existing


def copy_strategy(strategy_id: str) -> LibraryStrategy:
    """复制一份（派生变体：新 ID，内容原样；允许重名所以名字不变）。"""
    source = get_strategy(strategy_id)
    clone = LibraryStrategy(
        id=_generate_id(),
        name=source.name,
        description=source.description,
        endpoint=source.endpoint,
        prompt=source.prompt,
        skills=list(source.skills),
        created_at=_now_iso(),
        updated_at=_now_iso(),
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
        "endpoint": entry.endpoint,
        "prompt": entry.prompt,
        "skills": entry.skills,
    }
    return canonical_sha256(payload)
