"""labeling 编排层：打标引擎——把「基础提示词 + skill + 图 + 历史」拼成一次模型请求。

对外接口：
- 引擎：LabelingEngine（label 一轮打标 / restore 恢复会话）
- 结果与快照：LabelResult / SessionSnapshot / SessionSettings / HistoryMessage
- 异常：LabelingError 基类 + PromptNotSelectedError / EmptyTurnError /
  AttachmentReadError / SettingsFormatError（下游各域异常直接冒泡，见 errors 模块说明）
"""

from .engine import (
    HistoryMessage,
    LabelingEngine,
    LabelResult,
    SessionSettings,
    SessionSnapshot,
    StreamFinished,
    StreamStarted,
)
from .errors import (
    AttachmentReadError,
    EmptyTurnError,
    LabelingError,
    PromptNotSelectedError,
    SettingsFormatError,
)

__all__ = [
    "AttachmentReadError",
    "EmptyTurnError",
    "HistoryMessage",
    "LabelResult",
    "LabelingEngine",
    "LabelingError",
    "PromptNotSelectedError",
    "SessionSettings",
    "SessionSnapshot",
    "SettingsFormatError",
    "StreamFinished",
    "StreamStarted",
]
