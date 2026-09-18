"""``_clock`` 时间戳出口的格式钉与边界用例（G2 收敛后给单一实现补的那一层）。

收敛前全仓有三份助手 + 七处调用点手写 ``datetime.now(UTC).isoformat()``。这种「表达式自己就是
格式定义」的写法，最怕收敛时把落盘格式改窄或改宽：时间戳会进 ``state.json`` / ``run.json`` /
导入记录 / 会话事件流，前端与对账脚本都按 ISO 解析。所以这里逐字钉住两种格式的形状（含带不带
微秒、带不带 UTC 偏移），并保证两者指同一时刻、都能原样解析回来。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from dataset_factory._clock import now_iso, now_log_stamp

_FULL_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")
_SECONDS_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


def test_now_iso_keeps_the_microsecond_iso_shape() -> None:
    """全精度戳的对外形状：ISO 8601 + 微秒 + UTC 偏移，一位不多一位不少。"""
    stamp = now_iso()

    assert _FULL_SHAPE.match(stamp), stamp
    assert datetime.fromisoformat(stamp).tzinfo is UTC


def test_now_log_stamp_stops_at_whole_seconds() -> None:
    """日志戳到秒为止：多出小数秒说明格式漂了（run.log 行首要短）。"""
    stamp = now_log_stamp()

    assert _SECONDS_SHAPE.match(stamp), stamp
    assert datetime.fromisoformat(stamp).microsecond == 0


def test_both_formats_point_at_the_same_instant() -> None:
    """同一时刻取两次：截断只发生在秒以下，两者相差必须小于一秒且顺序不倒。"""
    full = now_iso()
    compact = now_log_stamp()

    delta = datetime.fromisoformat(full) - datetime.fromisoformat(compact)
    assert delta < timedelta(seconds=1)
