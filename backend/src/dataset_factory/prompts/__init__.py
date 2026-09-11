"""prompts 数据域：预置提示词库的增删改查与滚动备份（全项目只有本模块碰提示词文件）。

对外接口：
- 模型与序列化：Prompt / dump_prompt / parse_prompt（md + frontmatter 磁盘格式的唯一事实来源）
- CRUD：list_prompts / read_prompt / save_prompt / delete_prompt
- 异常：PromptError 基类 + PromptNameError / PromptNotFoundError / PromptParseError / PromptTooLargeError
"""

from .errors import (
    PromptError,
    PromptNameError,
    PromptNotFoundError,
    PromptParseError,
    PromptTooLargeError,
)
from .model import Prompt, dump_prompt, parse_prompt
from .store import delete_prompt, list_prompts, read_prompt, save_prompt

__all__ = [
    "Prompt",
    "PromptError",
    "PromptNameError",
    "PromptNotFoundError",
    "PromptParseError",
    "PromptTooLargeError",
    "delete_prompt",
    "dump_prompt",
    "list_prompts",
    "parse_prompt",
    "read_prompt",
    "save_prompt",
]
