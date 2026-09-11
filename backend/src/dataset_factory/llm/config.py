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
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ENV_API_KEY = "DSF_API_KEY"  # pragma: allowlist secret —— 环境变量名常量、非密钥值（辅通道，优先覆盖 credentials 文件）
ENV_HOME = "DATASET_FACTORY_HOME"  # 数据根覆盖（默认 ~/.dataset_factory）

_HOME_DIRNAME = ".dataset_factory"
_CONFIG_FILENAME = "config.json"
_CREDENTIALS_FILENAME = (
    "credentials"  # pragma: allowlist secret —— 文件名常量、非密钥值
)
_MASK = "**********"


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
class EndpointConfig:
    """构建 API 客户端所需的端点三要素。

    api_key 为 SecretValue，因此本对象自动生成的 repr 也不会泄露密钥。
    """

    base_url: str
    model: str
    api_key: SecretValue


def data_root() -> Path:
    """数据根目录：环境变量 DATASET_FACTORY_HOME 覆盖，否则 ~/.dataset_factory。"""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / _HOME_DIRNAME


def read_config() -> EndpointConfig:
    """读取端点配置 + 密钥，组装成 EndpointConfig。

    数据根由 data_root() 决定；测试用 temp_data_root fixture 设 DATASET_FACTORY_HOME 隔离真实目录。
    """
    root = data_root()
    base_url, model = _read_config_json(root / _CONFIG_FILENAME)
    api_key = _resolve_api_key(root / _CREDENTIALS_FILENAME)
    return EndpointConfig(base_url=base_url, model=model, api_key=api_key)


def _read_config_json(path: Path) -> tuple[str, str]:
    """从 config.json 读端点配置。

    Args:
        path: config.json 的路径。

    Returns:
        (base_url, model) 两个非空字符串。

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
    return base_url, model


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
    config_json = (
        json.dumps({"base_url": base_url, "model": model}, ensure_ascii=False, indent=2)
        + "\n"
    )
    _atomic_write_text(root / _CONFIG_FILENAME, config_json)
    _atomic_write_text(root / _CREDENTIALS_FILENAME, api_key)


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写文本文件：同目录临时文件 + fsync + os.replace。

    先把内容写进同目录的临时文件、刷盘，再用 os.replace 改名成目标文件——外界要么
    看到旧文件、要么看到新文件，绝不会看到写了一半的损坏文件。几点关键：

    - 临时文件必须和目标同目录：os.replace 的原子性只在同一文件系统内成立；
    - 用 os.replace 而非 os.rename：Windows 上目标已存在时 rename 会失败，
      replace 两平台都能原子覆盖；
    - tempfile.mkstemp 在 Unix 上默认以 0600 建文件（仅本人可读写），密钥天然受
      保护；Windows 靠用户主目录默认 ACL 隔离。

    Args:
        path: 目标文件路径。
        text: 要写入的文本内容。

    Raises:
        ConfigError: 目录无法创建，或临时文件写入 / 改名等底层 OSError。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
    except OSError as exc:
        raise ConfigError(
            f"无法在 {path.parent} 准备写入：{exc.strerror or exc}"
        ) from exc
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ConfigError(f"无法写入 {path}：{exc.strerror or exc}") from exc
