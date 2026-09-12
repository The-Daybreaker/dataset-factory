"""api 入口层的请求 / 响应模型（pydantic）——HTTP 边界的运行时校验。

字段类型错 → FastAPI 自动 422（一次报全部校验错误，PRD 验收 13）；业务规则（提示词
不存在、图片非法等）由核心库的域异常给出、错误映射表翻译成 HTTP 状态码。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm import SUPPORTED_API_FORMAT


class ErrorDetail(BaseModel):
    """错误响应的统一形状——所有域错误都长这样（`{"detail": "一句话"}`）。

    各端点在 `responses=` 里引用它，把错误路径写进 OpenAPI 契约：
    前端生成类型时才能覆盖错误体，而不是只看到成功路径。
    """

    detail: str


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


class SkillFileInfo(BaseModel):
    """技能包内单个文件的条目（预览清单用）。"""

    path: str = Field(
        description="包内相对路径（POSIX 风格，如 SKILL.md、references/h3.md）"
    )
    role: str = Field(
        description="角色：skill=注入源 / reference=参考资料（两者可预览）；"
        "asset、script、other=不参与注入且不可预览"
    )
    previewable: bool = Field(description="是否可通过文件内容端点预览")


class SkillFilesResponse(BaseModel):
    """GET /api/skills/{name}/files 的响应体。"""

    name: str
    files: list[SkillFileInfo]


class SkillFileContent(BaseModel):
    """GET /api/skills/{name}/files/{path} 的响应体——UTF-8 文本内容。"""

    path: str
    content: str


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

    name: str | None = None
    base_url: str | None
    model: str | None
    api_key_configured: bool
    key_source: str | None


class ConfigUpdateRequest(BaseModel):
    """PUT /api/config 的请求体——api_key 缺省沿用现有密钥（不强迫重输）。"""

    base_url: str
    model: str
    api_key: str | None = None


class EndpointConfigSummary(BaseModel):
    """端点配置概要——列表 / 创建 / 更新的响应体，密钥只报有无、绝不回内容。"""

    name: str = Field(description="配置名（endpoints/ 下的目录名）")
    base_url: str = Field(description="端点地址")
    model: str = Field(description="模型名")
    api_format: str = Field(
        description="API 调用格式（一期仅 OpenAI Chat Completions）"
    )
    has_api_key: bool = Field(description="是否已存密钥（只报有无）")
    is_active: bool = Field(description="是否为当前使用的配置")


class EndpointCreateRequest(BaseModel):
    """POST /api/endpoints 的请求体——新增一套配置；api_key 缺省暂不配置（可用环境变量兜底）。"""

    name: str = Field(description="配置名（即目录名，1–64 字符、不含路径保留字符）")
    base_url: str
    model: str
    api_key: str | None = None
    api_format: str = SUPPORTED_API_FORMAT


class EndpointUpdateRequest(BaseModel):
    """PUT /api/endpoints/{name} 的请求体——api_key 缺省沿用已存密钥（不强迫重输）。"""

    base_url: str
    model: str
    api_key: str | None = None
    api_format: str = SUPPORTED_API_FORMAT
