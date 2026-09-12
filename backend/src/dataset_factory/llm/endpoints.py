"""端点多配置存储（endpoints/ 目录）——全项目唯一读写多套端点配置的地方。

目录布局（design.md「存储方案」，ADR「端点多配置与激活机制」）：

    ~/.dataset_factory/endpoints/
    ├── <配置名>/config.json   # 该配置的非敏感字段：base_url / model / api_format
    │                          #   （+ 用户手配的请求参数，更新时原样保留）
    ├── <配置名>/credentials   # 该配置的密钥（Unix 0600；界面与接口均不回显）
    └── active                 # 当前使用的配置名（纯文本一行）

设计要点：

- 密钥只进不出：列表与概要只给「是否已配置」，绝不回显内容；SecretValue 字符串化即脱敏；
- 写操作全部原子写（同目录临时文件 + os.replace，见 _fs），credentials 在 Unix 上以
  0600 落盘（mkstemp 默认权限）；
- 边界 Fail-Fast：名称不合法 / 重名 / 配置不存在 / 删除当前使用中的配置，一律抛
  ConfigError（哪里错、怎么修），不甩原始栈、不泄密钥；
- llm 不依赖任何功能模块（import-linter forbidden 契约守）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .._fs import atomic_write_bytes, data_root

ENDPOINTS_DIRNAME = "endpoints"
ACTIVE_FILENAME = "active"
_CONFIG_FILENAME = "config.json"
_CREDENTIALS_FILENAME = (
    "credentials"  # pragma: allowlist secret —— 文件名常量、非密钥值
)
_MASK = "**********"

# 空数据根上首次创建配置时用的名字（入口层「一套都没有」时也拿它兜底创建）。
DEFAULT_CONFIG_NAME = "default"

# 一期唯一支持的 API 调用格式：随配置存储、其余格式在界面上灰显预留（未来补适配器即启用）。
SUPPORTED_API_FORMAT = "openai-chat-completions"

# config.json 里「请求参数」相关的键：更新端点字段时原样保留，避免把用户手配的生成 /
# 传输参数抹掉（参数语义见 config.RequestConfig）。
_REQUEST_PARAM_KEYS = (
    "temperature",
    "top_p",
    "max_tokens",
    "extra_body",
    "timeout_seconds",
    "max_retries",
)

_MAX_NAME_LENGTH = 64
# Windows 文件名保留字符。数据根可能随 DATASET_FACTORY_HOME 搬到任何平台，统一按最严
# 平台校验，保证同一份数据在哪都能落盘。
_FORBIDDEN_NAME_CHARS = set('<>:"/\\|?*')


class ConfigError(Exception):
    """配置 / 密钥不可用（缺失、损坏、字段不全、名称不合法）。

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
class EndpointConfigInfo:
    """一套端点配置的概要（不含密钥内容）。

    Attributes:
        name: 配置名（即 endpoints/ 下的目录名）。
        base_url: 端点地址。
        model: 模型名。
        api_format: API 调用格式（一期仅 OpenAI Chat Completions）。
        has_api_key: 该配置是否已存密钥（只报有无，绝不回内容）。
        is_active: 是否为当前使用的配置（active 指针指向它）。
    """

    name: str
    base_url: str
    model: str
    api_format: str
    has_api_key: bool
    is_active: bool


def validate_config_name(raw: str) -> str:
    """校验并规整配置名（去除首尾空白后返回）。

    规则：1–64 个字符；不含 Windows 保留字符与控制字符；不为 ``.`` / ``..``、不以点开头
    或结尾（Windows 会吞掉结尾的点，造成与预期不符的重名）。

    Args:
        raw: 用户输入的配置名。

    Returns:
        规整后的配置名。

    Raises:
        ConfigError: 名称不合法（消息点名原因）。
    """
    name = raw.strip()
    if not name:
        raise ConfigError("配置名不能为空；请填写名称。")
    if len(name) > _MAX_NAME_LENGTH:
        raise ConfigError(f"配置名过长（最多 {_MAX_NAME_LENGTH} 个字符）；请缩短。")
    if name.startswith(".") or name.endswith("."):
        raise ConfigError(f"配置名「{name}」不合法；不能以点开头或结尾，请换一个名称。")
    if any(ch in _FORBIDDEN_NAME_CHARS or ord(ch) < 32 for ch in name):
        raise ConfigError(
            f"配置名「{name}」含不合法字符；请避免冒号、斜杠、引号等文件名保留字符。"
        )
    return name


