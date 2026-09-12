"""llm 模块的请求侧配置视图——把当前使用的端点配置组装成可发请求的 EndpointConfig。

存储侧（endpoints/ 多配置目录、迁移、增删改查、active 指针）见同目录 endpoints.py；
本模块是其上的「请求视图」：读当前使用（active）的配置，套上密钥双通道与请求参数，
产出构建 API 客户端所需的类型化对象。入口层管理多配置请调 endpoints 的接口；本模块
只回答「现在发请求用哪套配置」。

设计要点：

- 密钥双通道：当前使用配置的 credentials 文件为主，环境变量 DSF_API_KEY 为辅且优先覆盖；
- 全程脱敏：密钥绝不进 repr / str / 日志 / 错误信息；
- 边界 Fail-Fast：缺失 / 损坏给可操作错误（哪里错、怎么修），不甩原始栈、不泄密钥。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from .endpoints import (
    ConfigError,
    SecretValue,
    active_config_name,
    has_config,
    has_stored_key,
    read_active_files,
    read_config_data,
)

ENV_API_KEY = "DSF_API_KEY"  # pragma: allowlist secret —— 环境变量名常量、非密钥值（辅通道，优先覆盖 credentials 文件）

# 请求参数的默认值（可被 config.json 覆盖；见 design「llm 模块实现基调」的生成参数条）。
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_MAX_RETRIES = 2


@dataclass(frozen=True)
class RequestConfig:
    """一次模型请求的可调参数：生成参数（标准层 + 透传层）与传输参数。

    分两层是刻意的（见 design「llm 模块实现基调」）：**标准层**只放 OpenAI 标准参数
    （temperature / top_p / max_tokens，语义跨端点通用）；**透传层** `extra_body` 原样转发
    端点专有参数（如 Qwen 的 `chat_template_kwargs.enable_thinking`、`top_k`），llm 不解释
    其语义——这样将来端点冒出的新参数不必改核心接口，符合「不绑定厂商」的方向。

    所有字段都有默认值：不配也能跑，配了才生效。

    Attributes:
        temperature: 采样温度；None = 不传该参数（用端点默认）。
        top_p: 核采样阈值；None = 不传。
        max_tokens: **输出** token 上限；None = 不传。注意它管输出侧，不是上下文窗口——
            上下文窗口是模型的固有属性、不可设置。
        extra_body: 端点专有参数，原样放进 SDK 的 extra_body 转发；None = 不传。
        timeout_seconds: 单次 HTTP 调用超时（秒）。推理型模型默认带思考模式时响应明显更慢，
            必要时调大它。
        max_retries: SDK 内建重试次数（对超时 / 5xx / 429 指数退避）。
    """

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    extra_body: Mapping[str, object] | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_retries: int = _DEFAULT_MAX_RETRIES


@dataclass(frozen=True)
class EndpointConfig:
    """构建 API 客户端所需的端点配置。

    api_key 为 SecretValue，因此本对象自动生成的 repr 也不会泄露密钥。

    Attributes:
        base_url: 端点地址。
        model: 模型名。
        api_key: 密钥（脱敏包裹）。
        request: 请求参数（生成 + 传输），全部有默认值——调用方不关心时无需提供。
    """

    base_url: str
    model: str
    api_key: SecretValue
    request: RequestConfig = RequestConfig()


@dataclass(frozen=True)
class ConfigDescription:
    """当前配置状态的诊断视图（不含密钥内容）。

    Attributes:
        name: 当前使用的配置名；None = 没有生效的当前配置（未配置任何端点、指针未设或悬空）。
        base_url: 端点地址；未配置时 None。
        model: 模型名；未配置时 None。
        key_source: 密钥来源："env"（环境变量 DSF_API_KEY）/ "file"（当前配置的 credentials
            文件）/ None（两通道都没配）。
    """

    name: str | None
    base_url: str | None
    model: str | None
    key_source: str | None


def read_config() -> EndpointConfig:
    """读取当前使用的端点配置 + 请求参数 + 密钥，组装成 EndpointConfig。

    数据根由 data_root() 决定；测试用 temp_data_root fixture 设 DATASET_FACTORY_HOME 隔离真实目录。

    Returns:
        EndpointConfig：端点三要素 + 请求参数（未配置时用内置默认）。

    Raises:
        ConfigError: 未配置任何端点 / 当前配置缺失或损坏 / 密钥两通道都拿不到（消息可操作、不含密钥）。
    """
    name, data, file_key = read_active_files()
    # read_active_files 已把 base_url / model 校验为非空字符串，这里收窄只是让类型系统知道。
    base_url = cast(str, data["base_url"])
    model = cast(str, data["model"])
    return EndpointConfig(
        base_url=base_url,
        model=model,
        api_key=_resolve_api_key(file_key),
        request=_parse_request_config(name, data),
    )


def describe_config() -> ConfigDescription:
    """只读描述当前使用的配置状态（诊断用，不因密钥缺失而报错）。

    与 read_config 的分工：read_config 是「装配客户端」用的完整读取（缺一样就 fail loud）；
    describe_config 是「给用户看现在配了什么」的诊断视图（缺什么就显示什么）。config.json
    损坏仍会抛 ConfigError——坏文件不该被粉饰成「未配置」。

    Returns:
        ConfigDescription：当前配置的名称 / base_url / model 与密钥来源；没有生效的当前
        配置时各字段为 None（密钥来源仍可能报 env——环境变量独立于配置存在）。

    Raises:
        ConfigError: 当前配置的 config.json 存在但损坏（非法 JSON / 顶层非对象 / 字段类型错）。
    """
    env_key_present = bool(os.environ.get(ENV_API_KEY, "").strip())
    active = active_config_name()
    if active is None or not has_config(active):
        return ConfigDescription(
            name=None,
            base_url=None,
            model=None,
            key_source="env" if env_key_present else None,
        )
    data = read_config_data(active)
    key_source: str | None
    if env_key_present:
        key_source = "env"
    elif has_stored_key(active):
        key_source = "file"
    else:
        key_source = None
    return ConfigDescription(
        name=active,
        base_url=cast(str, data["base_url"]),
        model=cast(str, data["model"]),
        key_source=key_source,
    )


def _parse_request_config(name: str, data: Mapping[str, object]) -> RequestConfig:
    """解析 config.json 里可选的请求参数（没配就用内置默认）。

    Args:
        name: 配置名（仅用于报错信息）。
        data: config.json 解析出的顶层对象。

    Returns:
        请求参数；所有字段都可缺省。

    Raises:
        ConfigError: 某个参数字段存在但类型不对。
    """
    timeout = _opt_float(name, data, "timeout_seconds")
    retries = _opt_int(name, data, "max_retries")
    return RequestConfig(
        temperature=_opt_float(name, data, "temperature"),
        top_p=_opt_float(name, data, "top_p"),
        max_tokens=_opt_int(name, data, "max_tokens"),
        extra_body=_opt_mapping(name, data, "extra_body"),
        timeout_seconds=timeout if timeout is not None else _DEFAULT_TIMEOUT_SECONDS,
        max_retries=retries if retries is not None else _DEFAULT_MAX_RETRIES,
    )


def _opt_float(name: str, data: Mapping[str, object], key: str) -> float | None:
    """取可选数字字段；缺失返回 None。

    Raises:
        ConfigError: 字段存在但不是数字（bool 不算数字）。
    """
    raw = data.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"端点配置「{name}」的 {key} 应是数字；请检查内容。")
    return float(raw)


def _opt_int(name: str, data: Mapping[str, object], key: str) -> int | None:
    """取可选整数字段；缺失返回 None。

    Raises:
        ConfigError: 字段存在但不是整数（bool 不算整数）。
    """
    raw = data.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"端点配置「{name}」的 {key} 应是整数；请检查内容。")
    return raw


def _opt_mapping(
    name: str, data: Mapping[str, object], key: str
) -> dict[str, object] | None:
    """取可选对象字段；缺失返回 None。

    Raises:
        ConfigError: 字段存在但不是 JSON 对象。
    """
    raw = data.get(key)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"端点配置「{name}」的 {key} 应是 JSON 对象；请检查内容。")
    return cast(dict[str, object], raw)


def _resolve_api_key(file_key: SecretValue | None) -> SecretValue:
    """按双通道解析 API 密钥：环境变量 DSF_API_KEY 优先，其次当前配置的 credentials 文件。

    Args:
        file_key: 当前配置 credentials 文件里的密钥（读不到为 None）。

    Returns:
        包好的密钥（SecretValue，字符串化时脱敏）。

    Raises:
        ConfigError: 两个通道都拿不到非空密钥。
    """
    env_key = os.environ.get(ENV_API_KEY)
    if env_key and env_key.strip():
        return SecretValue(env_key.strip())
    if file_key is not None:
        return file_key
    raise ConfigError(
        "未找到 API 密钥：请设置当前使用配置的密钥（`dsf config set` 或 Web 设置页），"
        f"或使用环境变量 {ENV_API_KEY}。"
    )
