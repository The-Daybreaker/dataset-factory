"""api 入口层的请求 / 响应模型（pydantic）——HTTP 边界的运行时校验。

字段类型错 → FastAPI 自动 422（一次报全部校验错误，PRD 验收 13）；业务规则（提示词
不存在、图片非法等）由核心库的域异常给出、错误映射表翻译成 HTTP 状态码。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
        default=2.0,
        ge=0.1,
        le=10.0,
        description="视频抽帧 fps（整数值，1–10；端点对浮点 fps 判非法，整性在模型校验器兜底）",
    )
    video_max_frames: int = Field(
        default=16, ge=1, le=256, description="视频抽帧帧数上限"
    )

    @model_validator(mode="after")
    def _video_fps_is_integral(self) -> LabelRequest:
        """视频轮的 fps 必须是整数值：端点（SiliconFlow）对浮点 fps 判 20015 非法。

        契约类型保持 number（避免破坏性变更挡板），整性收窄在边界校验器兜底；
        非整数值 → 422，错误消息给到可操作的原因。
        """
        if self.video_base64 is not None and self.video_fps != int(self.video_fps):
            raise ValueError(
                "video_fps 必须为整数值（如 1、2、4）：打标端点对浮点 fps 判参数非法。"
            )
        return self


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


class Problem(BaseModel):
    """RFC 9457 problem+json 错误体——二期新端点的统一错误形状。

    与一期 `ErrorDetail`（`{"detail"}`）并存：一期端点不返工，前端读 detail 兼容两者。
    扩展字段（occupier / 冲突清单等）由各错误在响应里按需附加，不进本模型。
    """

    type: str = Field(description="机器可读的错误类别 slug（如 task-not-found）")
    title: str = Field(description="人读的短语概括")
    status: int = Field(description="HTTP 状态码（与响应状态一致）")
    detail: str = Field(description="中文可操作消息——下一步该做什么")


class WorkdirInfo(BaseModel):
    """工作目录注册表条目——GET /api/workdirs 列表与详情的响应体。"""

    id: str = Field(description="wid 短 ID（注册表主键，搬迁后不变）")
    path: str = Field(description="工作目录的规范绝对路径")
    title: str = Field(description="显示名（默认 = 目录名，可改、允许重名）")
    last_used_at: float = Field(description="最后使用时刻（Unix 秒，UTC）")


class WorkdirCreateRequest(BaseModel):
    """POST /api/workdirs 的请求体：登记工作目录（可选带初始导入）。

    带来源目录 = 复制导入（素材被复制进工作目录，初始导入）；不带 = 就地采用
    （直接使用目录内素材，不复制）。两种都会扫描 + 登记导入记录，均为长任务。
    """

    path: str = Field(description="工作目录绝对路径（服务端本地）")
    title: str = Field(default="", description="显示名；缺省 = 目录名")
    source: str | None = Field(
        default=None,
        description="原始素材目录；提供 = 复制导入，缺省 = 就地采用。"
        "来源须与工作目录不同且互不嵌套",
    )


class WorkdirCreateAccepted(BaseModel):
    """POST /api/workdirs 的 202 受理响应：任务句柄 + 已登记的工作目录条目。"""

    task_id: str = Field(description="导入任务句柄（GET /api/tasks/{id} 轮询）")
    workdir: WorkdirInfo = Field(description="已登记（或复用）的工作目录条目")


class WorkdirImportRequest(BaseModel):
    """POST /api/workdirs/{wid}/imports 的请求体：补充导入。"""

    source: str = Field(
        description="原始素材目录（服务端本地，须与工作目录不同且互不嵌套）",
    )
    force_names: list[str] = Field(
        default_factory=list,
        description="异名同容时仍按新名强制导入的源文件名清单；缺省 = 全部默认跳过",
    )


class ImportAccepted(BaseModel):
    """POST /api/workdirs/{wid}/imports 的 202 受理响应：任务句柄。"""

    task_id: str = Field(description="导入任务句柄（GET /api/tasks/{id} 轮询）")


class ImportFileRecord(BaseModel):
    """导入记录里的单个文件：文件名 + 导入时的内容哈希。"""

    name: str = Field(description="文件名（工作目录内的平铺文件名）")
    sha256: str = Field(description="导入时内容哈希（SHA-256 十六进制）")


class ImportRecord(BaseModel):
    """一条导入记录（``.dsf/imports.jsonl`` 一行）——素材出身的载体。

    素材的出身 = 包含它的最近一次导入记录；同一素材多次导入取最近一次。
    就地采用的来源路径 = 工作目录自身。
    """

    imported_at: str = Field(description="导入时刻（UTC ISO 8601）")
    source: str = Field(description="来源目录路径（就地采用 = 工作目录自身）")
    files: list[ImportFileRecord] = Field(description="本次登记的文件清单")


# --------------------------------------------------------------------------
# 策略库（用户级组合清单）
# --------------------------------------------------------------------------


class StrategyView(BaseModel):
    """库策略条目——列表 / 详情 / 创建 / 更新的响应体。

    available = 引用健康度（现查）：任一引用（端点配置 / 提示词 / Skill）已不存在
    则为 False，missing_refs 给出缺失清单（界面置灰、禁止应用、走重新指定）。
    """

    id: str = Field(description="库策略 ID（内部稳定标识，改名不变）")
    name: str = Field(description="显示名（可改、允许重名）")
    description: str = Field(description="说明文字")
    endpoint: str = Field(description="端点配置名引用")
    prompt: str = Field(description="基础提示词名引用")
    skills: list[str] = Field(description="启用 Skill 名引用清单（有序）")
    available: bool = Field(description="引用健康度：全部引用现存在才可用")
    missing_refs: list[str] = Field(description="缺失引用的可读描述（健康时为空）")
    created_at: str = Field(description="创建时刻（UTC ISO 8601）")
    updated_at: str = Field(description="最近更新时刻（UTC ISO 8601）")


class StrategySaveRequest(BaseModel):
    """POST /api/strategies 与 PUT /api/strategies/{id} 的请求体（组合整体替换）。"""

    name: str = Field(description="显示名（非空）")
    description: str = Field(default="", description="说明文字")
    endpoint: str = Field(description="端点配置名（必须已存在）")
    prompt: str = Field(description="基础提示词名（必须已存在）")
    skills: list[str] = Field(
        default_factory=list, description="启用 Skill 名清单（必须已存在）"
    )


class StrategyRebindRequest(BaseModel):
    """POST /api/strategies/{id}/rebind 的请求体：缺失引用的「重新指定」。

    只更新提供的引用位，其余保持不变——对置灰策略来说，健康的引用没有理由
    被 UI 一起重交一遍。至少提供一个字段。
    """

    endpoint: str | None = Field(
        default=None, description="新的端点配置名；缺省 = 不变"
    )
    prompt: str | None = Field(
        default=None, description="新的基础提示词名；缺省 = 不变"
    )
    skills: list[str] | None = Field(
        default=None, description="新的 Skill 清单（整体替换）；缺省 = 不变"
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> StrategyRebindRequest:
        """至少指定一个引用位，否则这次调用没有语义。"""
        if self.endpoint is None and self.prompt is None and self.skills is None:
            raise ValueError(
                "至少提供一个要重新指定的引用位（endpoint / prompt / skills 之一）。"
            )
        return self


# --------------------------------------------------------------------------
# 批次（= 策略 × 工作目录）
# --------------------------------------------------------------------------


class BatchView(BaseModel):
    """批次摘要——列表 / 详情 / 配置补丁 / 停用召回的响应体。

    组合全文在快照文件（.dsf/strategies/sN.json），本视图只带元数据；
    product_count 供删除确认弹窗展示「将删多少个产物」。
    """

    id: str = Field(description="批次标识（sN 形式，如 s1）")
    seq: int = Field(description="序号（只增不复用）")
    name: str = Field(description="策略显示名（纯显示别名，可改、允许重名）")
    description: str = Field(description="说明文字")
    active: bool = Field(
        description="是否启用（False = 已停用，不出现在下拉 / 打包选项）"
    )
    created_at: str = Field(description="创建时刻（UTC ISO 8601）")
    product_count: int = Field(description="该批次现有产物 txt 数")


class BatchCreateRequest(BaseModel):
    """POST /api/workdirs/{wid}/batches 的请求体：新建批次。

    type = library：从库策略 copy-on-apply（id 必填；name / description 缺省
    沿用库策略）；type = scratch：从零配置（name / endpoint / prompt 必填）。
    """

    type: str = Field(
        description="新建方式：library（应用库策略）或 scratch（从零配置）"
    )
    id: str | None = Field(
        default=None, description="库策略 ID（type = library 时必填）"
    )
    name: str | None = Field(
        default=None, description="显示名；library 缺省沿用库策略名，scratch 必填"
    )
    description: str | None = Field(
        default=None, description="说明文字；library 缺省沿用库策略"
    )
    endpoint: str | None = Field(default=None, description="端点配置名（scratch 必填）")
    prompt: str | None = Field(default=None, description="基础提示词名（scratch 必填）")
    skills: list[str] | None = Field(
        default=None, description="启用 Skill 名清单（scratch 可缺省 = 空）"
    )

    @model_validator(mode="after")
    def _by_type(self) -> BatchCreateRequest:
        """按 type 校验必填组合（缺了当场 422，不到业务层才发现）。"""
        if self.type == "library":
            if not self.id:
                raise ValueError("type = library 时必须提供库策略 id。")
            return self
        if self.type == "scratch":
            missing = [
                field_name
                for field_name in ("name", "endpoint", "prompt")
                if getattr(self, field_name) is None
            ]
            if missing:
                raise ValueError(
                    "type = scratch 时必须提供 " + "、".join(missing) + "。"
                )
            return self
        raise ValueError("type 必须是 library 或 scratch。")


class BatchUpdateRequest(BaseModel):
    """PATCH /api/workdirs/{wid}/batches/{sN} 的请求体：改名 / 描述。

    组合不可改（工作目录下的策略是库策略的应用副本，想换组合 = 新建批次）；
    未声明字段一律拒绝，让「发错字段」当场 422 而不是被静默忽略。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="显示名；缺省 = 不变")
    description: str | None = Field(default=None, description="说明文字；缺省 = 不变")


