"""llm 的异常类型：能力层对外只抛类型化异常，不甩 SDK 原始栈、不泄密钥。"""

from __future__ import annotations


class LLMError(Exception):
    """llm 调用相关错误的基类；消息只描述「哪里错、怎么修」，绝不含密钥内容。"""