def active_config_name() -> str | None:
    """读当前使用的配置名；未设置（无指针文件或内容为空）返回 None。

    指针内容不在这里校验合法性——「悬空指向不存在的配置」由 read_active_files 等调用方
    结合 has_config 判断并给出各自的错误消息。

    Returns:
        当前使用的配置名，未设置时 None。

    Raises:
        ConfigError: 指针文件存在但读不出来。
    """
    pointer = _endpoints_root() / ACTIVE_FILENAME
    if not pointer.is_file():
        return None
    try:
        raw = pointer.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"无法读取当前配置指针 {ACTIVE_FILENAME}：{exc}") from exc
    name = raw.strip()
    return name or None


def has_config(name: str) -> bool:
    """判断一套配置是否存在（endpoints/<名称>/config.json 在不在）。

    名称不合法（含路径穿越形态如 ``..``）一律视为不存在，不抛错——本函数服务「探一探」
    场景（诊断视图、入口层判断），不该反过来炸调用方。
    """
    try:
        clean = validate_config_name(name)
    except ConfigError:
        return False
    return (_config_dir(clean) / _CONFIG_FILENAME).is_file()


def has_stored_key(name: str) -> bool:
    """判断一套配置是否已在 credentials 文件存了非空密钥（名称不合法视为没有）。"""
    try:
        clean = validate_config_name(name)
    except ConfigError:
        return False
    return _has_file_key(_config_dir(clean) / _CREDENTIALS_FILENAME)


def list_configs() -> list[EndpointConfigInfo]:
    """列出全部端点配置概要（按名称排序、不区分大小写；不含密钥内容）。

    Returns:
        配置概要列表；每套配置的 is_active 按 active 指针判定。

    Raises:
        ConfigError: 任一配置的 config.json 缺失 / 损坏 / 字段不全（fail loud，不静默跳过——
            坏数据不该被列表悄悄藏起来）。
    """
    active = active_config_name()
    infos: list[EndpointConfigInfo] = []
    for name in _existing_config_dirs():
        data = _read_config_file(name)
        infos.append(
            EndpointConfigInfo(
                name=name,
                base_url=cast(str, data["base_url"]),
                model=cast(str, data["model"]),
                api_format=cast("str | None", data.get("api_format"))
                or SUPPORTED_API_FORMAT,
                has_api_key=_has_file_key(_config_dir(name) / _CREDENTIALS_FILENAME),
                is_active=active is not None and active == name,
            )
        )
    return infos


def read_config_data(name: str) -> dict[str, object]:
    """读一套配置的 config.json 并做结构校验（合法 JSON 对象 + base_url / model 非空）。

    api_format 不在此校验：缺失视为支持格式（旧文件没有该字段），存了别的值由调用方按
    用途决定怎么处理（展示原样、构建请求时才真正依赖格式）。

    Args:
        name: 配置名（先过名称校验，杜绝路径穿越）。

    Returns:
        解析后的 config.json 对象。

    Raises:
        ConfigError: 名称不合法 / 文件缺失 / 非法 JSON / 顶层非对象 / 字段缺失或类型错。
    """
    clean = validate_config_name(name)
    return _read_config_file(clean)


