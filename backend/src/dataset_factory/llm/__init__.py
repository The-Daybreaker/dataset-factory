"""llm 能力层：全项目唯一与模型端点通信的模块。

对外接口：
- 配置与密钥：EndpointConfig / SecretValue / ConfigError / data_root / read_config /
  describe_config；多配置存储（endpoints/ 目录）——EndpointConfigInfo / list_configs /
  config_info / create_config / update_config / rename_config / delete_config /
  set_active_config / active_config_name / read_config_data / read_stored_api_key /
  has_config / has_stored_key / validated_request_params / DEFAULT_CONFIG_NAME /
  SUPPORTED_API_FORMAT
- 补全接口与客户端：Completer / OpenAIChatClient / build_completer
- 消息模型：Message / Role / TextPart / ImagePart / ContentPart
- 媒体护栏常量（单一事实源）：MAX_IMAGE_BYTES / MAX_VIDEO_BYTES / IMAGE_MIME_BY_SUFFIX / VIDEO_MIME_BY_SUFFIX / IMAGE_EXTENSIONS / VIDEO_EXTENSIONS
- 异常：LLMError 基类 + 分类子类（鉴权 / 限流 / 超时 / 连接 / 请求非法 / 未找到 / 服务端 / 意外）+ UnsupportedImageError / ImageTooLargeError
"""

from .._fs import data_root
from .client import (
    Completer,
    OpenAIChatClient,
    ProbeResult,
    build_completer,
    probe_endpoint,
)
from .config import (
    ENV_API_KEY,
    EndpointConfig,
    RequestConfig,
    describe_config,
    env_api_key,
    first_api_key,
    parse_request_params,
    read_config,
    resolve_api_key,
)
from .endpoints import (
    DEFAULT_CONFIG_NAME,
    SUPPORTED_API_FORMAT,
    ConfigConflictError,
    ConfigError,
    ConfigNotFoundError,
    EndpointConfigInfo,
    SecretValue,
    active_config_name,
    config_info,
    create_config,
    delete_config,
    has_config,
    has_stored_key,
    list_configs,
    read_config_data,
    read_stored_api_key,
    rename_config,
    set_active_config,
    update_config,
    validated_request_params,
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
from .images import MAX_IMAGE_BYTES
from .messages import (
    IMAGE_EXTENSIONS,
    IMAGE_MIME_BY_SUFFIX,
    MAX_VIDEO_BYTES,
    VIDEO_EXTENSIONS,
    VIDEO_MIME_BY_SUFFIX,
    ContentPart,
    ImagePart,
    Message,
    Role,
    StreamDelta,
    TextPart,
    VideoPart,
)

__all__ = [
    "DEFAULT_CONFIG_NAME",
    "ENV_API_KEY",
    "IMAGE_EXTENSIONS",
    "IMAGE_MIME_BY_SUFFIX",
    "MAX_IMAGE_BYTES",
    "MAX_VIDEO_BYTES",
    "SUPPORTED_API_FORMAT",
    "VIDEO_EXTENSIONS",
    "VIDEO_MIME_BY_SUFFIX",
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
    "ProbeResult",
    "RequestConfig",
    "Role",
    "SecretValue",
    "StreamDelta",
    "TextPart",
    "UnsupportedImageError",
    "VideoPart",
    "active_config_name",
    "build_completer",
    "config_info",
    "create_config",
    "data_root",
    "delete_config",
    "describe_config",
    "env_api_key",
    "first_api_key",
    "has_config",
    "has_stored_key",
    "list_configs",
    "parse_request_params",
    "probe_endpoint",
    "read_config",
    "read_config_data",
    "read_stored_api_key",
    "rename_config",
    "resolve_api_key",
    "set_active_config",
    "update_config",
    "validated_request_params",
]