class ExclusionsRequest(BaseModel):
    """排除名单增删的请求体：条目数组（素材主干）。"""

    items: list[str] = Field(description="条目清单（素材主干，如 cat_001）")


class ExclusionsView(BaseModel):
    """排除名单现状（增删动作都返回全量名单，前端以响应为准）。"""

    id: str = Field(description="批次标识（sN 形式）")
    seq: int = Field(description="序号")
    items: list[str] = Field(description="当前排除名单（追加序）")


# --------------------------------------------------------------------------
# 跑批（runs 执行器的 HTTP 面）
# --------------------------------------------------------------------------


class RunStartRequest(BaseModel):
    """启动跑批的请求体。"""

    mode: Literal["full", "retry"] = Field(
        description="full = 全量打未完成的条目；retry = 只打重试列表快照"
    )


class RunAccepted(BaseModel):
    """跑批受理响应（202）：后台线程已受理，进度走 current / SSE。"""

    run_id: str = Field(description="运行 id（.dsf/runs/ 下的目录名）")


class RunStatusView(BaseModel):
    """当前运行的进度快照（GET current 的响应体）。"""

    run_id: str = Field(description="运行 id")
    batch: int = Field(description="批次序号（sN 的 N——防止跨批次误读进度）")
    mode: str = Field(description="full | retry")
    status: str = Field(
        description="running | completed | interrupted | failed（failed = 启动失败）"
    )
    counters: dict[str, int] = Field(
        description="计数（planned / attempted / succeeded / failed / skipped）"
    )
    current_item: str | None = Field(description="正在（或最近一次）处理的素材主干")
    error: str | None = Field(description="启动失败的原因；正常运行为 null")


