"""runs 域的异常类型：类型化 + 可操作消息；problem+json 映射关系写在各类 docstring。

素材级失败（模型报错 / 素材读不出）不是异常——它们是运行的正常结果之一，逐条记进
运行流水；这里只放「运行根本没跑起来 / 跑不下去」的结构性错误。运行锁占用
（run-occupied）归 workdir 域（锁原语在 workdir.locks）。
"""

from __future__ import annotations


class RunError(Exception):
    """runs 域错误的基类；消息只描述「哪里错、怎么修」。"""


class RunNotActiveError(RunError):
    """该批次当前没有进行中的跑批（current / stop / stream 找不到运行）。

    HTTP 404 problem+json（run-not-active）。
    """


class BatchInactiveError(RunError):
    """批次处于停用（隐藏）状态，不允许启动跑批——HTTP 409 problem+json（batch-inactive）。"""


class RunJournalCorruptedError(RunError):
    """历史运行流水（items.jsonl）损坏——HTTP 500 problem+json（run-journal-corrupted）。

    续跑判定要读历史流水取「最近一次成功打标的素材哈希」；坏行 fail loud，
    用户可用「清理运行记录」移除损坏的那次运行后重试。
    """
