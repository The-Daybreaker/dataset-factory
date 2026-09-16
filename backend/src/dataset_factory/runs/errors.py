"""runs 域的异常类型：类型化 + 可操作消息；problem+json 映射关系写在各类 docstring。

素材级失败（模型报错 / 素材读不出）不是异常——它们是运行的正常结果之一，逐条记进
运行流水；这里只放「运行根本没跑起来 / 跑不下去」的结构性错误。
"""

from __future__ import annotations

from typing import Any


class RunError(Exception):
    """runs 域错误的基类；消息只描述「哪里错、怎么修」。"""


class RunOccupiedError(RunError):
    """同一工作目录已有跑批在运行（运行锁被占用）——HTTP 409 problem+json（run-occupied）。

    Attributes:
        occupier: 占用者信息（pid / started_at / hostname / batch）；残留信息损坏时
            为 None，此时只给笼统提示。
    """

    def __init__(self, message: str, *, occupier: dict[str, Any] | None = None) -> None:
        """带占用者信息构造（occupier 供 problem+json 扩展字段与界面提示）。"""
        super().__init__(message)
        self.occupier = occupier


class BatchInactiveError(RunError):
    """批次处于停用（隐藏）状态，不允许启动跑批——HTTP 409 problem+json（batch-inactive）。"""


class RunJournalCorruptedError(RunError):
    """历史运行流水（items.jsonl）损坏——HTTP 500 problem+json（run-journal-corrupted）。

    续跑判定要读历史流水取「最近一次成功打标的素材哈希」；坏行 fail loud，
    用户可用「清理运行记录」移除损坏的那次运行后重试。
    """
