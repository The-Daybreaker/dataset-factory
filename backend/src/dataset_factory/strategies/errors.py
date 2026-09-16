"""strategies 数据域的异常类型：只抛类型化异常，消息可操作、fail loud、不甩原始栈。

异常 → problem+json type slug 的对应关系写在本模块各类的 docstring 里
（映射落 api/app.py 的错误处理器）。
"""

from __future__ import annotations


class StrategyError(Exception):
    """策略域错误的基类；消息只描述「哪里错、怎么修」。"""


class StrategyNotFoundError(StrategyError):
    """库策略 ID 不存在——HTTP 404 problem+json（strategy-not-found）。"""


class StrategyNameError(StrategyError):
    """策略显示名不合法（空、超长）——HTTP 400 problem+json（strategy-name-invalid）。"""


class StrategyRefsError(StrategyError):
    """策略引用的资产（端点配置 / 提示词 / Skill）不存在——HTTP 400 problem+json（strategy-refs-invalid）。

    创建 / 更新时要求引用现存在（引用缺失的策略没有意义）；引用在创建后
    被删走「置灰 + 重新指定」路径（健康度判定），不在本异常管。
    """


class BatchNotFoundError(StrategyError):
    """批次序号不存在（或 sN 格式不合法）——HTTP 404 problem+json（batch-not-found）。"""
