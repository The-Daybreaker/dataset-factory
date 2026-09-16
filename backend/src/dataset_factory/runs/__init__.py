"""runs 编排层：跑批执行器——持运行锁串行逐条打标，产出三件套与业务事件流。

对外接口：
- 执行器：BatchRunner（run 跑一批 / stop 协作停止 / subscribe 订阅事件）
- 报告与事件：RunReport / RunStartedEvent / ItemUpdatedEvent / RunFinishedEvent
- 重试列表（state.json 的 retry_list 键，结构由本域定义）：read_retry_list
- 异常：RunError 基类 + RunOccupiedError（带占用者信息）/ BatchInactiveError /
  RunJournalCorruptedError（下游各域异常直接冒泡，见 errors 模块说明）

依赖方向（design「模块归属」）：runs → workdir（经 WorkdirStore 读写 ``.dsf/``）、
strategies（读快照与批次元数据）、labeling（逐条调用纯素材路径）、llm（异常分类）。
"""

from .errors import (
    BatchInactiveError,
    RunError,
    RunJournalCorruptedError,
    RunOccupiedError,
)
from .journal import RunJournal, load_recent_success_hashes
from .lock import RunLock, read_occupier
from .runner import (
    BatchRunner,
    ItemUpdatedEvent,
    RunEvent,
    RunFinishedEvent,
    RunMode,
    RunReport,
    RunStartedEvent,
    RunTrigger,
    read_retry_list,
)

__all__ = [
    "BatchInactiveError",
    "BatchRunner",
    "ItemUpdatedEvent",
    "RunError",
    "RunEvent",
    "RunFinishedEvent",
    "RunJournal",
    "RunJournalCorruptedError",
    "RunLock",
    "RunMode",
    "RunOccupiedError",
    "RunReport",
    "RunStartedEvent",
    "RunTrigger",
    "load_recent_success_hashes",
    "read_occupier",
    "read_retry_list",
]
