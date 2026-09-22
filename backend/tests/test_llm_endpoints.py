"""单元测试：端点多配置存储（endpoints/ 目录、active 指针、ID 身份）。

全部离线；temp_data_root 把数据根隔离到临时目录。覆盖：CRUD 与设为当前使用、
ID 身份与显示名解耦（改名只写字段、允许重名）、旧版数据（名字身份）的读时迁移、
请求参数校验（含 enable_thinking 一等布尔）、密钥只进不出。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataset_factory.llm import (
    SUPPORTED_API_FORMAT,
    ConfigError,
    ConfigNotFoundError,
    SecretValue,
    active_config_id,
    config_id_by_display_name,
    config_info,
    create_config,
    delete_config,
    has_config,
    has_stored_key,
    list_configs,
    read_stored_api_key,
    rename_config,
    set_active_config,
    update_config,
)


def _create(name: str, key: str = "sk-key") -> str:
    """测试便捷封装：建一套带密钥的配置，返回其 ID。"""
    return create_config(
        name,
        base_url=f"https://{name}.example.com/v1",
        model=f"m-{name}",
        api_key=SecretValue(key),
    )


# ---------- 创建与列表 ----------


def test_create_writes_files_and_auto_activates(temp_data_root: Path) -> None:
    """创建第一套配置：目录名即 ID、config.json 带 id+name、active 指针指向它。"""
    cid = _create("default")

    endpoint_dir = temp_data_root / "endpoints" / cid
    assert (endpoint_dir / "config.json").is_file()
    assert (endpoint_dir / "credentials").is_file()
    assert active_config_id() == cid
    assert cid not in ("default",)  # ID 是随机短 ID，不是显示名

    saved = json.loads((endpoint_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["id"] == cid
    assert saved["name"] == "default"
    assert saved["base_url"] == "https://default.example.com/v1"
    assert saved["model"] == "m-default"
    assert saved["api_format"] == SUPPORTED_API_FORMAT


def test_create_second_does_not_steal_active(temp_data_root: Path) -> None:
    """创建第二套配置不抢当前使用权；列表里只有第一套标 active。"""
    first = _create("alpha")
    _create("beta")

    assert active_config_id() == first
    infos = {info.id: info for info in list_configs()}
    assert infos[first].is_active
    other = next(i for i in infos if i != first)
    assert not infos[other].is_active


def test_list_sorted_by_display_name_casefold(temp_data_root: Path) -> None:
    """列表按显示名排序（不区分大小写）：大写排在小写前面按字母序而非 ASCII 码位。"""
    _create("beta")
    _create("Alpha")
    _create("charlie")

    assert [info.name for info in list_configs()] == ["Alpha", "beta", "charlie"]


def test_display_name_duplicates_allowed(temp_data_root: Path) -> None:
    """显示名允许重名（身份是 ID）：同名两套配置并存、ID 各自独立。"""
    first = _create("same")
    second = _create("same")

    assert first != second
    assert has_config(first)
    assert has_config(second)
    assert config_id_by_display_name("same") is None  # 重名 → 名字不再能唯一定位


def test_config_info_matches_list_entry(temp_data_root: Path) -> None:
    """单套读与列表读给出逐字段相同的概要（两条读路共用一份取数规则）。"""
    cid = create_config(
        "full",
        base_url="https://full/v1",
        model="m-full",
        api_key=SecretValue("sk-full"),
        request_params={"temperature": 0.3, "extra_body": {"think": False}},
    )
    create_config("nokey", base_url="https://no/v1", model="m-no", api_key=None)
    legacy_dir = temp_data_root / "endpoints" / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        json.dumps({"base_url": "https://legacy/v1", "model": "m-legacy"}),
        encoding="utf-8",
    )

    single = config_info(cid)
    infos = {info.id: info for info in list_configs()}
    assert single == infos[cid]
    # 旧版裸 config.json（无 id/name）：迁移补 ID、目录名转显示名。
    legacy = next(info for info in infos.values() if info.name == "legacy")
    assert legacy.base_url == "https://legacy/v1"
    assert legacy.model == "m-legacy"
    assert (temp_data_root / "endpoints" / "legacy").exists() is False


def test_empty_root_lists_nothing(temp_data_root: Path) -> None:
    """空数据根：列表为空、active 为 None，不算错。"""
    assert list_configs() == []
    assert active_config_id() is None


# ---------- 显示名规则 ----------


def test_display_name_rules(temp_data_root: Path) -> None:
    """显示名：去首尾空白、非空、不超长；文件名保留字符不再受限（身份是 ID）。"""
    cid = _create("  padded  ")
    assert config_info(cid).name == "padded"

    with pytest.raises(ConfigError, match="不能为空"):
        create_config("   ", base_url="https://x/v1", model="m", api_key=None)
    with pytest.raises(ConfigError, match="过长"):
        create_config("长" * 101, base_url="https://x/v1", model="m", api_key=None)
    # 冒号等文件名保留字符现在合法（只是显示别名）。
    colon = _create("bad:name")
    assert config_info(colon).name == "bad:name"


# ---------- 更新与参数 ----------


def test_update_changes_fields_and_keeps_key_and_params(temp_data_root: Path) -> None:
    """更新：端点字段换新；不带密钥沿用已存密钥；显示名与用户手配的请求参数原样保留。"""
    cid = _create("prod", key="sk-keep-me")
    config_path = temp_data_root / "endpoints" / cid / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["temperature"] = 0.3
    data["timeout_seconds"] = 300
    config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    returned = update_config(cid, base_url="https://new/v1", model="new-model")

    assert returned == cid
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://new/v1"
    assert saved["model"] == "new-model"
    assert saved["name"] == "prod"  # 显示名不被 update 触碰
    assert saved["temperature"] == 0.3
    assert saved["timeout_seconds"] == 300
    stored = read_stored_api_key(cid)
    assert stored is not None
    assert stored.reveal() == "sk-keep-me"


def test_create_with_request_params_writes_them(temp_data_root: Path) -> None:
    """创建时携带请求参数：给的键写入 config.json；键集外的键被存储闸门丢弃。"""
    cid = create_config(
        "tuned",
        base_url="https://tuned.example.com/v1",
        model="m-tuned",
        api_key=SecretValue("sk-k"),
        request_params={
            "temperature": 0.7,
            "max_tokens": 1024,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            "presence_penalty": 0.1,  # 不在参数键集里：丢弃而非报错（透传请放 extra_body）
        },
    )

    saved = json.loads(
        (temp_data_root / "endpoints" / cid / "config.json").read_text(encoding="utf-8")
    )
    assert saved["temperature"] == 0.7
    assert saved["max_tokens"] == 1024
    assert saved["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "presence_penalty" not in saved
    assert "timeout_seconds" not in saved  # 没给的键不出现


def test_enable_thinking_persists_as_first_class_param(temp_data_root: Path) -> None:
    """思考开关是一等参数：布尔值进 config.json、读侧带出（B 方案，2026-09-23）。"""
    cid = create_config(
        "think-off",
        base_url="https://sf.example.com/v1",
        model="Qwen/Qwen3.5-4B",
        api_key=None,
        request_params={"enable_thinking": False},
    )

    saved = json.loads(
        (temp_data_root / "endpoints" / cid / "config.json").read_text(encoding="utf-8")
    )
    assert saved["enable_thinking"] is False
    infos = {info.id: info for info in list_configs()}
    assert infos[cid].request_params == {"enable_thinking": False}


def test_enable_thinking_bad_type_raises(temp_data_root: Path) -> None:
    """思考开关非布尔：创建即拒绝（布尔闸门与数值闸门同一道边界）。"""
    with pytest.raises(ConfigError, match="enable_thinking 应是 true / false"):
        create_config(
            "bad",
            base_url="https://bad.example.com/v1",
            model="m-bad",
            api_key=None,
            request_params={"enable_thinking": "false"},
        )


def test_update_with_request_params_replaces_block(temp_data_root: Path) -> None:
    """更新时显式给参数块 = 整体替换：未提供的旧参数被清除（「给什么存什么」）。"""
    cid = _create("prod")
    config_path = temp_data_root / "endpoints" / cid / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["temperature"] = 0.3
    data["timeout_seconds"] = 300
    config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    update_config(
        cid,
        base_url="https://prod.example.com/v1",
        model="m-prod",
        request_params={"max_retries": 5},
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["max_retries"] == 5
    assert "temperature" not in saved
    assert "timeout_seconds" not in saved


def test_list_reports_request_params(temp_data_root: Path) -> None:
    """列表概要带出已设置的请求参数（已过类型校验）；未设置时为空映射。"""
    create_config(
        "a",
        base_url="https://a.example.com/v1",
        model="m-a",
        api_key=None,
        request_params={"temperature": 0.5, "extra_body": {"top_k": 40}},
    )
    _create("b")

    infos = {info.id: info for info in list_configs()}
    a = next(info for info in infos.values() if info.name == "a")
    assert a.request_params == {
        "temperature": 0.5,
        "extra_body": {"top_k": 40},
    }
    b = next(info for info in infos.values() if info.name == "b")
    assert b.request_params == {}


def test_update_with_new_key_overwrites_credentials(temp_data_root: Path) -> None:
    """更新时给了新密钥：credentials 被替换。"""
    cid = _create("prod", key="sk-old")

    update_config(
        cid,
        base_url="https://new/v1",
        model="m",
        api_key=SecretValue("sk-brand-new"),
    )

    stored = read_stored_api_key(cid)
    assert stored is not None
    assert stored.reveal() == "sk-brand-new"


def test_update_missing_config_raises(temp_data_root: Path) -> None:
    """更新不存在的配置 → ConfigNotFoundError（接口层据此映射 404）。"""
    with pytest.raises(ConfigNotFoundError, match="不存在"):
        update_config("eghost00001", base_url="https://x/v1", model="m")


# ---------- 改名（只写字段） ----------


def test_rename_writes_display_name_only(temp_data_root: Path) -> None:
    """改名：只写 config.json 的 name 字段——目录、ID、指针、密钥全部不动。"""
    cid = _create("old-name", key="sk-stays")

    returned = rename_config(cid, "new-name")

    assert returned == cid
    endpoint_dir = temp_data_root / "endpoints" / cid
    assert endpoint_dir.is_dir()  # 目录仍是 ID，未动
    saved = json.loads((endpoint_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["name"] == "new-name"
    assert active_config_id() == cid  # 指针存 ID，不受改名影响
    assert has_stored_key(cid) is True
    stored = read_stored_api_key(cid)
    assert stored is not None
    assert stored.reveal() == "sk-stays"


def test_rename_to_existing_display_name_allowed(temp_data_root: Path) -> None:
    """改成已有的显示名：允许重名（身份是 ID），两套并存。"""
    first = _create("alpha")
    second = _create("beta")

    rename_config(second, "alpha")

    names = sorted(info.name for info in list_configs())
    assert names == ["alpha", "alpha"]
    assert has_config(first)
    assert has_config(second)


def test_rename_missing_config_raises(temp_data_root: Path) -> None:
    """改不存在的配置 → ConfigNotFoundError。"""
    with pytest.raises(ConfigNotFoundError, match="不存在"):
        rename_config("eghost00001", "anywhere")


def test_rename_to_invalid_display_name_raises(temp_data_root: Path) -> None:
    """空显示名 → ConfigError，配置不受影响。"""
    cid = _create("source")

    with pytest.raises(ConfigError, match="不能为空"):
        rename_config(cid, "   ")

    assert has_config(cid) is True


# ---------- 删除与切换 ----------


def test_delete_refuses_active_and_allows_others(temp_data_root: Path) -> None:
    """删除当前使用中的配置被拒；切换后可删；目录连同 credentials 一起消失。"""
    from dataset_factory.llm import ConfigConflictError

    first = _create("a")
    second = _create("b")

    with pytest.raises(ConfigConflictError, match="当前使用"):
        delete_config(first)

    set_active_config(second)
    delete_config(first)
    assert has_config(first) is False
    assert active_config_id() == second


def test_set_active_requires_existing(temp_data_root: Path) -> None:
    """切换到不存在的 ID → ConfigNotFoundError（指针绝不悬空写出）。"""
    from dataset_factory.llm import ConfigNotFoundError

    with pytest.raises(ConfigNotFoundError):
        set_active_config("eghost00001")


# ---------- 旧版数据迁移 ----------


def test_legacy_config_migrates_to_id(temp_data_root: Path) -> None:
    """旧版数据（目录名=名字、config.json 无 id）：读时惰性迁移，内容不丢。"""
    endpoint_dir = temp_data_root / "endpoints" / "旧配置名"
    endpoint_dir.mkdir(parents=True)
    (endpoint_dir / "config.json").write_text(
        json.dumps(
            {
                "base_url": "https://legacy.example.com/v1",
                "model": "m-legacy",
                "extra_body": {"top_k": 40},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (endpoint_dir / "credentials").write_text("sk-legacy", encoding="utf-8")
    (temp_data_root / "endpoints" / "active").write_text("旧配置名\n", encoding="utf-8")

    infos = list_configs()

    assert len(infos) == 1
    migrated = infos[0]
    assert migrated.id.startswith("e")
    assert migrated.name == "旧配置名"  # 旧目录名 → 显示名
    assert migrated.base_url == "https://legacy.example.com/v1"
    assert migrated.request_params == {"extra_body": {"top_k": 40}}
    assert migrated.is_active is True  # 旧指针按名解析成新 ID
    assert active_config_id() == migrated.id
    # 目录已改名为 ID、旧目录消失；密钥与参数随目录走。
    assert (temp_data_root / "endpoints" / "旧配置名").exists() is False
    stored = read_stored_api_key(migrated.id)
    assert stored is not None
    assert stored.reveal() == "sk-legacy"
    saved = json.loads(
        (temp_data_root / "endpoints" / migrated.id / "config.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["id"] == migrated.id
    assert saved["name"] == "旧配置名"
    # 幂等：再次读取不再变化。
    again = list_configs()
    assert [info.id for info in again] == [migrated.id]


def test_legacy_active_pointer_resolved_by_name(temp_data_root: Path) -> None:
    """已迁移配置 + 旧版指针（存的是名字）：active 按名解析回该配置的 ID。"""
    cid = _create("pointer-name")
    # 手工把指针回写成旧版形态（名字），模拟「迁移前指针、迁移后条目」。
    (temp_data_root / "endpoints" / "active").write_text(
        "pointer-name\n", encoding="utf-8"
    )

    assert active_config_id() == cid
