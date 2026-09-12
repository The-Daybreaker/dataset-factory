"""prompts 数据域的异常类型：只抛类型化异常，消息可操作、fail loud、不甩原始栈。"""

from __future__ import annotations


class PromptError(Exception):
    """提示词库操作错误的基类；消息只描述「哪里错、怎么修」。"""


class PromptNameError(PromptError):
    """提示词名称不合法（空、含路径分隔符 / 控制字符 / 点号、保留名 _history 等）。"""


class PromptExistsError(PromptError):
    """目标名称的提示词已存在（如改名撞名）——HTTP 409。"""


class PromptNotFoundError(PromptError):
    """按名称找不到提示词条目（读 / 删不存在的条目）。"""


class PromptParseError(PromptError):
    """提示词文件损坏或格式非法（frontmatter 未闭合、YAML 不合法、含未知字段、非 UTF-8）。"""


class PromptTooLargeError(PromptError):
    """提示词条目序列化后超过字节上限（32 KiB）。"""
