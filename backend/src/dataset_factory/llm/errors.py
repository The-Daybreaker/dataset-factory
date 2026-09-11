"""llm 的异常类型：能力层对外只抛类型化异常，不甩 SDK 原始栈、不泄密钥。

每类错误带 retryable 标记（是否值得重试）+ 可操作人话消息，供入口层映射为 CLI 退出码 /
HTTP 状态码；用户可见消息只说「哪里错、怎么修」，绝不含密钥内容。
"""

from __future__ import annotations


class LLMError(Exception):
    """llm 调用相关错误的基类。

    类属性 retryable 标记该类错误是否值得重试，子类按需覆盖。
    """

    retryable: bool = False


class LLMAuthError(LLMError):
    """鉴权 / 权限失败（密钥无效、过期或无权访问）；不可重试，需用户重设密钥。"""


class LLMRateLimitError(LLMError):
    """触发限流或额度用尽；退避后可重试。"""

    retryable = True


class LLMTimeoutError(LLMError):
    """调用超时；可重试（大图 / 慢模型也可调大 timeout）。"""

    retryable = True


class LLMConnectionError(LLMError):
    """连不上端点（网络或 base_url 不可达）；可重试。"""

    retryable = True


class LLMBadRequestError(LLMError):
    """请求被端点判为非法（消息 / 图片 / 参数不合法）；不可重试，需改输入。"""


class LLMNotFoundError(LLMError):
    """端点或模型不存在；不可重试，多为 base_url / 模型名配置错。"""


class LLMServerError(LLMError):
    """模型服务端错误（5xx）；可重试。"""

    retryable = True


class LLMUnexpectedError(LLMError):
    """未归类的意外 API 错误；不可重试，兜底。"""


class UnsupportedImageError(LLMError):
    """图片格式不支持或无法识别（只接受 png / jpeg / webp / gif）。"""


class ImageTooLargeError(LLMError):
    """图片字节数超过上限。"""
