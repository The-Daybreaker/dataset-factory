"""runs 编排层：跑批执行器 + 条目视图读模型。

对外接口：
- 执行器：BatchRunner（run 跑一批 / stop 协作停止 / subscribe 订阅事件）
- 报告与事件：RunReport / RunStartedEvent / ItemUpdatedEvent / RunFinishedEvent
- 条目视图（打标页左列六分组的读模型，状态不落库、每次现算）：build_item_view /
  ItemView / ItemRow / 六分组键
- 运行流水回读：load_recent_success_hashes（续跑跳过判定的哈希锚点）/
  load_latest_item_records（每条素材最近一次尝试的结果）
- 重试列表（state.json 的 retry_list 键，结构由本域定义）：read / add / remove /
  clear（加入的资格判定 retry_rejections 用条目视图现算，PRD F7）；可重试类原因码
  清单 RETRYABLE_REASON_CODES（F5 两类清单的单一事实源）
- 跑批状态取值域（`run.json` / 进度快照 / SSE 共用一套词）：RUN_STATUS_RUNNING /
  RUN_STATUS_INTERRUPTED / TERMINAL_RUN_STATUSES（终态判定只此一处）
- 异常：RunError 基类 + BatchInactiveError / RunNotActiveError / RunJournalCorruptedError /
  RetryItemNotEligibleError（运行锁与状态锁原语、run-occupied 占用异常都在 workdir 域，
  经 workdir.locks 取用）

依赖方向（design「模块归属」）：runs → workdir（经 WorkdirStore 读写 ``.dsf/``、
经 assets 解析素材与产物、经 locks 取两把锁原语）、strategies（读快照与批次元数据）、
labeling（逐条调用纯素材路径）、llm（异常分类）。
"""

from .errors import (
    BatchInactiveError,
    RetryItemNotEligibleError,
    RunError,
    RunJournalCorruptedError,
    RunNotActiveError,
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
    retry_rejections,
)
from .journal import (
    ItemRecord,
    RunJournal,
    load_latest_item_records,
    load_recent_success_hashes,
)
from .progress import (
    RUN_STATUS_INTERRUPTED,
    RUN_STATUS_RUNNING,
    TERMINAL_RUN_STATUSES,
)
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
    add_retry_items,
    clear_retry_list,
    completer_for_snapshot,
    read_retry_list,
    remove_retry_items,
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
    "RUN_STATUS_INTERRUPTED",
    "RUN_STATUS_RUNNING",
    "TERMINAL_RUN_STATUSES",
    "BatchInactiveError",
    "BatchRunner",
    "ItemRecord",
    "ItemRow",
    "ItemUpdatedEvent",
    "ItemView",
    "RetryItemNotEligibleError",
    "RunError",
    "RunEvent",
    "RunFinishedEvent",
    "RunJournal",
    "RunJournalCorruptedError",
    "RunMode",
    "RunNotActiveError",
    "RunReport",
    "RunStartedEvent",
    "RunTrigger",
    "add_retry_items",
    "build_item_view",
    "clear_retry_list",
    "completer_for_snapshot",
    "load_latest_item_records",
    "load_recent_success_hashes",
    "read_retry_list",
    "remove_retry_items",
    "retry_rejections",
]
