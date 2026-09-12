"""llm 能力层：全项目唯一与模型端点通信的模块。

对外接口：
- 配置与密钥：EndpointConfig / SecretValue / ConfigError / data_root / read_config /
  describe_config；多配置存储（endpoints/ 目录）——EndpointConfigInfo / list_configs /
  create_config / update_config / delete_config / set_active_config / active_config_name /
  read_config_data / read_stored_api_key / has_config / has_stored_key /
  DEFAULT_CONFIG_NAME / SUPPORTED_API_FORMAT
- 补全接口与客户端：Completer / OpenAIChatClient / build_completer
- 消息模型：Message / Role / TextPart / ImagePart / ContentPart
- 异常：LLMError 基类 + 分类子类（鉴权 / 限流 / 超时 / 连接 / 请求非法 / 未找到 / 服务端 / 意外）+ UnsupportedImageError / ImageTooLargeError
"""

from .._fs import data_root
from .client import Completer, OpenAIChatClient, build_completer
from .config import EndpointConfig, RequestConfig, describe_config, read_config
from .endpoints import (
    DEFAULT_CONFIG_NAME,
    SUPPORTED_API_FORMAT,
    ConfigConflictError,
    ConfigError,
    ConfigNotFoundError,
    EndpointConfigInfo,
    SecretValue,
    active_config_name,
    create_config,
    delete_config,
    has_config,
    has_stored_key,
    list_configs,
    read_config_data,
    read_stored_api_key,
    set_active_config,
    update_config,
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
    "DEFAULT_CONFIG_NAME",
    "SUPPORTED_API_FORMAT",
    "Completer",
    "ConfigConflictError",
    "ConfigError",
    "ConfigNotFoundError",
    "ContentPart",
    "EndpointConfig",
    "EndpointConfigInfo",
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
    "RequestConfig",
    "Role",
    "SecretValue",
    "TextPart",
    "UnsupportedImageError",
    "active_config_name",
    "build_completer",
    "create_config",
    "data_root",
    "delete_config",
    "describe_config",
    "has_config",
    "has_stored_key",
    "list_configs",
    "read_config",
    "read_config_data",
    "read_stored_api_key",
    "set_active_config",
    "update_config",
]
