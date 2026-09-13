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
    video_base64: str | None = Field(
        default=None,
        description="视频（data URL 或纯 base64）；与图片互斥（一期单素材/次）",
    )
    video_name: str = Field(default="video.mp4", description="视频原始文件名")
    video_fps: float = Field(
        default=2.0, ge=0.1, le=10.0, description="视频抽帧 fps（请求值，端点把关上限）"
    )
    video_max_frames: int = Field(
        default=16, ge=1, le=256, description="视频抽帧帧数上限"
    )


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


class ServiceStatus(BaseModel):
    """GET /api/service 的响应体：服务运行状态（serve 启动时注入）。"""

    version: str = Field(description="服务版本")
    host: str = Field(description="监听地址")
    port: int = Field(description="监听端口")
    started_at: str = Field(description="启动时间（UTC ISO 8601）")
    log_file: str = Field(description="运行日志文件路径")


class ServiceLogs(BaseModel):
    """GET /api/service/logs 的响应体：运行日志尾部。"""

    path: str = Field(description="日志文件路径")
    exists: bool = Field(
        description="日志文件是否已存在（服务启动后首次写日志前为 false）"
    )
    content: str = Field(description="日志尾部内容（最近若干行，换行拼接）")


class EndpointTestRequest(BaseModel):
    """POST /api/endpoints/test 的请求体：用表单当前值探测连通性（不必先保存）。"""

    base_url: str = Field(description="端点根地址")
    model: str = Field(description="模型名")
    api_format: str = Field(
        default=SUPPORTED_API_FORMAT,
        description="API 调用格式（一期仅 OpenAI Chat Completions）",
    )
    api_key: str | None = Field(
        default=None, description="密钥；缺省回落该配置已存密钥"
    )
    name: str | None = Field(
        default=None, description="配置名（回落已存密钥时用它定位）"
    )


class EndpointTestResult(BaseModel):
    """POST /api/endpoints/test 的响应体：探测结果（HTTP 恒 200，成败看 ok）。"""

    ok: bool = Field(description="是否连通")
    message: str = Field(description="结果说明（失败时为分类后的可操作提示）")
    latency_ms: float = Field(description="请求耗时（毫秒；未发出请求时为 0）")


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


class PromptRenameRequest(BaseModel):
    """POST /api/prompts/{name}/rename 的请求体。"""

    new_name: str


class SkillInfo(BaseModel):
    """skill 条目。"""

    name: str
    description: str
    enabled: bool
    body_chars: int = Field(
        description="注入正文字符数（SKILL.md + references/ 全部文件，即打标请求的注入量）"
    )


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


class EndpointRequestParams(BaseModel):
    """一套端点配置的请求参数（生成 + 传输），随配置存于其 config.json。

    所有键都可缺省：null = 不设该参数（请求时用端点自身默认或工具内置默认）。
    """

    temperature: float | None = Field(
        default=None, ge=0, description="采样温度；null = 不传（用端点默认）"
    )
    top_p: float | None = Field(
        default=None, ge=0, description="核采样阈值；null = 不传"
    )
    max_tokens: int | None = Field(
        default=None, ge=1, description="输出 token 上限；null = 不传"
    )
    extra_body: dict[str, object] | None = Field(
        default=None,
        description="端点专有参数透传（openai SDK 的 extra_body，原样转发不解释）；"
        "null = 不传。厂商文档里的专有参数（如开关思考模式）放这里",
    )
    timeout_seconds: float | None = Field(
        default=None, gt=0, description="单次请求超时秒数；null = 用内置默认（120）"
    )
    max_retries: int | None = Field(
        default=None, ge=0, description="失败自动重试次数；null = 用内置默认（2）"
    )


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
    request_params: EndpointRequestParams = Field(
        description="已设置的请求参数（生成 + 传输）；未设置的键为 null"
    )


class EndpointCreateRequest(BaseModel):
    """POST /api/endpoints 的请求体——新增一套配置；api_key 缺省暂不配置（可用环境变量兜底）。"""

    name: str = Field(description="配置名（即目录名，1–64 字符、不含路径保留字符）")
    base_url: str
    model: str
    api_key: str | None = None
    api_format: str = SUPPORTED_API_FORMAT
    request_params: EndpointRequestParams | None = Field(
        default=None,
        description="请求参数（生成 + 传输）；缺省 = 全不设（用默认值）",
    )


class EndpointUpdateRequest(BaseModel):
    """PUT /api/endpoints/{name} 的请求体——api_key 缺省沿用已存密钥（不强迫重输）。"""

    base_url: str
    model: str
    api_key: str | None = None
    api_format: str = SUPPORTED_API_FORMAT
    request_params: EndpointRequestParams | None = Field(
        default=None,
        description="请求参数（生成 + 传输）；缺省 = 沿用已有参数不变；"
        "提供 = 整体替换（未提供的参数键视为清除）",
    )