def read_stored_api_key(name: str) -> SecretValue | None:
    """读指定配置已存的密钥（仅 credentials 文件，不含环境变量通道）；未配置返回 None。

    供入口层做「密钥留空沿用」（改 base_url 不必重输密钥）。与请求时的密钥解析（config
    层，环境变量优先）刻意不同：这里只看文件里的值，避免把环境变量误持久化进文件。

    Args:
        name: 配置名（先过名称校验，杜绝路径穿越）。

    Returns:
        包好的密钥；未存密钥（无文件 / 空白 / 读不了）返回 None。

    Raises:
        ConfigError: 名称不合法。
    """
    clean = validate_config_name(name)
    credentials = _config_dir(clean) / _CREDENTIALS_FILENAME
    if not credentials.is_file():
        return None
    try:
        raw = credentials.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return SecretValue(raw) if raw else None


def read_active_files() -> tuple[str, dict[str, object], SecretValue | None]:
    """读当前使用配置的原始数据：（配置名, config.json 解析结果, 文件中的密钥或 None）。

    密钥只从该配置的 credentials 文件取；环境变量 DSF_API_KEY 的覆盖在 config 层做——
    那是「构建请求」的语义，不属于存储。

    Returns:
        (配置名, config.json 解析对象, 文件密钥或 None) 三元组。

    Raises:
        ConfigError: 未配置任何端点 / active 指向不存在的配置 / config.json 损坏或字段不全。
    """
    name = active_config_name()
    if name is None:
        raise ConfigError(
            "未配置任何端点；请先用 `dsf config set` 设置，或在 Web 设置页添加端点配置。"
        )
    if not has_config(name):
        raise ConfigError(
            f"当前使用的端点配置「{name}」不存在；请重新选择当前使用的配置，"
            "或检查数据根下 endpoints/ 目录。"
        )
    data = read_config_data(name)
    return name, data, read_stored_api_key(name)


def create_config(
    name: str,
    base_url: str,
    model: str,
    api_key: SecretValue | None,
    api_format: str = SUPPORTED_API_FORMAT,
) -> None:
    """新增一套端点配置；当前没有生效的 active 指针时，顺手把它设为当前使用。

    自动激活只发生在「指针缺失」时（典型：第一套配置，创建完就能用）；指针已指向其他
    配置时不抢当前使用权——切换是显式动作（set_active_config）。

    Args:
        name: 配置名（校验合法性 + 不区分大小写的重名检查）。
        base_url: 端点地址（非空）。
        model: 模型名（非空）。
        api_key: 密钥；None = 暂不配置（请求时可用 DSF_API_KEY 环境变量兜底）。
        api_format: API 调用格式；一期仅支持 OpenAI Chat Completions。

    Raises:
        ConfigError: 名称不合法 / 重名 / 字段为空 / 格式不支持 / 落盘失败。
    """
    clean = validate_config_name(name)
    clean_base_url = _require_clean(base_url, "base_url")
    clean_model = _require_clean(model, "model")
    _require_supported_format(api_format)
    if api_key is not None and not api_key.reveal().strip():
        raise ConfigError("API 密钥不能为空白；请填写有效密钥。")
    _require_name_available(clean)
    _write_config_files(
        _config_dir(clean), clean_base_url, clean_model, api_format, api_key
    )
    if active_config_name() is None:
        set_active_config(clean)


def update_config(
    name: str,
    base_url: str,
    model: str,
    api_key: SecretValue | None = None,
    api_format: str = SUPPORTED_API_FORMAT,
) -> None:
    """更新一套已存在配置的端点字段；api_key 传 None 表示沿用该配置已存的密钥。

    「沿用」= 不动 credentials 文件（而不是把环境变量或其他配置的密钥抄过来）。
    config.json 里用户手配的请求参数（温度 / 透传参数等）原样保留。

    Args:
        name: 配置名（必须已存在）。
        base_url: 端点地址（非空）。
        model: 模型名（非空）。
        api_key: 新密钥；None = 沿用已存密钥。
        api_format: API 调用格式；一期仅支持 OpenAI Chat Completions。

    Raises:
        ConfigError: 配置不存在 / 名称不合法 / 字段为空 / 格式不支持 / 落盘失败。
    """
    clean = validate_config_name(name)
    clean_base_url = _require_clean(base_url, "base_url")
    clean_model = _require_clean(model, "model")
    _require_supported_format(api_format)
    if api_key is not None and not api_key.reveal().strip():
        raise ConfigError("API 密钥不能为空白；请填写有效密钥。")
    dir_path = _require_config_exists(clean)
    _write_config_files(
        dir_path,
        clean_base_url,
        clean_model,
        api_format,
        api_key,
        preserve_params_from=dir_path,
    )


