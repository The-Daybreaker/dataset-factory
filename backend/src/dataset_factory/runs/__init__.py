"""runs 编排层：跑批执行器 + 条目视图读模型。

对外接口：
- 执行器：BatchRunner（run 跑一批 / stop 协作停止 / subscribe 订阅事件）
- 报告与事件：RunReport / RunStartedEvent / ItemUpdatedEvent / RunFinishedEvent
- 条目视图（打标页左列六分组的读模型，状态不落库、每次现算）：build_item_view /
  ItemView / ItemRow / 六分组键
- 运行流水回读：load_recent_success_hashes（续跑跳过判定的哈希锚点）/
  load_latest_item_records（每条素材最近一次尝试的结果）
- 重试列表（state.json 的 retry_list 键，结构由本域定义）：read_retry_list；
  可重试类原因码清单 RETRYABLE_REASON_CODES（F5 两类清单的单一事实源）
- 异常：RunError 基类 + RunOccupiedError（带占用者信息）/ BatchInactiveError /
  RunJournalCorruptedError（下游各域异常直接冒泡，见 errors 模块说明）

依赖方向（design「模块归属」）：runs → workdir（经 WorkdirStore 读写 ``.dsf/``、
经 assets 解析素材与产物）、strategies（读快照与批次元数据）、labeling（逐条调用
纯素材路径）、llm（异常分类）。
"""

from .errors import (
    BatchInactiveError,
    RunError,
    RunJournalCorruptedError,
    RunNotActiveError,
    RunOccupiedError,
)
from .items import (
    GROUP_DONE,
    GROUP_FAILED,
    GROUP_MISSING,
    GROUP_QUEUED,
    GROUP_RETRY,
    GROUP_UNIMPORTED,
    ITEM_GROUPS,
    ItemRow,
    ItemView,
    build_item_view,
)
from .journal import (
    ItemRecord,
    RunJournal,
    load_latest_item_records,
    load_recent_success_hashes,
)
from .lock import RunLock, read_occupier
from .runner import (
    RETRYABLE_REASON_CODES,
    BatchRunner,
    ItemUpdatedEvent,
    RunEvent,
    RunFinishedEvent,
    RunMode,
    RunReport,
    RunStartedEvent,
    RunTrigger,
    completer_for_snapshot,
    read_retry_list,
)

__all__ = [
    "GROUP_DONE",
    "GROUP_FAILED",
    "GROUP_MISSING",
    "GROUP_QUEUED",
    "GROUP_RETRY",
    "GROUP_UNIMPORTED",
    "ITEM_GROUPS",
    "RETRYABLE_REASON_CODES",
    "BatchInactiveError",
    "BatchRunner",
    "ItemRecord",
    "ItemRow",
    "ItemUpdatedEvent",
    "ItemView",
    "RunError",
    "RunEvent",
    "RunFinishedEvent",
    "RunJournal",
    "RunJournalCorruptedError",
    "RunLock",
    "RunMode",
    "RunNotActiveError",
    "RunOccupiedError",
    "RunReport",
    "RunStartedEvent",
    "RunTrigger",
    "build_item_view",
    "completer_for_snapshot",
    "load_latest_item_records",
    "load_recent_success_hashes",
    "read_occupier",
    "read_retry_list",
]
