"""端点多配置存储（endpoints/ 目录）——全项目唯一读写多套端点配置的地方。

目录布局（ID 身份，2026-09-23 定案；与策略库同构——名字只是显示字段）：

    ~/.dataset_factory/endpoints/
    ├── <配置ID>/config.json   # 非敏感字段：id / name（显示名）/ base_url / model /
    │                          #   api_format（+ 用户手配的请求参数，更新时原样保留）
    ├── <配置ID>/credentials   # 密钥（Unix 0600；界面与接口均不回显）
    └── active                 # 当前使用的配置 ID（纯文本一行）

设计要点：

- **身份 = 内部稳定 ID**（创建时分配，目录名即 ID）；显示名可改、允许重名——改名只写
  config.json 的 name 字段，不动目录、不动指针。引用（策略库 / 快照）一律存 ID，
  「改名炸引用」在结构上不可能再发生（2026-09-22 事故的根治）；
- **存量迁移**：读侧发现 config.json 缺 id（旧版以名字当身份、目录名即名字）即惰性
  升级——分配 ID、回写、目录改名；逐配置原子写、幂等，中途断电无半截状态；
- 密钥只进不出：列表与概要只给「是否已配置」，绝不回显内容；SecretValue 字符串化即脱敏；
- 写操作全部原子写（同目录临时文件 + os.replace，见 _fs），credentials 在 Unix 上以
  0600 落盘（mkstemp 默认权限）；
- 边界 Fail-Fast：ID 不存在 / 显示名不合法 / 删除当前使用中的配置，一律抛 ConfigError；
- llm 不依赖任何功能模块（import-linter forbidden 契约守）。
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from collections.abc import Mapping
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

# 默认显示名（入口层「一套都没有」时兜底创建的那套用；ID 总是另行分配）。
DEFAULT_CONFIG_NAME = "default"

# 一期唯一支持的 API 调用格式：随配置存储、其余格式在界面上灰显预留（未来补适配器即启用）。
SUPPORTED_API_FORMAT = "openai-chat-completions"

# config.json 里「请求参数」相关的键：更新端点字段且未显式给参数时原样保留，避免把
# 用户手配的生成 / 传输参数抹掉（参数语义见 config.RequestConfig）。键集是封闭的——
# 不在这份清单里的键（如厂商文档里的其他参数）不属于本工具的参数面，经界面/API 写入时
# 会被丢弃（要透传厂商专有参数请放 extra_body）。enable_thinking 是一等参数（B 方案，
# 2026-09-23）：思考模式开关，布尔值，SiliconFlow / DashScope 等国内端点的官方顶层
# 口径——其余厂商形状（reasoning_effort / thinking 等）仍走 extra_body 逃生门。
_REQUEST_PARAM_KEYS = (
    "temperature",
    "top_p",
    "max_tokens",
    "enable_thinking",
    "extra_body",
    "timeout_seconds",
    "max_retries",
)

# 数值型参数键（浮点 / 整数分别校验）；extra_body 是透传对象；enable_thinking 是布尔。
_FLOAT_PARAM_KEYS = ("temperature", "top_p", "timeout_seconds")
_INT_PARAM_KEYS = ("max_tokens", "max_retries")
_BOOL_PARAM_KEYS = ("enable_thinking",)

# 显示名长度上限（纯显示别名、允许重名，对齐策略库口径）。
_MAX_NAME_LENGTH = 100

# 配置 ID 形状（目录名即 ID；与策略 ID 同一模式，字母开头避免被 CLI 解析为选项）。
_CONFIG_ID_RE = re.compile(r"^[a-z][A-Za-z0-9_-]{10}$")


class ConfigError(Exception):
    """配置 / 密钥不可用（缺失、损坏、字段不全、显示名不合法）。

    消息只描述「哪里错、怎么修」，绝不含密钥内容。
    """


class ConfigNotFoundError(ConfigError):
    """端点配置不存在（按 ID 找不到）。"""


class ConfigConflictError(ConfigError):
    """配置状态冲突（删除当前使用中的配置等）。"""


def validated_request_params(
    data: Mapping[str, object], name: str
) -> dict[str, object]:
    """从配置数据里取出「请求参数」键并校验值类型；只返回实际存在的键。

    这是请求参数的唯一类型校验点：存储读侧（list_configs 的概要展示）与请求装配侧
    （config 层组装 RequestConfig）共用同一份判定，保证「界面看得到的」与「发请求用的」
    不会各判各的。写侧也复用（create_config / update_config 的 request_params 入参先过
    这里），让不合法的参数在落盘前就被拦下。

    Args:
        data: config.json 解析出的顶层对象（或只含参数键的子集）。
        name: 配置名（仅用于报错信息）。

    Returns:
        只含实际存在的参数键的字典（值保持原样，不做数值强转）。

    Raises:
        ConfigError: 某个参数键存在但类型不对（bool 不算数字 / 整数；extra_body 须为对象）。
    """
    params: dict[str, object] = {}
    for key in _REQUEST_PARAM_KEYS:
        raw = data.get(key)
        if raw is None:
            continue
        if key in _FLOAT_PARAM_KEYS:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ConfigError(f"端点配置「{name}」的 {key} 应是数字；请检查内容。")
        elif key in _INT_PARAM_KEYS:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ConfigError(f"端点配置「{name}」的 {key} 应是整数；请检查内容。")
        elif key in _BOOL_PARAM_KEYS:
            if not isinstance(raw, bool):
                raise ConfigError(
                    f"端点配置「{name}」的 {key} 应是 true / false；请检查内容。"
                )
        else:  # extra_body：透传对象
            if not isinstance(raw, dict):
                raise ConfigError(
                    f"端点配置「{name}」的 {key} 应是 JSON 对象；请检查内容。"
                )
        params[key] = raw
    return params


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
        id: 内部稳定 ID（endpoints/ 下的目录名，不随改名变化）。
        name: 显示名（可改、允许重名）。
        base_url: 端点地址。
        model: 模型名。
        api_format: API 调用格式（一期仅 OpenAI Chat Completions）。
        has_api_key: 该配置是否已存密钥（只报有无，绝不回内容）。
        is_active: 是否为当前使用的配置（active 指针指向它）。
        request_params: 已设置的请求参数（生成 + 传输；只含实际存在的键，值已经过
            validated_request_params 类型校验）。
    """

    id: str
    name: str
    base_url: str
    model: str
    api_format: str
    has_api_key: bool
    is_active: bool
    request_params: Mapping[str, object]