def delete_config(name: str) -> None:
    """删除一套端点配置（连同其 credentials）；当前使用中的配置不允许删。

    Args:
        name: 配置名（必须已存在）。

    Raises:
        ConfigError: 名称不合法 / 配置不存在 / 试图删除当前使用中的配置 / 删除失败。
    """
    clean = validate_config_name(name)
    dir_path = _require_config_exists(clean)
    active = active_config_name()
    if active is not None and active == clean:
        raise ConfigError(f"「{clean}」是当前使用的配置；请先切换到其他配置再删除。")
    try:
        shutil.rmtree(dir_path)
    except OSError as exc:
        raise ConfigError(
            f"无法删除端点配置「{clean}」：{exc.strerror or exc}"
        ) from exc


def set_active_config(name: str) -> None:
    """把当前使用指针指向一套已存在的配置；对新请求立即生效（下次构建客户端即读它）。

    Args:
        name: 配置名（必须已存在）。

    Raises:
        ConfigError: 名称不合法 / 配置不存在 / 指针写入失败。
    """
    clean = validate_config_name(name)
    _require_config_exists(clean)
    pointer = _endpoints_root() / ACTIVE_FILENAME
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(pointer, f"{clean}\n".encode())
    except (OSError, UnicodeEncodeError) as exc:
        raise ConfigError(f"无法写入当前配置指针：{exc}") from exc


def _endpoints_root() -> Path:
    """endpoints/ 目录路径（不隐含创建——创建时机归各写操作，便于区分错误来源）。"""
    return data_root() / ENDPOINTS_DIRNAME


def _config_dir(name: str) -> Path:
    """某套配置的目录路径。调用方负责先校验名称（杜绝路径穿越）。"""
    return _endpoints_root() / name


def _require_clean(value: str, field: str) -> str:
    """去首尾空白并要求非空（base_url / model 等必填字段的统一小闸门）。

    Args:
        value: 原始输入。
        field: 字段名（用于错误消息）。

    Returns:
        规整后的值。

    Raises:
        ConfigError: 去空白后为空。
    """
    cleaned = value.strip()
    if not cleaned:
        raise ConfigError(f"{field} 不能为空；请填写。")
    return cleaned


def _require_supported_format(api_format: str) -> None:
    """校验 API 调用格式是当前支持的唯一值。

    Raises:
        ConfigError: 不支持（消息说明当前仅支持什么）。
    """
    if api_format != SUPPORTED_API_FORMAT:
        raise ConfigError(
            f"API 格式「{api_format}」暂未支持；当前仅支持 OpenAI Chat Completions。"
        )


def _require_name_available(name: str) -> None:
    """重名检查（不区分大小写——Windows 目录名不区分大小写，跨平台口径取其严）。

    Raises:
        ConfigError: 已存在同名（或仅大小写不同）的配置。
    """
    for existing in _existing_config_dirs():
        if existing.casefold() == name.casefold():
            raise ConfigError(
                f"已存在配置「{existing}」（名称不区分大小写）；请换一个名称。"
            )


def _require_config_exists(name: str) -> Path:
    """要求配置存在，返回其目录路径。

    Raises:
        ConfigError: 配置不存在。
    """
    dir_path = _config_dir(name)
    if not (dir_path / _CONFIG_FILENAME).is_file():
        raise ConfigError(f"端点配置「{name}」不存在；请检查名称。")
    return dir_path


