"""skills 数据域的异常类型：只抛类型化异常，消息可操作、fail loud、不甩原始栈。"""

from __future__ import annotations


class SkillError(Exception):
    """Skill 库操作错误的基类；消息只描述「哪里错、怎么修」。"""


class SkillNameError(SkillError):
    """skill 名称不合法（空、含路径分隔符 / 控制字符、以点或下划线开头等）。"""


class SkillNotFoundError(SkillError):
    """按名称找不到 skill（读 / 启用停用 / 删不存在的 skill）。"""


class SkillExistsError(SkillError):
    """导入时库里已有同名 skill——重名不合并、不静默覆盖，交给用户先删旧的或改 name。"""


class SkillFormatError(SkillError):
    """skill 包格式非法（缺 SKILL.md、frontmatter 缺失 / 未闭合 / 非法 YAML / 缺 name 或 description、非 UTF-8）。"""
