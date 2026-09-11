"""llm 模块的配置与密钥读取（能力层）——全项目唯一接触端点配置与密钥的地方。

设计要点：
- config.json（非敏感：base_url / 模型名）与 credentials（密钥）分离存放；
- 读写都收敛在本模块：read_config 读、write_config 原子写（临时文件 + fsync + os.replace），入口层不直接碰这两个文件；
- 密钥双通道：credentials 文件为主，环境变量 DSF_API_KEY 为辅且优先覆盖；
- 全程脱敏：密钥绝不进 repr / str / 日志 / 错误信息；
- 边界 Fail-Fast：缺失 / 损坏给可操作错误（哪里错、怎么修），不甩原始栈、不泄密钥；
- llm 不依赖任何功能模块（import-linter forbidden 契约守）。

credentials 文件格式：纯文本，内容为 API key 本身（单一密钥，最简）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .._fs import atomic_write_text, data_root

ENV_API_KEY = "DSF_API_KEY"  # pragma: allowlist secret —— 环境变量名常量、非密钥值（辅通道，优先覆盖 credentials 文件）

_CONFIG_FILENAME = "config.json"
_CREDENTIALS_FILENAME = (
    "credentials"  # pragma: allowlist secret —— 文件名常量、非密钥值
)
_MASK = "**********"

# 请求参数的默认值（可被 config.json 覆盖；见 design「llm 模块实现基调」的生成参数条）。
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_MAX_RETRIES = 2

# config.json 里「请求参数」相关的键：本模块读它们，但 write_config 不负责写（写入只更新
# 端点三要素）——这些键若已存在则原样保留，避免 `dsf config set` 改端点时抹掉用户配好的参数。
_REQUEST_PARAM_KEYS = (
    "temperature",
    "top_p",
    "max_tokens",
    "extra_body",
    "timeout_seconds",
    "max_retries",
)


class ConfigError(Exception):
    """配置 / 密钥不可用（缺失、损坏、字段不全）。

    消息只描述「哪里错、怎么修」，绝不含密钥内容。
    """


@dataclass(frozen=True, repr=False)
class SecretValue:
    """密钥包裹：任何字符串化都只显掩码，防止误入日志 / repr / 错误信息。

    真正要用密钥的地方（如构建 API 客户端）显式调 reveal()——让"用到密钥"这件事在代码里看得见。
    """

    value: str

    def reveal(self) -> str:
        """取出真实密钥；仅在必须把密钥交给 SDK 时调用。"""
        return self.value

    def __repr__(self) -> str:
        return f"SecretValue('{_MASK}')"


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


def read_config() -> EndpointConfig:
    """读取端点配置 + 请求参数 + 密钥，组装成 EndpointConfig。

    数据根由 data_root() 决定；测试用 temp_data_root fixture 设 DATASET_FACTORY_HOME 隔离真实目录。

    Returns:
        EndpointConfig：端点三要素 + 请求参数（未配置时用内置默认）。

    Raises:
        ConfigError: 配置或密钥缺失 / 损坏（消息可操作、不含密钥）。
    """
    root = data_root()
    base_url, model, request = _read_config_json(root / _CONFIG_FILENAME)
    api_key = _resolve_api_key(root / _CREDENTIALS_FILENAME)
    return EndpointConfig(
        base_url=base_url, model=model, api_key=api_key, request=request
    )


def describe_config() -> tuple[str | None, str | None, str | None]:
    """只读描述当前配置状态（诊断用，不因密钥缺失而报错）。

    与 read_config 的分工：read_config 是「装配客户端」用的完整读取（缺一样就 fail loud）；
    describe_config 是「给用户看现在配了什么」的诊断视图（缺什么就显示缺什么）。

    Returns:
        (base_url, model, 密钥来源) 三元组：config.json 缺失时前两项为 None；密钥来源为
        "env"（环境变量 DSF_API_KEY）/ "file"（credentials 文件）/ None（两通道都没配）。

    Raises:
        ConfigError: config.json 存在但损坏（非法 JSON / 顶层非对象 / 字段类型错）。
    """
    root = data_root()
    base_url: str | None = None
    model: str | None = None
    config_path = root / _CONFIG_FILENAME
    if config_path.exists():
        # 只关心端点两项：请求参数不影响「现在配了什么」这个诊断视图。
        base_url, model, _ = _read_config_json(config_path)
    if os.environ.get(ENV_API_KEY, "").strip():
        key_source: str | None = "env"
    else:
        credentials = root / _CREDENTIALS_FILENAME
        try:
            has_file_key = credentials.exists() and bool(
                credentials.read_text(encoding="utf-8").strip()
            )
        except OSError:
            has_file_key = False
        key_source = "file" if has_file_key else None
    return base_url, model, key_source


def _read_config_json(path: Path) -> tuple[str, str, RequestConfig]:
    """从 config.json 读端点配置与可选的请求参数。

    Args:
        path: config.json 的路径。

    Returns:
        (base_url, model, 请求参数)——前两项是非空字符串；第三项在文件里没配时全是内置默认值。

    Raises:
        ConfigError: 文件缺失 / 不可读 / 非合法 JSON / 顶层非对象 / 字段缺失或类型错。
    """
    if not path.exists():
        raise ConfigError(
            f"未找到端点配置 {path}；请先用 `dsf config set` 设置 base_url 与模型名。"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"无法读取端点配置 {path}：{exc.strerror or exc}") from exc
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"端点配置 {path} 不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列）；请检查语法。"
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"端点配置 {path} 顶层应为 JSON 对象；请检查内容。")
    # json.loads 返回 Any：显式收成 dict[str, object] 再逐字段 isinstance 校验，
    # 既满足 strict 类型检查，也把「外部不可信数据在边界做运行时校验」落实。
    data = cast(dict[str, object], parsed)
    base_url = data.get("base_url")
    model = data.get("model")
    if not isinstance(base_url, str) or not base_url:
        raise ConfigError(f"端点配置 {path} 的 base_url 缺失或不是非空字符串；请补全。")
    if not isinstance(model, str) or not model:
        raise ConfigError(f"端点配置 {path} 的 model 缺失或不是非空字符串；请补全。")
    return base_url, model, _parse_request_config(path, data)


def _parse_request_config(path: Path, data: Mapping[str, object]) -> RequestConfig:
    """解析 config.json 里可选的请求参数（没配就用内置默认）。

    Args:
        path: config.json 路径（仅用于报错信息）。
        data: config.json 解析出的顶层对象。

    Returns:
        请求参数；所有字段都可缺省。

    Raises:
        ConfigError: 某个参数字段存在但类型不对。
    """
    timeout = _opt_float(path, data, "timeout_seconds")
    retries = _opt_int(path, data, "max_retries")
    return RequestConfig(
        temperature=_opt_float(path, data, "temperature"),
        top_p=_opt_float(path, data, "top_p"),
        max_tokens=_opt_int(path, data, "max_tokens"),
        extra_body=_opt_mapping(path, data, "extra_body"),
        timeout_seconds=timeout if timeout is not None else _DEFAULT_TIMEOUT_SECONDS,
        max_retries=retries if retries is not None else _DEFAULT_MAX_RETRIES,
    )


def _opt_float(path: Path, data: Mapping[str, object], key: str) -> float | None:
    """取可选数字字段；缺失返回 None。

    Raises:
        ConfigError: 字段存在但不是数字（bool 不算数字）。
    """
    raw = data.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"端点配置 {path} 的 {key} 应是数字；请检查内容。")
    return float(raw)


def _opt_int(path: Path, data: Mapping[str, object], key: str) -> int | None:
    """取可选整数字段；缺失返回 None。

    Raises:
        ConfigError: 字段存在但不是整数（bool 不算整数）。
    """
    raw = data.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"端点配置 {path} 的 {key} 应是整数；请检查内容。")
    return raw


def _opt_mapping(
    path: Path, data: Mapping[str, object], key: str
) -> dict[str, object] | None:
    """取可选对象字段；缺失返回 None。

    Raises:
        ConfigError: 字段存在但不是 JSON 对象。
    """
    raw = data.get(key)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"端点配置 {path} 的 {key} 应是 JSON 对象；请检查内容。")
    return cast(dict[str, object], raw)


def _read_optional_json(path: Path) -> dict[str, object] | None:
    """尽力读一个 JSON 对象（用于保留既有字段）；文件不存在或不是对象时返回 None。"""
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None


def _resolve_api_key(credentials_path: Path) -> SecretValue:
    """按双通道解析 API 密钥：环境变量 DSF_API_KEY 优先，其次 credentials 文件。

    Args:
        credentials_path: credentials 文件路径（主通道）。

    Returns:
        包好的密钥（SecretValue，字符串化时脱敏）。

    Raises:
        ConfigError: 两个通道都拿不到非空密钥，或 credentials 文件不可读。
    """
    env_key = os.environ.get(ENV_API_KEY)
    if env_key and env_key.strip():
        return SecretValue(env_key.strip())
    if credentials_path.exists():
        try:
            raw = credentials_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(
                f"无法读取密钥文件 {credentials_path}：{exc.strerror or exc}"
            ) from exc
        if raw:
            return SecretValue(raw)
    raise ConfigError(
        "未找到 API 密钥：请设置 credentials 文件（`dsf config set`）"
        f"或环境变量 {ENV_API_KEY}。"
    )


def read_stored_api_key() -> SecretValue | None:
    """读当前已配置的密钥（env 优先、其次 credentials 文件）；未配置返回 None。

    供入口层做配置「部分更新」（用户没填新密钥时沿用现有值，避免强迫重输）；与
    _resolve_api_key 的差别：这里不因缺失而报错——缺就返回 None，由调用方决定怎么提示。
    """
    env_key = os.environ.get(ENV_API_KEY)
    if env_key and env_key.strip():
        return SecretValue(env_key.strip())
    credentials = data_root() / _CREDENTIALS_FILENAME
    if credentials.exists():
        try:
            raw = credentials.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if raw:
            return SecretValue(raw)
    return None


def write_config(config: EndpointConfig) -> None:
    """把端点配置与密钥原子写入数据根（config.json + credentials）。

    与 read_config 对称——入口层（`dsf config set` / Web 配置页）组装好 EndpointConfig
    交给本函数落盘，全项目只有 llm 接触这两个文件。写前对三个字段做 Fail-Fast 校验，
    避免落下一个读侧又会拒绝的坏配置；两个文件各自原子写（临时文件 + fsync +
    os.replace），崩溃不留半个损坏文件。credentials 在 Unix 上以 0600 落盘（mkstemp
    默认权限，仅本人可读写），Windows 无 0600 语义、靠用户主目录默认 ACL 隔离。

    Args:
        config: 端点三要素（base_url / model / api_key）。

    Raises:
        ConfigError: 字段去掉首尾空白后为空，或底层目录 / 文件写入失败。
    """
    base_url = config.base_url.strip()
    model = config.model.strip()
    api_key = config.api_key.reveal().strip()
    if not base_url:
        raise ConfigError(
            "base_url 不能为空；请填写端点地址（如 https://api.example.com/v1）。"
        )
    if not model:
        raise ConfigError("model 不能为空；请填写模型名。")
    if not api_key:
        raise ConfigError("api_key 不能为空；请填写密钥。")
    root = data_root()
    # 请求参数（temperature / extra_body / timeout 等）由用户自行维护（手改 config.json，
    # 后续入口再扩展）：这里把既有值原样保留，避免「改端点」顺带抹掉配好的生成参数。
    payload: dict[str, object] = {"base_url": base_url, "model": model}
    existing = _read_optional_json(root / _CONFIG_FILENAME)
    if existing is not None:
        for key in _REQUEST_PARAM_KEYS:
            if key in existing:
                payload[key] = existing[key]
    config_json = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(root / _CONFIG_FILENAME, config_json)
    _atomic_write_text(root / _CREDENTIALS_FILENAME, api_key)


def _atomic_write_text(path: Path, text: str) -> None:
    """建目录后复用共享原子写落盘，把底层错误翻译成 ConfigError。

    原子写的机制（同目录临时文件 + fsync + os.replace + 兜底清理）收敛在 `_fs` 供各
    数据域复用；本封装只补 llm 域的两件事：先建父目录（以便区分「建目录失败」与
    「写文件失败」），再把底层 OSError / UnicodeEncodeError 翻译成 ConfigError（消息可
    操作、不含密钥）。

    Args:
        path: 目标文件路径。
        text: 要写入的文本内容。

    Raises:
        ConfigError: 目录无法创建，或底层写入 / 改名失败，或内容含 UTF-8 无法编码的字符。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"无法在 {path.parent} 准备写入：{exc.strerror or exc}"
        ) from exc
    try:
        atomic_write_text(path, text)
    except OSError as exc:
        raise ConfigError(f"无法写入 {path}：{exc.strerror or exc}") from exc
    except UnicodeEncodeError as exc:
        raise ConfigError(
            f"无法写入 {path}：内容含 UTF-8 无法编码的字符（{exc.reason}）"
        ) from exc
