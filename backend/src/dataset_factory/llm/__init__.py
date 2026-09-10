"""llm 能力层：全项目唯一与模型端点通信的模块。

对外接口（逐步补齐）：
- 配置与密钥：EndpointConfig / SecretValue / ConfigError / data_root / read_config
- provider 中立接口 + OpenAI 兼容客户端（后续补）
"""

from .config import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    data_root,
    read_config,
)

__all__ = [
    "ConfigError",
    "EndpointConfig",
    "SecretValue",
    "data_root",
    "read_config",
]
