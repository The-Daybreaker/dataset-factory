"""产品内置的预置提示词（开箱可用）——本模块是内置条目内容的唯一事实来源。

首次使用（数据根 prompts/ 下没有播种标记文件）时由 `seed_builtin_presets` 把内置条目
写进提示词库；播种是一次性的（标记文件落盘后不再触发），用户之后可自由修改 / 删除内置
条目，不会被覆盖或复活。写法依据 archive/research 的打标提示词调研（JoyCaption 提示词
清单 / WD14 tagger / 本项目 RESEARCH-0003/0004 的训练侧内容惯例）。
"""

from __future__ import annotations

from .model import Prompt

# 内置预置集合的版本：写进播种标记文件；将来增补内置条目时 bump 它并让播种补写缺失项。
BUILTIN_PRESET_VERSION = "1"

_BODY = "\n".join(
    [
        "请为这张图片写一段用于 LoRA 训练的画面描述（caption）。",
        "",
        "要求：",
        "- 客观描述画面中可见的内容，不臆测画外信息；",
        "- 覆盖主体、动作、服装、构图、光影与画面风格；",
        "- 用三到五句自然连贯的话书写，不分点、不加标题、不输出任何多余说明。",
    ]
)

BUILTIN_PROMPTS: tuple[Prompt, ...] = (
    Prompt(
        name="详细描述",
        description="通用主力——三到五句自然语言详细描述，覆盖主体到光影风格。",
        body=_BODY,
    ),
)