def _generate_id() -> str:
    """生成字母开头的随机短 ID（目录名即 ID；字母开头避免被 CLI 解析为选项）。"""
    return "e" + secrets.token_urlsafe(8)[:10]


def _validate_display_name(raw: str) -> str:
    """校验显示名：去首尾空白后非空、不超长；返回规整后的名字。

    显示名不再是文件名（目录名是 ID），文件名保留字符约束不再适用——只挡空与离谱长度，
    与策略库同口径。
    """
    cleaned = raw.strip()
    if not cleaned:
        raise ConfigError("配置名不能为空；请填写名称。")
    if len(cleaned) > _MAX_NAME_LENGTH:
        raise ConfigError(
            f"配置名过长（{len(cleaned)} 字符，上限 {_MAX_NAME_LENGTH}）——请缩短后重试。"
        )
    return cleaned


def _endpoints_root() -> Path:
    """endpoints/ 目录路径（不隐含创建——创建时机归各写操作，便于区分错误来源）。"""
    return data_root() / ENDPOINTS_DIRNAME


def _iter_entry_dirs() -> list[Path]:
    """列出 endpoints/ 下含 config.json 的配置目录（库目录不存在视为没有配置）。"""
    root = _endpoints_root()
    if not root.is_dir():
        return []
    return [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and (entry / _CONFIG_FILENAME).is_file()
    ]


