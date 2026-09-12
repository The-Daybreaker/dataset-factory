"""Skill 的数据模型 + SKILL.md frontmatter 解析（agentskills.io 开放标准）。

Skill 包按 agentskills.io 标准落盘（`<name>/SKILL.md` + references/ 等），本模块只**读**它的
frontmatter 取 name + description（外部标准，宽容多余字段如 license / metadata，但 name 与
description 必填、缺失即 fail loud）；skill 目录整体原样保存，不改写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import yaml

from .errors import SkillFormatError

_DELIMITER = "---"


@dataclass(frozen=True)
class Skill:
    """一个已导入的 skill。

    Attributes:
        name: skill 名称（取自 SKILL.md frontmatter，也是库里的目录名）。
        description: 用途说明（取自 frontmatter，供选择器展示）。
        enabled: 库级启用状态；停用的 skill 保留在库里、但不供打标注入（停用不删除）。
    """

    name: str
    description: str
    enabled: bool


@dataclass(frozen=True)
class SkillImport:
    """一次导入的结果。

    Attributes:
        skill: 导入后的 skill（默认启用）。
        total_bytes: 整包字节数，供入口层给「体积提示」（skill 全量注入、不设字节上限）。
    """

    skill: Skill
    total_bytes: int


# 包内文件角色：skill=注入源（SKILL.md）；reference=参考资料（供查阅、不自动注入）；
# asset / script=包资产与脚本；other=其他文件。除前两者外均不参与注入。
SkillFileRole = Literal["skill", "reference", "asset", "script", "other"]


@dataclass(frozen=True)
class SkillFileEntry:
    """技能包内一个文件的条目（包内容预览用）。

    Attributes:
        path: 包内相对路径（POSIX 风格，如 ``SKILL.md``、``references/h3.md``）。
        role: 文件角色（见 SkillFileRole）。
        previewable: 是否可通过 read_skill_file 预览内容——仅 SKILL.md 与 references/
            下文件开放（注入范围成文见 design「技能双栏模式」）。
    """

    path: str
    role: SkillFileRole
    previewable: bool


def parse_skill_frontmatter(text: str) -> tuple[str, str]:
    """从 SKILL.md 全文解析 frontmatter 的 name 与 description。

    agentskills.io 要求 SKILL.md 以 YAML frontmatter 开头、含 name 与 description；其余字段
    （license / metadata 等）宽容忽略。与本项目自有的提示词格式相反：这里是**外部标准**，
    多余字段合法、不报错，但缺 name / description 即 fail loud。

    Args:
        text: SKILL.md 全文。

    Returns:
        (name, description) 两个字符串。

    Raises:
        SkillFormatError: 缺 frontmatter / 未闭合 / 非法 YAML / 顶层非映射 / 缺 name 或 description。
    """
    if not text.startswith(f"{_DELIMITER}\n"):
        raise SkillFormatError(
            f"SKILL.md 缺少 YAML frontmatter（应以单独一行 {_DELIMITER} 开头）；"
            "不是合法的 agentskills.io skill 包。"
        )
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i] == _DELIMITER), None)
    if end is None:
        raise SkillFormatError(
            "SKILL.md 的 frontmatter 未闭合（开头有 ---，但找不到结束的 ---）。"
        )
    front_text = "\n".join(lines[1:end])
    try:
        loaded: object = yaml.safe_load(front_text)
    except yaml.YAMLError as exc:
        raise SkillFormatError(
            "SKILL.md 的 frontmatter 不是合法 YAML；请检查语法。"
        ) from exc
    if not isinstance(loaded, dict):
        raise SkillFormatError(
            "SKILL.md 的 frontmatter 顶层应是键值映射（含 name / description）。"
        )
    meta = cast(dict[object, object], loaded)
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SkillFormatError("SKILL.md 的 frontmatter 缺少非空的 name 字段。")
    description = meta.get("description")
    if not isinstance(description, str):
        raise SkillFormatError(
            "SKILL.md 的 frontmatter 缺少 description 字段（应为字符串）。"
        )
    return name, description
