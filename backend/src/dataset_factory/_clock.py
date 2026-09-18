"""全仓共用的「当前 UTC 时刻」出口：只有两种记录格式，都收在这一个模块里。

时间戳会写进 ``state.json`` / ``run.json`` / 导入记录 / 会话事件流，前端与对账脚本都按 ISO
解析，格式一旦各写各的就会漂（同名字段一处带微秒、一处只到秒，排序与比对都会静默失真）。
所以取时钟这件事只留这一处，各域按用途挑函数、不自定义格式。

目录名用的时间戳（``runs`` 的 ``%Y%m%dT%H%M%SZ``、``sessions`` 的 ``%Y%m%d-%H%M%S-%f``）不在
这里：它们的精度与防撞策略是各自 id 方案的一部分，跟着各自的 ``_new_*_id`` 走更清楚。
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["now_iso", "now_log_stamp"]


def now_iso() -> str:
    """当前 UTC 时刻的 ISO 8601 串（进状态文件、导入记录与会话事件流）。"""
    return datetime.now(UTC).isoformat()


def now_log_stamp() -> str:
    """当前 UTC 时刻的秒精度 ISO 串（``run.log`` 行首时间戳）。

    日志比记录更在意可读性：人在终端里看的是「什么时候失败」，微秒只会把行首撑长。
    """
    return datetime.now(UTC).isoformat(timespec="seconds")
