"""策略快照：把组合清单在「应用时刻」装配成内容全文（copy-on-apply 的落点）。

快照 = 批次自包含的根基（design「策略快照与多策略并存」）：存完整内容副本
不存引用——库里的提示词 / Skill 之后被编辑或删除，本批照样按快照重跑。
行业口径同此（可复现性要求 the full config, not a summary）。

结构（design 定案）：端点（ID / 显示名 / base_url / 模型名 / 生成参数）+ 基础提示词
全文 + 启用 Skill 全文 + 各自内容哈希 + 快照时间 + 工具版本号；库应用来源
（库 ID + 应用时刻组合哈希）由批次侧附加，供将来的「从库更新」比对。

身份口径（2026-09-23 ID 化）：端点 / 提示词 / Skill 块各冻结一个 `id`（运行期凭据
密钥解析按它定位，**改名不影响任何已建批次**）；`name` 是显示名的展示性存档。
旧版快照（无 id、只有旧配置名）由运行侧按名兜底解析。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from .._fs import canonical_sha256
from ..llm.endpoints import (
    SUPPORTED_API_FORMAT,
    config_info,
    read_config_data,
    validated_request_params,
)
from ..prompts.store import read_prompt
from ..skills.store import get_skill, read_skill

__all__ = ["StrategySnapshot", "build_snapshot", "tool_version"]


def tool_version() -> str:
    """工具版本号（可编辑安装也能查到发行元数据；查不到降级 unknown）。

    快照装配与运行日志（run.json 的 dsf_version）共用——同一份「工具出身」口径。
    """
    try:
        return version("dataset-factory")
    except PackageNotFoundError:
        return "unknown"


def _sha256_text(text: str) -> str:
    """文本内容哈希（UTF-8 字节的 SHA-256）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class StrategySnapshot:
    """一套策略的应用时刻全文快照。

    Attributes:
        endpoint: 端点块（id / name / base_url / model / api_format / request_params /
            sha256）。id 是密钥解析的稳定键；name 是显示名的展示性存档。
        prompt: 基础提示词块（id / name / body / sha256）。
        skills: 启用 Skill 块清单（id / name / body / sha256，序 = 注入序）。
        built_at: 快照装配时刻（UTC ISO 8601）。
        tool_version: 装配时的工具版本号。
        source: 库应用来源（strategy_id + strategy_sha256）；从零配置的批次为 None。
    """

    endpoint: dict[str, Any]
    prompt: dict[str, Any]
    skills: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    built_at: str = ""
    tool_version: str = ""
    source: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        """序列化为落盘 JSON（人可读、原子写由调用方负责）。"""
        return asdict(self)

    @classmethod
    def from_json(cls, data: object) -> StrategySnapshot:
        """从落盘 JSON 还原（形状不对 fail loud，抛 ValueError 由调用方翻译）。"""
        if not isinstance(data, dict):
            # ValueError 而非 TypeError：语义是「落盘内容损坏」，调用方按损坏翻译。
            raise ValueError("快照文件形状不对：顶层不是对象")  # noqa: TRY004
        record = cast("dict[str, Any]", data)
        try:
            return cls(
                endpoint=cast("dict[str, Any]", record["endpoint"]),
                prompt=cast("dict[str, Any]", record["prompt"]),
                skills=[
                    cast("dict[str, Any]", item)
                    for item in cast("list[object]", record["skills"])
                ],
                built_at=str(record["built_at"]),
                tool_version=str(record["tool_version"]),
                source=cast("dict[str, Any] | None", record.get("source")),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"快照文件缺少字段或类型不对：{exc}") from exc


def build_snapshot(
    endpoint_id: str,
    prompt_id: str,
    skill_ids: list[str],
    *,
    built_at: str,
    source: dict[str, Any] | None = None,
) -> StrategySnapshot:
    """把组合清单装配成快照：现读端点配置 / 提示词正文 / Skill 全文并记哈希。

    Args:
        endpoint_id: 端点配置 ID（endpoints/<id>/）。
        prompt_id: 基础提示词 ID。
        skill_ids: 启用 Skill ID 清单（有序）。
        built_at: 快照时刻（UTC ISO，由调用方传入以对齐同批其他时间戳）。
        source: 库应用来源；从零配置为 None。

    Raises:
        ConfigError: 端点配置缺失或损坏。
        PromptNotFoundError: 提示词不存在。
        SkillNotFoundError: Skill 不存在。
    """
    config = read_config_data(endpoint_id)
    endpoint_info = config_info(endpoint_id)
    endpoint_id = endpoint_info.id  # 入参可能是唯一显示名：落快照前规范化为稳定 ID。
    endpoint_display = endpoint_info.name
    endpoint_block: dict[str, Any] = {
        "id": endpoint_id,
        "name": endpoint_display,
        "base_url": config["base_url"],
        "model": config["model"],
        "api_format": str(config.get("api_format") or SUPPORTED_API_FORMAT),
        "request_params": validated_request_params(config, endpoint_display),
    }
    endpoint_block["sha256"] = canonical_sha256(
        {
            key: endpoint_block[key]
            for key in ("base_url", "model", "api_format", "request_params")
        }
    )
    prompt = read_prompt(prompt_id)
    prompt_id = prompt.id
    prompt_block: dict[str, Any] = {
        "id": prompt_id,
        "name": prompt.name,
        "body": prompt.body,
        "sha256": _sha256_text(prompt.body),
    }
    skill_blocks: list[dict[str, Any]] = []
    for sid in skill_ids:
        meta = get_skill(sid)
        body = read_skill(sid)
        skill_blocks.append(
            {
                "id": sid,
                "name": meta.name,
                "body": body,
                "sha256": _sha256_text(body),
            },
        )
    return StrategySnapshot(
        endpoint=endpoint_block,
        prompt=prompt_block,
        skills=skill_blocks,
        built_at=built_at,
        tool_version=tool_version(),
        source=source,
    )
