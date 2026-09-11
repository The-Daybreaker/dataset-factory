"""api 入口层的请求 / 响应模型（pydantic）——HTTP 边界的运行时校验。

字段类型错 → FastAPI 自动 422（一次报全部校验错误，PRD 验收 13）；业务规则（提示词
不存在、图片非法等）由核心库的域异常给出、错误映射表翻译成 HTTP 状态码。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LabelRequest(BaseModel):
    """POST /api/label 的请求体。"""

    session_id: str | None = Field(default=None, description="续接的会话 id；缺省新建")
    prompt_name: str | None = Field(
        default=None, description="基础提示词名称；续接时缺省沿用会话设置"
    )
    skill_names: list[str] | None = Field(
        default=None, description="启用的 skill 清单；缺省沿用会话设置"
    )
    instruction: str = Field(default="", description="打标指令（纯图轮可空）")
    image_base64: str | None = Field(
        default=None, description="图片（data URL 或纯 base64）；缺省纯文本轮"
    )
    image_name: str = Field(default="image.png", description="图片原始文件名")


class LabelResponse(BaseModel):
    """POST /api/label 的响应体。"""

    session_id: str
    caption: str


class SettingsView(BaseModel):
    """会话当前设置。"""

    prompt_name: str | None
    skill_names: list[str]


class HistoryMessageView(BaseModel):
    """一条历史消息。"""

    role: str
    text: str
    attachment: str | None


class SessionSnapshotResponse(BaseModel):
    """GET /api/sessions/* 的响应体（恢复会话的完整快照）。"""

    session_id: str
    settings: SettingsView
    messages: list[HistoryMessageView]


class PromptInfo(BaseModel):
    """提示词条目（列表项）。"""

    name: str
    description: str


class PromptFull(PromptInfo):
    """提示词全文。"""

    body: str


class PromptSaveRequest(BaseModel):
    """PUT /api/prompts/{name} 的请求体。"""

    description: str = ""
    body: str


class SkillInfo(BaseModel):
    """skill 条目。"""

    name: str
    description: str
    enabled: bool


class SkillImportRequest(BaseModel):
    """POST /api/skills/import 的请求体。"""

    path: str = Field(description="skill 目录的本地路径（服务端可访问）")


class SkillImportResponse(BaseModel):
    """导入结果。"""

    name: str
    description: str
    enabled: bool
    total_bytes: int


class ConfigResponse(BaseModel):
    """GET /api/config 的响应体——密钥只报来源、绝不回内容。"""

    base_url: str | None
    model: str | None
    api_key_configured: bool
    key_source: str | None


class ConfigUpdateRequest(BaseModel):
    """PUT /api/config 的请求体——api_key 缺省沿用现有密钥（不强迫重输）。"""

    base_url: str
    model: str
    api_key: str | None = None
