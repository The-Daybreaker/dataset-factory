"""llm 能力层：全项目唯一与模型端点通信的模块。

对外接口：
- 配置与密钥：EndpointConfig / SecretValue / ConfigError / data_root / read_config /
  describe_config / write_config
- 补全接口与客户端：Completer / OpenAIChatClient / build_completer
- 消息模型：Message / Role / TextPart / ImagePart / ContentPart
- 异常：LLMError 基类 + 分类子类（鉴权 / 限流 / 超时 / 连接 / 请求非法 / 未找到 / 服务端 / 意外）+ UnsupportedImageError / ImageTooLargeError
"""

from .client import Completer, OpenAIChatClient, build_completer
from .config import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    data_root,
    describe_config,
    read_config,
    write_config,
)
from .errors import (
    ImageTooLargeError,
    LLMAuthError,
    LLMBadRequestError,
    LLMConnectionError,
    LLMError,
    LLMNotFoundError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnexpectedError,
    UnsupportedImageError,
)
from .messages import ContentPart, ImagePart, Message, Role, TextPart

__all__ = [
    "Completer",
    "ConfigError",
    "ContentPart",
    "EndpointConfig",
    "ImagePart",
    "ImageTooLargeError",
    "LLMAuthError",
    "LLMBadRequestError",
    "LLMConnectionError",
    "LLMError",
    "LLMNotFoundError",
    "LLMRateLimitError",
    "LLMServerError",
    "LLMTimeoutError",
    "LLMUnexpectedError",
    "Message",
    "OpenAIChatClient",
    "Role",
    "SecretValue",
    "TextPart",
    "UnsupportedImageError",
    "build_completer",
    "data_root",
    "describe_config",
    "read_config",
    "write_config",
]