class RetryListRequest(BaseModel):
    """重试列表加入的请求体：条目数组（素材主干）。"""

    items: list[str] = Field(description="条目清单（素材主干，如 cat_001）")


class RetryListView(BaseModel):
    """重试列表现状（加入 / 移出 / 清空都返回全量名单，前端以响应为准）。"""

    id: str = Field(description="批次标识（sN 形式）")
    seq: int = Field(description="序号")
    items: list[str] = Field(description="当前重试名单（名单顺序即重试顺序）")


# --------------------------------------------------------------------------
# 条目视图（打标页左列六分组）
# --------------------------------------------------------------------------

#: 行的媒体形态（界面据此选图标）。
ItemMedia = Literal["image", "video", "file"]

#: 条目的状态位。前四个互斥（design「缺失立为第四个条目状态位」），
#: unimported 不是条目状态——它是「未导入」分组里那些文件的行状态。
ItemStatus = Literal["queued", "done", "failed", "missing", "unimported"]


class ItemRowView(BaseModel):
    """左列的一行——六分组共用一个行形状，用不上的字段为 null。

    共用一个形状是为了界面不必为每个分组各写一套解析；字段按行类别分工：
    未完成行带 attempt / reason_code / message，缺失行带 source / recoverable，
    未导入行带 reason / size / limit。
    """

    item: str = Field(description="条目身份 = 素材主干（不含扩展名）")
    name: str = Field(description="展示用文件名（含扩展名）")
    media: ItemMedia = Field(description="媒体形态（界面选图标）")
    status: ItemStatus = Field(description="状态位")
    can_retry: bool = Field(
        description="能否加入重试列表——False 时界面置灰（排队中无可重试、"
        "缺失要先补素材、不可重试失败要先解决格式问题）",
    )
    in_retry: bool = Field(description="是否已在重试列表（叠加标记「已排重试」）")
    attempt: int | None = Field(
        default=None, description="未完成行：最近一次的尝试序号（1–4）"
    )
    reason_code: str | None = Field(
        default=None, description="未完成行：失败原因码（F5 两类清单）"
    )
    message: str | None = Field(
        default=None, description="未完成行：失败原因（人读，来自运行流水）"
    )
    source: str | None = Field(
        default=None,
        description="缺失行：来源文件的完整路径（悬停提示与「从别处导入」用）",
    )
    recoverable: bool | None = Field(
        default=None,
        description="缺失行：来源那儿是否还有这份素材（决定「重新导入」可不可点）",
    )
    reason: str | None = Field(
        default=None,
        description="未导入行：原因（标准措辞——扩展名不支持 / 超出大小上限 / 未登记）",
    )
    size: int | None = Field(default=None, description="未导入行：文件字节数")
    limit: int | None = Field(
        default=None, description="未导入行：该档大小上限（只有超限那一类有值）"
    )


class ItemListView(BaseModel):
    """条目视图响应（GET items）：六个分组恒在，空组给空列表。

    分组键固定六个：queued / done / failed / missing（四个互斥状态位）、
    retry（叠加标记的聚合视图，条目同时留在自己的状态分组里）、
    unimported（素材级待办清单，不属于条目）。各组计数 = 该组行数。
    """

    batch: int = Field(description="批次序号（防把 s2 的视图渲染进 s1 的列表）")
    query: str = Field(description="生效的搜索词（空串 = 没过滤）")
    groups: dict[str, list[ItemRowView]] = Field(description="分组键 → 该组的行")
