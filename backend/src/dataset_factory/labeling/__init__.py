"""labeling 编排层：打标引擎——把「基础提示词 + skill + 素材 + 历史」拼成一次模型请求。

对外接口：
- 引擎：LabelingEngine（label 会话式一轮打标 / label_material 纯素材打标 /
  restore 恢复会话）
- 结果与快照：LabelResult / MaterialLabelResult / SessionSnapshot / SessionSettings /
  HistoryMessage
- 异常：LabelingError 基类 + PromptNotSelectedError / EmptyTurnError /
  AttachmentReadError / SettingsFormatError / MaterialReadError / MaterialOversizeError
  （下游各域异常直接冒泡，见 errors 模块说明）
"""

from .engine import (
    HistoryMessage,
    LabelingEngine,
    LabelResult,
    MaterialLabelResult,
    SessionSettings,
    SessionSnapshot,
    StreamFinished,
    StreamStarted,
    active_session_ids,
)
from .errors import (
    AttachmentReadError,
    EmptyTurnError,
    LabelingError,
    MaterialOversizeError,
    MaterialReadError,
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
    "MaterialLabelResult",
    "MaterialOversizeError",
    "MaterialReadError",
    "PromptNotSelectedError",
    "SessionSettings",
    "SessionSnapshot",
    "SettingsFormatError",
    "StreamFinished",
    "StreamStarted",
    "active_session_ids",
]
