"""提示词条目的数据模型 + md/frontmatter 序列化——本模块是磁盘格式的唯一事实来源。

条目落盘为一个 md 文件：开头是 YAML frontmatter（`---` 与 `---` 之间，含 name（显示名）
与 description 两个字段），随后紧跟正文（提示词本体）。序列化（dump_prompt）与解析
（parse_prompt）成对，保证写出去什么、读回来就是什么（round-trip 一致）；golden 契约
测试用一份手写样例把这个磁盘格式钉死，防两侧一起漂移。

身份与名字（2026-09-23 ID 化）：文件名 = 内部稳定 ID（创建时分配，不随改名变化）；
frontmatter 的 name = 显示名（可改、允许重名）。「改名 = 改 frontmatter 一个字段」，
文件名永不动——引用（策略 / 会话设置 / 快照）一律存 ID。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

import yaml

from .errors import PromptParseError

# frontmatter 分隔符：文件以单独一行 "---" 开头，到下一个单独一行 "---" 结束。
_DELIMITER = "---"

# frontmatter 只认这两个字段；出现别的键即报错（挡住手误，如把 description 拼错）。
# name（显示名）是 2026-09-23 ID 化新增的：旧版条目没有它，由迁移补写。
_ALLOWED_FRONTMATTER_KEYS = frozenset({"name", "description"})

# 提示词 ID 形状（文件名即 ID；与策略 / 端点 ID 同一模式，字母开头避免被 CLI 解析为选项）。
PROMPT_ID_RE = re.compile(r"^[a-z][A-Za-z0-9_-]{10}$")


@dataclass(frozen=True)
class Prompt:
    """一条提示词。

    Attributes:
        name: 显示名（可改、允许重名）。
        description: 选择器里说明这条提示词产出什么、适合什么；可为空串。
        body: 提示词正文（Markdown 文本）。
        id: 内部稳定 ID（= 文件名去掉 .md；不随改名变化）。内置预置模板不落库时为空串。
    """

    name: str
    description: str
    body: str
    id: str = ""


def dump_prompt(prompt: Prompt) -> str:
    """把 Prompt 序列化成落盘文本：YAML frontmatter（name + description）+ 正文。

    Args:
        prompt: 要序列化的提示词。

    Returns:
        md 文本：一行 ---、name 与 description 两行、再一行 ---，随后紧跟正文（正文与
        结束的 --- 之间不留空行，以保证与 parse_prompt 精确 round-trip）。
    """
    front = yaml.safe_dump(
        {"name": prompt.name, "description": prompt.description},
        allow_unicode=True,
        default_flow_style=False,
    ).strip("\n")
    return f"{_DELIMITER}\n{front}\n{_DELIMITER}\n{prompt.body}"


def parse_prompt(entry_id: str, text: str, fallback_name: str | None = None) -> Prompt:
    """把落盘文本解析回 Prompt：拆 frontmatter、校验字段、余下即正文。

    宽容与严格的分界：文件不以 ``---`` 开头 = 没有 frontmatter（宽容，description 视为空、
    全文即正文，方便手建的简单条目；显示名回落 fallback 或 ID）；一旦以 ``---`` 开头就
    必须有闭合的 ``---``（否则报错），且 frontmatter 必须是只含 name / description 的
    YAML 映射（挡住手误与未知字段，fail loud）。缺 name 字段 = 旧版条目（尚未迁移），
    显示名回落 fallback（迁移前 = 文件名）。

    Args:
        entry_id: 条目 ID（从文件名得来，仅用于填 Prompt.id）。
        text: 文件全文。
        fallback_name: frontmatter 缺 name 时的显示名回落（通常是迁移前的旧文件名）。

    Returns:
        解析出的 Prompt。

    Raises:
        PromptParseError: frontmatter 未闭合、YAML 不合法、顶层不是映射、字段类型错、
            或含 name / description 以外的字段。
    """
    label = fallback_name or entry_id
    if not text.startswith(f"{_DELIMITER}\n"):
        return Prompt(id=entry_id, name=label, description="", body=text)
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i] == _DELIMITER), None)
    if end is None:
        raise PromptParseError(
            f"提示词 {label!r} 的 frontmatter 未闭合（开头有 ---，但找不到结束的 ---）；请补全。"
        )
    front_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    try:
        loaded: object = yaml.safe_load(front_text)
    except yaml.YAMLError as exc:
        raise PromptParseError(
            f"提示词 {label!r} 的 frontmatter 不是合法 YAML；请检查语法。"
        ) from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise PromptParseError(
            f"提示词 {label!r} 的 frontmatter 顶层应是键值映射（如 description: ...）；请检查内容。"
        )
    meta = cast(dict[object, object], loaded)
    unknown = sorted(
        str(key) for key in meta if str(key) not in _ALLOWED_FRONTMATTER_KEYS
    )
    if unknown:
        raise PromptParseError(
            f"提示词 {label!r} 的 frontmatter 含未知字段 {unknown}；"
            "只支持 name（显示名）与 description。"
        )
    description = meta.get("description", "")
    if not isinstance(description, str):
        raise PromptParseError(
            f"提示词 {label!r} 的 description 应是字符串；请检查内容。"
        )
    raw_name = meta.get("name")
    if raw_name is not None and not isinstance(raw_name, str):
        raise PromptParseError(f"提示词 {label!r} 的 name 应是字符串；请检查内容。")
    name = raw_name if isinstance(raw_name, str) and raw_name else label
    return Prompt(id=entry_id, name=name, description=description, body=body)
