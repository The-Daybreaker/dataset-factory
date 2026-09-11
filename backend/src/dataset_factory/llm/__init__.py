"""llm 能力层：全项目唯一与模型端点通信的模块。

对外接口：
- 配置与密钥：EndpointConfig / SecretValue / ConfigError / data_root / read_config / write_config
- 补全接口与客户端：Completer / OpenAIChatClient / build_completer
- 消息模型：Message / Role / TextPart / ContentPart
- 异常：LLMError
"""

from .client import Completer, OpenAIChatClient, build_completer
from .config import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    data_root,
    read_config,
    write_config,
)
from .errors import LLMError
from .messages import ContentPart, Message, Role, TextPart

__all__ = [
    "Completer",
    "ConfigError",
    "ContentPart",
    "EndpointConfig",
    "LLMError",
    "Message",
    "OpenAIChatClient",
    "Role",
    "SecretValue",
    "TextPart",
    "build_completer",
    "data_root",
    "read_config",
    "write_config",
]
