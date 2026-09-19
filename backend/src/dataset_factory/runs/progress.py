"""跑批状态的取值域与终态判定（`run.json`、进度快照与 SSE 共用同一套词）。

为什么单独一个模块：这套词有五个书写点（执行器写状态、跨进程读进度、HTTP 判终态、
CLI 判中断），而它们说的是同一件事——一次跑批处在哪个阶段。散着写的后果是加一个状态时
漏掉某处判定：例如新加一个终态却没进「终态集合」，SSE 订阅就会永远等不到收尾帧。
`tasks/` 里那套「长任务」状态（running / succeeded / ...）是**另一个**状态机（导入、导出
等异步任务的登记簿），与本模块的取值域无交集，故不合并。
"""

from __future__ import annotations

from typing import Final

#: 已受理、后台线程尚未跑起来（只出现在进度快照里，不落 `run.json`）。
RUN_STATUS_PENDING: Final = "pending"
#: 正在跑（`run.json` 骨架写入的值）。
RUN_STATUS_RUNNING: Final = "running"
#: 正常收尾。
RUN_STATUS_COMPLETED: Final = "completed"
#: 收到停止请求或批次被停用后停下。
RUN_STATUS_INTERRUPTED: Final = "interrupted"
#: 没能启动（跨进程锁被占、快照在启动前被删等）。
RUN_STATUS_FAILED: Final = "failed"

#: 不会再自行变化的状态：读到它就等于「这个批次当前没有进行中的跑批」。
TERMINAL_RUN_STATUSES: Final = frozenset(
    {RUN_STATUS_COMPLETED, RUN_STATUS_INTERRUPTED, RUN_STATUS_FAILED}
)