def _existing_config_dirs() -> list[str]:
    """列出 endpoints/ 下的配置目录名（含 config.json 的才算配置），按名称排序。

    endpoints/ 不存在视为没有配置；目录里没有 config.json 的（用户手动建的杂物目录）
    不算配置、静默跳过。
    """
    root = _endpoints_root()
    if not root.is_dir():
        return []
    names = [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / _CONFIG_FILENAME).is_file()
    ]
    return sorted(names, key=str.casefold)


def _read_config_file(name: str) -> dict[str, object]:
    """读并校验一套配置的 config.json（供列表与请求装配共用的底层读取）。

    Args:
        name: 配置名（来自文件系统枚举或已校验的指针，不再重复名称校验）。

    Returns:
        解析并校验过 base_url / model 的 config.json 对象。

    Raises:
        ConfigError: 文件缺失 / 读不了 / 非法 JSON / 顶层非对象 / 字段缺失或类型错。
    """
    path = _config_dir(name) / _CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(f"端点配置「{name}」缺少 config.json；请补全或删除该配置。")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"无法读取端点配置「{name}」的 config.json：{exc}") from exc
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"端点配置「{name}」的 config.json 不是合法 JSON"
            f"（第 {exc.lineno} 行第 {exc.colno} 列）；请检查语法。"
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"端点配置「{name}」的 config.json 顶层应为 JSON 对象；请检查内容。"
        )
    data = cast(dict[str, object], parsed)
    base_url = data.get("base_url")
    model = data.get("model")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError(
            f"端点配置「{name}」的 base_url 缺失或不是非空字符串；请补全。"
        )
    if not isinstance(model, str) or not model.strip():
        raise ConfigError(f"端点配置「{name}」的 model 缺失或不是非空字符串；请补全。")
    return data


def _write_config_files(
    dir_path: Path,
    base_url: str,
    model: str,
    api_format: str,
    api_key: SecretValue | None,
    preserve_params_from: Path | None = None,
) -> None:
    """写一套配置的两个文件（config.json + credentials），各自原子写。

    Args:
        dir_path: 配置目录。
        base_url: 已规整的端点地址。
        model: 已规整的模型名。
        api_format: API 调用格式。
        api_key: 密钥；None = 不写 credentials（更新场景即「沿用已存密钥」）。
        preserve_params_from: 给出时（更新场景），从该目录的旧 config.json 里把已有的
            请求参数键原样搬进新 payload，避免改端点抹掉用户手配的参数。

    Raises:
        ConfigError: 旧参数读不了 / 内容无法编码 / 落盘失败。
    """
    payload: dict[str, object] = {
        "base_url": base_url,
        "model": model,
        "api_format": api_format,
    }
    if preserve_params_from is not None:
        existing = _read_optional_json(preserve_params_from / _CONFIG_FILENAME)
        if existing is not None:
            for key in _REQUEST_PARAM_KEYS:
                if key in existing:
                    payload[key] = existing[key]
    config_json = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        config_bytes = config_json.encode("utf-8")
        key_bytes = (
            api_key.reveal().strip().encode("utf-8") if api_key is not None else None
        )
    except UnicodeEncodeError as exc:
        raise ConfigError(
            f"端点配置含 UTF-8 无法编码的字符（{exc.reason}）；请检查输入内容。"
        ) from exc
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(dir_path / _CONFIG_FILENAME, config_bytes)
        if key_bytes is not None:
            atomic_write_bytes(dir_path / _CREDENTIALS_FILENAME, key_bytes)
    except OSError as exc:
        raise ConfigError(f"无法写入端点配置文件：{exc.strerror or exc}") from exc


def _has_file_key(credentials_path: Path) -> bool:
    """credentials 文件里是否有非空密钥（读不了按没有算——探一探语义，不炸调用方）。"""
    if not credentials_path.is_file():
        return False
    try:
        return bool(credentials_path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


def _read_optional_json(path: Path) -> dict[str, object] | None:
    """尽力读一个 JSON 对象（用于保留既有字段）；读不了或不是对象时返回 None。"""
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