def _read_config_at(path: Path, fallback_name: str) -> dict[str, object]:
    """读并校验一个 config.json（合法 JSON 对象 + base_url / model 非空）。

    Args:
        path: config.json 路径。
        fallback_name: 报错信息里指代这套配置的名字（目录名或 ID）。

    Raises:
        ConfigError: 读不了 / 非法 JSON / 顶层非对象 / 字段缺失或类型错。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"无法读取端点配置「{fallback_name}」的 config.json：{exc}"
        ) from exc
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"端点配置「{fallback_name}」的 config.json 不是合法 JSON"
            f"（第 {exc.lineno} 行第 {exc.colno} 列）；请检查语法。"
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"端点配置「{fallback_name}」的 config.json 顶层应为 JSON 对象；请检查内容。"
        )
    data = cast(dict[str, object], parsed)
    base_url = data.get("base_url")
    model = data.get("model")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError(
            f"端点配置「{fallback_name}」的 base_url 缺失或不是非空字符串；请补全。"
        )
    if not isinstance(model, str) or not model.strip():
        raise ConfigError(
            f"端点配置「{fallback_name}」的 model 缺失或不是非空字符串；请补全。"
        )
    return data


def _load_entries() -> dict[str, tuple[Path, dict[str, object]]]:
    """扫描并返回全部配置：{id: (目录路径, config.json 数据)}；顺手完成存量惰性迁移。

    迁移判据：config.json 缺 id（或不合 ID 形状）= 旧版条目（目录名即旧配置名）→
    分配 ID、把旧名写进 name 字段、原子回写，再把目录改名为 ID。已迁移条目零写操作。
    坏文件 fail loud（与旧口径一致：坏数据不该被列表悄悄藏起来）。
    """
    entries: dict[str, tuple[Path, dict[str, object]]] = {}
    for directory in _iter_entry_dirs():
        data = _read_config_at(directory / _CONFIG_FILENAME, directory.name)
        raw_id = data.get("id")
        if not isinstance(raw_id, str) or not _CONFIG_ID_RE.fullmatch(raw_id):
            raw_id = _generate_id()
            data["id"] = raw_id
        current_name = data.get("name")
        if not isinstance(current_name, str) or not current_name:
            # 旧版条目：目录名即当时的配置名——迁移进 name 字段。
            data["name"] = directory.name
        if directory.name != raw_id:
            try:
                directory.rename(_endpoints_root() / raw_id)
            except OSError as exc:
                raise ConfigError(
                    f"无法把端点配置目录「{directory.name}」改名为 ID「{raw_id}」："
                    f"{exc.strerror or exc}"
                ) from exc
            atomic_write_bytes(
                _endpoints_root() / raw_id / _CONFIG_FILENAME,
                (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        entries[raw_id] = (_endpoints_root() / raw_id, data)
    return entries


def _require_entry(cid: str) -> tuple[Path, dict[str, object]]:
    """要求引用可解析（配置 ID 优先；唯一显示名次之），返回（目录路径, 数据）。

    读取与写操作的宽容解析与 CLI 口径一致；引用健康度（has_config）保持严格 ID。

    Raises:
        ConfigNotFoundError: 引用解析不到。
    """
    entries = _load_entries()
    if cid in entries:
        return entries[cid]
    matches = [
        (key, value) for key, value in entries.items() if value[1].get("name") == cid
    ]
    if len(matches) == 1:
        return matches[0][1]
    raise ConfigNotFoundError(f"端点配置「{cid}」不存在；请检查 ID 或显示名。")


def _entry_info(
    cid: str, data: Mapping[str, object], active: str | None
) -> EndpointConfigInfo:
    """条目数据 → 概要（列表与单套读共用这一份取数规则）。"""
    name = data.get("name")
    display = name if isinstance(name, str) and name else cid
    return EndpointConfigInfo(
        id=cid,
        name=display,
        base_url=cast(str, data["base_url"]),
        model=cast(str, data["model"]),
        api_format=cast("str | None", data.get("api_format")) or SUPPORTED_API_FORMAT,
        has_api_key=_has_file_key(_endpoints_root() / cid / _CREDENTIALS_FILENAME),
        is_active=active == cid,
        request_params=validated_request_params(data, display),
    )


def config_id_by_display_name(name: str) -> str | None:
    """按显示名查唯一配置 ID；不存在或重名（不唯一）返回 None。

    供存量迁移（策略 JSON 里的旧版名字引用 → ID）与 CLI 的名称便利解析使用。
    """
    matches = [
        cid for cid, (_, data) in _load_entries().items() if data.get("name") == name
    ]
    return matches[0] if len(matches) == 1 else None


def active_config_id() -> str | None:
    """读当前使用的配置 ID；未设置（无指针文件或内容为空）返回 None。

    旧版指针内容是配置名：能在条目里按名唯一命中就解析成 ID 返回（读路径不改写指针，
    下一次显式切换自然落新格式）；命中不了按悬空处理，由调用方结合 has_config 报错。
    """
    pointer = _endpoints_root() / ACTIVE_FILENAME
    if not pointer.is_file():
        return None
    try:
        raw = pointer.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"无法读取当前配置指针 {ACTIVE_FILENAME}：{exc}") from exc
    ref = raw.strip()
    if not ref:
        return None
    if ref in _load_entries():
        return ref
    by_name = config_id_by_display_name(ref)
    return by_name if by_name is not None else ref


def has_config(cid: str) -> bool:
    """判断一套配置是否存在（按 ID）。

    ID 不存在就是不存在，不抛错——本函数服务「探一探」场景（策略引用健康度、
    入口层判断），不该反过来炸调用方。
    """
    return cid in _load_entries()


def has_stored_key(cid: str) -> bool:
    """判断一套配置是否已在 credentials 文件存了非空密钥（引用解析不到视为没有）。"""
    entries = _load_entries()
    if cid in entries:
        dir_path = entries[cid][0]
    else:
        try:
            dir_path, _ = _require_entry(cid)
        except ConfigNotFoundError:
            return False
    return _has_file_key(dir_path / _CREDENTIALS_FILENAME)


def list_configs() -> list[EndpointConfigInfo]:
    """列出全部端点配置概要（按显示名排序、不区分大小写；不含密钥内容）。

    Returns:
        配置概要列表；每套配置的 is_active 按 active 指针判定。

    Raises:
        ConfigError: 任一配置的 config.json 缺失 / 损坏 / 字段不全（fail loud，不静默跳过——
            坏数据不该被列表悄悄藏起来）。
    """
    entries = _load_entries()
    active = active_config_id()
    infos = [_entry_info(cid, data, active) for cid, (_, data) in entries.items()]
    return sorted(infos, key=lambda info: info.name.casefold())


def config_info(cid: str) -> EndpointConfigInfo:
    """读单套配置的概要（与 list_configs 走同一份取数规则）。

    入参接受配置 ID 或唯一显示名；返回的 id 恒为解析后的稳定 ID（来自数据本身，
    不抄入参——显示名引用时入参不是 ID）。

    Raises:
        ConfigNotFoundError: ID 不存在。
        ConfigError: config.json 损坏 / 字段不全。
    """
    _, data = _require_entry(cid)
    resolved = cast(str, data["id"])
    return _entry_info(resolved, data, active_config_id())


def read_config_data(cid: str) -> dict[str, object]:
    """读一套配置的 config.json 并做结构校验（合法 JSON 对象 + base_url / model 非空）。

    api_format 不在此校验：缺失视为支持格式（旧文件没有该字段），存了别的值由调用方按
    用途决定怎么处理。

    Raises:
        ConfigNotFoundError: ID 不存在。
        ConfigError: config.json 损坏 / 字段不全。
    """
    _, data = _require_entry(cid)
    return data


def read_stored_api_key(cid: str) -> SecretValue | None:
    """读指定配置已存的密钥（仅 credentials 文件，不含环境变量通道）；未配置返回 None。

    供入口层做「密钥留空沿用」（改 base_url 不必重输密钥）。与请求时的密钥解析（config
    层，环境变量优先）刻意不同：这里只看文件里的值，避免把环境变量误持久化进文件。

    Raises:
        ConfigNotFoundError: 引用解析不到。
    """
    dir_path, _ = _require_entry(cid)
    credentials = dir_path / _CREDENTIALS_FILENAME
    if not credentials.is_file():
        return None
    try:
        raw = credentials.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return SecretValue(raw) if raw else None


def read_active_files() -> tuple[str, dict[str, object], SecretValue | None]:
    """读当前使用配置的原始数据：（配置 ID, config.json 解析结果, 文件中的密钥或 None）。

    密钥只从该配置的 credentials 文件取；环境变量 DSF_API_KEY 的覆盖在 config 层做——
    那是「构建请求」的语义，不属于存储。

    Raises:
        ConfigError: 未配置任何端点 / active 指向不存在的配置 / config.json 损坏或字段不全。
    """
    cid = active_config_id()
    if cid is None:
        raise ConfigError(
            "未配置任何端点；请先用 `dsf config set` 设置，或在 Web 设置页添加端点配置。"
        )
    if not has_config(cid):
        raise ConfigError(
            f"当前使用的端点配置「{cid}」不存在；请重新选择当前使用的配置，"
            "或检查数据根下 endpoints/ 目录。"
        )
    data = read_config_data(cid)
    return cid, data, read_stored_api_key(cid)


def _require_clean(value: str, field: str) -> str:
    """去首尾空白并要求非空（base_url / model 等必填字段的统一小闸门）。"""
    cleaned = value.strip()
    if not cleaned:
        raise ConfigError(f"{field} 不能为空；请填写。")
    return cleaned


def _require_supported_format(api_format: str) -> None:
    """校验 API 调用格式是当前支持的唯一值。"""
    if api_format != SUPPORTED_API_FORMAT:
        raise ConfigError(
            f"API 格式「{api_format}」暂未支持；当前仅支持 OpenAI Chat Completions。"
        )


def _write_config_files(
    dir_path: Path,
    *,
    config_id: str,
    name: str,
    base_url: str,
    model: str,
    api_format: str,
    api_key: SecretValue | None,
    request_params: Mapping[str, object] | None = None,
    preserve_params_from: Path | None = None,
) -> None:
    """写一套配置的两个文件（config.json + credentials），各自原子写。

    Args:
        dir_path: 配置目录（= ID 目录）。
        config_id: 配置 ID（写进 config.json）。
        name: 显示名（写进 config.json）。
        base_url: 已规整的端点地址。
        model: 已规整的模型名。
        api_format: API 调用格式。
        api_key: 密钥；None = 不写 credentials（更新场景即「沿用已存密钥」）。
        request_params: 已校验的请求参数键值（None = 本调用不携带参数）。
        preserve_params_from: 给出时（更新且未显式给参数的场景），从该目录的旧
            config.json 里把已有的请求参数键原样搬进新 payload，避免改端点抹掉用户
            手配的参数。

    Raises:
        ConfigError: 旧参数读不了 / 内容无法编码 / 落盘失败。
    """
    payload: dict[str, object] = {
        "id": config_id,
        "name": name,
        "base_url": base_url,
        "model": model,
        "api_format": api_format,
    }
    if request_params is not None:
        for key in _REQUEST_PARAM_KEYS:
            if key in request_params:
                payload[key] = request_params[key]
    elif preserve_params_from is not None:
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


def create_config(
    name: str,
    base_url: str,
    model: str,
    api_key: SecretValue | None,
    api_format: str = SUPPORTED_API_FORMAT,
    request_params: Mapping[str, object] | None = None,
) -> str:
    """新增一套端点配置；当前没有生效的 active 指针时，顺手把它设为当前使用。

    显示名允许重名（身份是 ID）；自动激活只发生在「指针缺失」时（典型：第一套配置，
    创建完就能用）。

    Returns:
        新配置的 ID——目录即此 ID，入口层组装响应用它。

    Raises:
        ConfigError: 显示名不合法 / 字段为空 / 格式不支持 / 参数类型不合法 / 落盘失败。
    """
    display = _validate_display_name(name)
    clean_base_url = _require_clean(base_url, "base_url")
    clean_model = _require_clean(model, "model")
    _require_supported_format(api_format)
    if api_key is not None and not api_key.reveal().strip():
        raise ConfigError("API 密钥不能为空白；请填写有效密钥。")
    params_payload = (
        validated_request_params(request_params, display)
        if request_params is not None
        else None
    )
    root = _endpoints_root()
    cid = _generate_id()
    while (root / cid).exists():  # 撞名重摇（概率趋近于零，防御性兜底）
        cid = _generate_id()
    _write_config_files(
        root / cid,
        config_id=cid,
        name=display,
        base_url=clean_base_url,
        model=clean_model,
        api_format=api_format,
        api_key=api_key,
        request_params=params_payload,
    )
    if active_config_id() is None:
        set_active_config(cid)
    return cid


def update_config(
    cid: str,
    base_url: str,
    model: str,
    api_key: SecretValue | None = None,
    api_format: str = SUPPORTED_API_FORMAT,
    request_params: Mapping[str, object] | None = None,
) -> str:
    """更新一套已存在配置的端点字段；api_key 传 None 表示沿用该配置已存的密钥。

    「沿用」= 不动 credentials 文件（而不是把环境变量或其他配置的密钥抄过来）。
    显示名不在这里改（改名走 rename_config，语义分开）。请求参数的更新语义：
    request_params 缺省（None）= 已有参数原样保留（与密钥的沿用同一套心智）；显式
    给出 = **整体替换**该配置的请求参数块。

    Returns:
        配置 ID（解析后的稳定 ID；显示名引用时 = 命中的条目 ID）。

    Raises:
        ConfigNotFoundError: 引用解析不到。
        ConfigError: 字段为空 / 格式不支持 / 参数类型不合法 / 落盘失败。
    """
    dir_path, existing = _require_entry(cid)
    cid = cast(str, existing["id"])
    current_name = existing.get("name")
    display = current_name if isinstance(current_name, str) and current_name else cid
    clean_base_url = _require_clean(base_url, "base_url")
    clean_model = _require_clean(model, "model")
    _require_supported_format(api_format)
    if api_key is not None and not api_key.reveal().strip():
        raise ConfigError("API 密钥不能为空白；请填写有效密钥。")
    params_payload = (
        validated_request_params(request_params, display)
        if request_params is not None
        else None
    )
    _write_config_files(
        dir_path,
        config_id=cid,
        name=display,
        base_url=clean_base_url,
        model=clean_model,
        api_format=api_format,
        api_key=api_key,
        request_params=params_payload,
        preserve_params_from=dir_path if params_payload is None else None,
    )
    return cid


def rename_config(cid: str, new_name: str) -> str:
    """改一套配置的显示名：只写 config.json 的 name 字段。

    目录名是 ID、指针存 ID、引用存 ID——改名不再有任何文件系统操作或指针同步，
    「改名炸引用」在结构上不可能。同名词用 = 无操作返回（写一次同值，无副作用）。

    Returns:
        配置 ID（不变）。

    Raises:
        ConfigNotFoundError: ID 不存在。
        ConfigError: 显示名不合法 / 落盘失败。
    """
    dir_path, existing = _require_entry(cid)
    display = _validate_display_name(new_name)
    data: dict[str, object] = dict(existing)
    data["name"] = display
    try:
        atomic_write_bytes(
            dir_path / _CONFIG_FILENAME,
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    except OSError as exc:
        raise ConfigError(f"无法写入端点配置文件：{exc.strerror or exc}") from exc
    return cid


def delete_config(cid: str) -> None:
    """删除一套端点配置（连同其 credentials）；当前使用中的配置不允许删。

    Raises:
        ConfigNotFoundError: ID 不存在。
        ConfigConflictError: 试图删除当前使用中的配置。
        ConfigError: 删除失败。
    """
    dir_path, data = _require_entry(cid)
    resolved = cast(str, data["id"])
    active = active_config_id()
    if active is not None and active == resolved:
        raise ConfigConflictError(
            f"「{data.get('name') or resolved}」是当前使用的配置；请先切换到其他配置再删除。"
        )
    try:
        shutil.rmtree(dir_path)
    except OSError as exc:
        raise ConfigError(f"无法删除端点配置「{cid}」：{exc.strerror or exc}") from exc


def set_active_config(cid: str) -> None:
    """把当前使用指针指向一套已存在的配置（指针存 ID）；对新请求立即生效。

    Raises:
        ConfigError: ID 不存在 / 指针写入失败。
    """
    _require_entry(cid)
    pointer = _endpoints_root() / ACTIVE_FILENAME
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(pointer, f"{cid}\n".encode())
    except (OSError, UnicodeEncodeError) as exc:
        raise ConfigError(f"无法写入当前配置指针：{exc}") from exc


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
