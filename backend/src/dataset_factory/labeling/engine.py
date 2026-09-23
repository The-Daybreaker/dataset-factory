"""打标编排引擎——全项目唯一知道「一轮打标怎么拼」的地方。

一轮打标 = 基础提示词（system 消息）+ 启用的 skill 全文（<skill> 标记包裹进当轮 user 消息）
+ 图片与指令（当轮 user 消息）+ 历史回放（按原角色重建、历史附件以真字节跟随重发）。每轮经
sessions 落盘（设置变更 → 用户消息 → 请求信封 → 模型回复；先校验本轮全部输入再落任何事件，
失败轮零痕迹，信封先落盘再调模型、失败也有「当时喂了什么」可查）；llm 消息模型与会话事件
JSON 的来回转换收敛在本模块（sessions 只把
设置与信封当任意 JSON 忠实存取，守分层）。历史不缓存、每轮回放 events.jsonl 重建——新进程
（如 CLI 续接）与崩溃重启后天然续上同一会话。

二期追加一条**纯素材输入的调用路径**（``label_material``）：批量跑批无 UI、无人值守、
不建会话——素材直接给文件路径，提示词与 skill 全文由调用方传入（来自策略快照，不是库名
引用，快照隔离），全程零写盘；顺手对实际读取的素材字节算一次 SHA-256（「处理时刻的输入
哈希」，运行流水判定产物时效的锚点）。两条路径共用同一套拼装（``_assemble``），
「一轮怎么拼」仍然只有一处。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import cast

from .._obs import ms_since
from ..llm import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    VIDEO_EXTENSIONS,
    VIDEO_MIME_BY_SUFFIX,
    Completer,
    ContentPart,
    ImagePart,
    LLMError,
    Message,
    Role,
    StreamDelta,
    TextPart,
    VideoPart,
)
from ..prompts import PROMPT_ID_RE, Prompt, prompt_id_by_display_name, read_prompt
from ..sessions import (
    JsonValue,
    MessageEvent,
    SessionEvent,
    SettingsEvent,
    append_envelope,
    append_message,
    append_settings,
    attachment_path,
    create_session,
    read_events,
    read_strategy_id,
    save_attachment,
    save_attachment_bytes,
)
from ..skills import (
    SKILL_ID_RE,
    get_skill,
    list_skills,
    read_skill,
    skill_id_by_display_name,
)
from .errors import (
    AttachmentReadError,
    EmptyTurnError,
    MaterialOversizeError,
    MaterialReadError,
    PromptNotSelectedError,
    SettingsFormatError,
)

# 会话设置在 settings 事件里的 JSON 键（该结构由本模块定义，sessions 不解析）。
_KEY_PROMPT = "prompt"  # pragma: allowlist secret
_KEY_SKILLS = "skills"  # pragma: allowlist secret

# skill 全文的边界标记：让模型认出这是注入的 skill 说明，也让记录能认出来源。
_SKILL_OPEN = "<skill>"
_SKILL_CLOSE = "</skill>"

logger = logging.getLogger(__name__)

# 进行中的轮次（会话 id 集合）：从「本轮会话确定」起登记到轮次终结（完成 / 失败 / 客户端
# 断开）。供会话删除路径检查「这个会话还有没有轮次在写」——Windows 上删正被追加的文件
# 会失败，Linux 上虽不报错但会让删除与追加交错；统一拒绝是两侧一致的语义。
_ACTIVE_TURNS: set[str] = set()


def active_session_ids() -> frozenset[str]:
    """当前有轮次在写（非流式执行中 / 流式未收尾）的会话 id 集合（删除路径的检查面）。"""
    return frozenset(_ACTIVE_TURNS)


def _require_caption(caption: str) -> None:
    """空稿护栏：正文空白即抛 LLMError（流式调用方在 try 内触发它，让半截思考进 partial 落盘）。

    Raises:
        LLMError: caption 空白（与跑批路径 runner 同口径的失败分类）。
    """
    if not caption.strip():
        raise LLMError("模型返回了空白描述；请重试或调整输入。")


@dataclass(frozen=True)
class SessionSettings:
    """一个会话的当前设置（从最后一条 settings 事件折叠而来）。

    Attributes:
        prompt_id: 当前基础提示词 ID；会话从未设置过时为 None（首轮打标必须指定）。
        skill_ids: 当前勾选启用的 skill ID 序列（保持勾选顺序注入）。
    """

    prompt_id: str | None
    skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class HistoryMessage:
    """回放出的历史消息（供入口层展示对话历史）。

    Attributes:
        role: 消息角色（``user`` / ``assistant``）。
        text: 消息文本。
        attachment: 附件在会话 attachments/ 下的文件名；无图为 None。
        reasoning: 助手消息的思考过程全文（落盘的会话才有；只供界面回看）。
        partial: True = 断流 / 报错时落盘的半截回复（B5；界面标注「未完成」）。
        elapsed_ms: 本轮整轮耗时毫秒（V7；恢复历史后仍可显示）。
        reasoning_ms: 本轮思考耗时毫秒（V7；思考开关的仪表盘）。
    """

    role: str
    text: str
    attachment: str | None
    reasoning: str | None = None
    partial: bool = False
    elapsed_ms: int | None = None
    reasoning_ms: int | None = None


@dataclass(frozen=True)
class SessionSnapshot:
    """恢复一个会话所需的全部状态（重启后界面/续接的起点）。

    Attributes:
        session_id: 会话 id。
        settings: 会话当前设置。
        messages: 对话历史（user / assistant 消息，按时间序）。
        strategy_id: 会话归属（策略 id 或 ``__new__`` 草稿桶）；无归属（存量会话）
            为 None。
    """

    session_id: str
    settings: SessionSettings
    messages: tuple[HistoryMessage, ...]
    strategy_id: str | None = None


@dataclass(frozen=True)
class LabelResult:
    """一轮打标的结果。

    Attributes:
        session_id: 本轮所属会话的 id（首次调用新建的会话也在这里返回，供续接）。
        caption: 模型产出的打标文本。
    """

    session_id: str
    caption: str


@dataclass(frozen=True)
class MaterialLabelResult:
    """一次纯素材打标的结果（无会话路径）。

    Attributes:
        caption: 模型产出的打标文本。
        asset_hash: 本次实际读取并发送的素材字节的 SHA-256——「处理时刻的输入哈希」，
            运行流水记下它才能判定「这份产物对应当前素材，还是素材在打标后被换过」
            （design「素材完整性校验（哈希）」的唯一锚点）。
    """

    caption: str
    asset_hash: str


class LabelingEngine:
    """打标编排引擎：组装一轮打标、经 sessions 落盘、调用模型拿回 caption。

    构造时注入 Completer（测试注入假实现即可离线跑）与模型名（模型名进请求信封，
    供复盘「当时用的是哪个模型」）。
    """

    def __init__(self, completer: Completer, model: str) -> None:
        """注入补全客户端与模型名。

        Args:
            completer: 实现 llm.Completer 协议的客户端（唯一与模型通信的通道）。
            model: 模型名（写入请求信封）。
        """
        self._completer = completer
        self._model = model

    def label(
        self,
        session_id: str | None = None,
        *,
        prompt_id: str | None = None,
        skill_ids: Sequence[str] | None = None,
        instruction: str = "",
        image: Path | None = None,
        image_bytes: bytes | None = None,
        image_name: str = "image.png",
        video_bytes: bytes | None = None,
        video_name: str = "video.mp4",
        video_mime: str = "video/mp4",
        video_fps: int = 2,
        video_max_frames: int = 16,
        strategy_id: str | None = None,
    ) -> LabelResult:
        """跑一轮打标：组装请求 → 先落信封 → 调模型 → 落回复，返回 caption 与会话 id。

        session_id 为 None 时新建会话（新 id 随结果返回，外部调用方保存后即可续接）。
        迭代改写 = 带同一 session_id 再调：历史自动回放携带。设置沿用与切换：prompt_id /
        skill_ids 不传（None）沿用会话当前设置，传入新值（含空清单）即切换——设置真正
        变化时才追加 settings 事件。库级停用的 skill 注入时跳过（T4 语义：停用 = 不供打标
        注入），会话设置里保留原勾选记录。

        图片两种给法（二选一，同时给是编程错、直接 ValueError）：``image`` 传本地文件路径
        （CLI 用，存副本再读回）；``image_bytes`` 直接传字节（HTTP 入口用，网络来的字节没有
        源文件），``image_name`` 是它的原始文件名（保留进 attachments/，冲突加序号）。

        Args:
            session_id: 续接的会话 id；None 新建会话。
            prompt_id: 本轮使用的基础提示词 ID；None 沿用当前设置。
            skill_ids: 本轮启用的 skill ID 序列；None 沿用当前设置，空序列表示清空。
            instruction: 用户本轮的打标指令（可为空——纯图打标时任务说明在基础提示词里）。
            image: 本轮图片文件路径；None 表示不以此方式附图。
            image_bytes: 本轮图片字节；None 表示不以此方式附图。
            image_name: image_bytes 方式的原始文件名（仅名字用途，不参与内容判定）。
            strategy_id: 新建会话时的归属章（策略 id 或 ``__new__`` 草稿桶）；续接时
                忽略（归属跟随既有会话，不随轮次漂移）。

        Returns:
            LabelResult：会话 id + 模型产出的 caption。

        Raises:
            EmptyTurnError: 指令为空且未附图片（没有任何可打标的内容）。
            PromptNotSelectedError: 会话没有基础提示词且本轮未传入。
            AttachmentReadError: 附件副本读取失败。
            SettingsFormatError: 会话的设置事件结构非法。
            SessionNotFoundError: session_id 指向不存在的会话。
            PromptNotFoundError: 基础提示词在提示词库中不存在。
            SkillNotFoundError: 勾选的 skill 名不在 skill 库中（拼写错误或已被删除）。
            LLMError: 模型调用失败（此时信封已落盘，「当时喂了什么」有据可查）。
            ValueError: image 与 image_bytes 同时提供；或图片与视频同时提供（一期单素材/次）。
        """
        start = perf_counter()
        (
            session_id,
            prompt,
            skill_texts,
            history,
            sent_image_bytes,
            sent_video_bytes,
            attachment,
        ) = _begin_turn(
            session_id=session_id,
            prompt_id=prompt_id,
            skill_ids=skill_ids,
            instruction=instruction,
            image=image,
            image_bytes=image_bytes,
            image_name=image_name,
            video_bytes=video_bytes,
            video_name=video_name,
            strategy_id=strategy_id,
        )
        _ACTIVE_TURNS.add(session_id)
        try:
            return self._run_turn(
                session_id=session_id,
                start=start,
                prompt=prompt,
                skill_texts=skill_texts,
                history=history,
                sent_image_bytes=sent_image_bytes,
                sent_video_bytes=sent_video_bytes,
                attachment=attachment,
                instruction=instruction,
                video_mime=video_mime,
                video_fps=video_fps,
                video_max_frames=video_max_frames,
            )
        finally:
            _ACTIVE_TURNS.discard(session_id)

    def _run_turn(
        self,
        *,
        session_id: str,
        start: float,
        prompt: Prompt,
        skill_texts: list[str],
        history: tuple[Message, ...],
        sent_image_bytes: bytes | None,
        sent_video_bytes: bytes | None,
        attachment: str | None,
        instruction: str,
        video_mime: str,
        video_fps: int,
        video_max_frames: int,
    ) -> LabelResult:
        """非流式一轮的模型调用与落盘段（从 label() 拆出，让轮次登记包住全程）。"""
        messages, envelope_messages = _assemble(
            prompt_body=prompt.body,
            skill_texts=skill_texts,
            history=history,
            instruction=instruction,
            image_bytes=sent_image_bytes,
            video_bytes=sent_video_bytes,
            video_mime=video_mime,
            video_fps=video_fps,
            video_max_frames=video_max_frames,
            attachment=attachment,
        )
        append_envelope(
            session_id, {"model": self._model, "messages": envelope_messages}
        )

        # 分层计时的第二段边界（第一段是 HTTP 接入、第三段在 llm 客户端内部）：这一段包含
        # 读事件流、折叠设置、读提示词与 skill 全文、拼消息、落信封——skill 包很大或历史很长
        # 时它也会明显变慢，所以必须与「模型调用」分开计时，否则排查时分不清卡在哪一层。
        assemble_ms = ms_since(start)

        llm_start = perf_counter()
        try:
            caption = self._completer.complete(messages)
        finally:
            # 模型层耗时无论成败都记一行；失败详情由入口层边界记录（库层不 catch 异常）。
            llm_ms = ms_since(llm_start)
            logger.info("一轮打标模型调用结束：%.0fms（会话 %s）", llm_ms, session_id)

        append_message(session_id, "assistant", caption)
        logger.info(
            "一轮打标完成：组装 %.0fms、模型 %.0fms、合计 %.0fms（会话 %s）",
            assemble_ms,
            llm_ms,
            ms_since(start),
            session_id,
        )
        return LabelResult(session_id=session_id, caption=caption)

    def label_stream(
        self,
        session_id: str | None = None,
        *,
        prompt_id: str | None = None,
        skill_ids: Sequence[str] | None = None,
        instruction: str = "",
        image: Path | None = None,
        image_bytes: bytes | None = None,
        image_name: str = "image.png",
        video_bytes: bytes | None = None,
        video_name: str = "video.mp4",
        video_mime: str = "video/mp4",
        video_fps: int = 2,
        video_max_frames: int = 16,
        strategy_id: str | None = None,
    ) -> Iterator[StreamStarted | StreamDelta | StreamFinished]:
        """流式跑一轮打标：先落信封 → 逐段产出增量 → 终稿落盘，事件序列返回给调用方。

        与 label() 共用同一套准备（校验 / 事件落盘 / 组装），区别只在模型调用方式：
        stream 逐段产出、结束把 caption 终稿与思考过程全文一并落盘（非流式路径无
        思考内容可存）。事件顺序 = StreamStarted（信封已落盘）→ StreamDelta…（思考 /
        正文增量）→ StreamFinished（终稿）。模型调用失败在流中途抛 LLMError——调用方此时可能已把
        部分增量发给界面，由入口层决定如何呈现「生成中断」。

        Args / Raises: 同 label()（同一套准备与素材互斥规则）。

        Yields:
            StreamStarted | StreamDelta | StreamFinished：打标流事件。
        """
        start = perf_counter()
        (
            session_id,
            prompt,
            skill_texts,
            history,
            sent_image_bytes,
            sent_video_bytes,
            attachment,
        ) = _begin_turn(
            session_id=session_id,
            prompt_id=prompt_id,
            skill_ids=skill_ids,
            instruction=instruction,
            image=image,
            image_bytes=image_bytes,
            image_name=image_name,
            video_bytes=video_bytes,
            video_name=video_name,
            strategy_id=strategy_id,
        )
        _ACTIVE_TURNS.add(session_id)
        try:
            yield from self._stream_turn(
                session_id=session_id,
                start=start,
                prompt=prompt,
                skill_texts=skill_texts,
                history=history,
                sent_image_bytes=sent_image_bytes,
                sent_video_bytes=sent_video_bytes,
                attachment=attachment,
                instruction=instruction,
                video_mime=video_mime,
                video_fps=video_fps,
                video_max_frames=video_max_frames,
            )
        finally:
            # 生成器被消费完或客户端中途断开（GeneratorExit）都要摘牌，否则该会话
            # 会被永久当作「进行中」而拒绝删除。
            _ACTIVE_TURNS.discard(session_id)

    def _stream_turn(
        self,
        *,
        session_id: str,
        start: float,
        prompt: Prompt,
        skill_texts: list[str],
        history: tuple[Message, ...],
        sent_image_bytes: bytes | None,
        sent_video_bytes: bytes | None,
        attachment: str | None,
        instruction: str,
        video_mime: str,
        video_fps: int,
        video_max_frames: int,
    ) -> Iterator[StreamStarted | StreamDelta | StreamFinished]:
        """流式一轮的模型调用与落盘段（从 label_stream() 拆出，轮次登记在外层）。"""
        messages, envelope_messages = _assemble(
            prompt_body=prompt.body,
            skill_texts=skill_texts,
            history=history,
            instruction=instruction,
            image_bytes=sent_image_bytes,
            video_bytes=sent_video_bytes,
            video_mime=video_mime,
            video_fps=video_fps,
            video_max_frames=video_max_frames,
            attachment=attachment,
        )
        append_envelope(
            session_id, {"model": self._model, "messages": envelope_messages}
        )
        yield StreamStarted(session_id=session_id)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        trimmer = _LeadingBlankTrimmer()
        llm_start = perf_counter()
        try:
            for delta in self._completer.stream(messages):
                if delta.kind == "content":
                    # 前导空白在源头丢弃（A3）：正文首字之前的空行不产帧、不进气泡、
                    # 不进落盘——工作台气泡上方那 42px 空洞与产物前导空行同源。
                    text = trimmer.trim(delta.text)
                    if not text:
                        continue
                    content_parts.append(text)
                    yield StreamDelta(kind="content", text=text)
                else:
                    reasoning_parts.append(delta.text)
                    yield delta
            caption = "".join(content_parts)
            # 空稿护栏，与跑批路径同口径（B5；runner 对空白 caption 同样判失败）。
            # 必须在 try 内触发（经 _require_caption 抛出）：让下面 except 的 partial
            # 落盘覆盖这条路径——思考型模型「token 全花在思考、正文零字」的半截同样是
            # 已收到的内容，此前只活在浏览器内存里（前端 keepPartial 即时结算），
            # 刷新 / 重启即丢（2026-09-23 用户实测曝光）。
            _require_caption(caption)
        except LLMError:
            # B5（2026-09-21 审计）：断流 / 超时的半截回复也落盘并标 partial——
            # 会话历史不因失败整条消失（PRD-0001 验收 10 / 11：失败不丢历史、
            # append-only）；重发那句话也不会变成两条重复 user 气泡。
            # 上方空稿护栏的 raise 也走这里（2026-09-23 起）：纯思考零正文的半截
            # 同样落 partial，不再只活在浏览器内存里。
            partial_text = "".join(content_parts)
            partial_reasoning = "".join(reasoning_parts)
            if partial_text or partial_reasoning:
                append_message(
                    session_id,
                    "assistant",
                    partial_text,
                    reasoning=partial_reasoning or None,
                    partial=True,
                    elapsed_ms=int(ms_since(start)),
                    reasoning_ms=int(ms_since(llm_start)),
                )
            raise
        # 思考过程随终稿一起落盘（2026-09-20 用户定夺，2026-09-21 用户再次确认）：
        # 恢复会话可回看；回放下一轮请求装配仍只取正文，思考不进请求。
        reasoning = "".join(reasoning_parts)
        append_message(
            session_id,
            "assistant",
            caption,
            reasoning=reasoning or None,
            elapsed_ms=int(ms_since(start)),
            reasoning_ms=int(ms_since(llm_start)),
        )
        logger.info(
            "一轮流式打标完成：合计 %.0fms（会话 %s）", ms_since(start), session_id
        )
        yield StreamFinished(result=LabelResult(session_id=session_id, caption=caption))

    def label_material(
        self,
        material: Path,
        *,
        prompt_body: str,
        skill_texts: Sequence[str] = (),
        instruction: str = "",
        video_fps: int = 2,
        video_max_frames: int = 16,
    ) -> MaterialLabelResult:
        """纯素材打标：读一个素材文件、拼一轮请求、调模型，返回 caption 与素材哈希。

        批量跑批（runs 执行器）的调用路径——与 label() 的三点不同：**不建会话**（零写盘，
        没有事件流、信封与附件副本，无 UI 无人值守不需要复盘现场）；**提示词与 skill 按
        全文传入**（调用方从策略快照读全文，不经库按名解析——快照隔离：库端事后被编辑
        或删除都不影响本批）；**结果带素材哈希**（读文件与算哈希同一次读取完成，零额外
        IO——见 :class:`MaterialLabelResult`）。图片与视频按扩展名区分，一次一个素材。

        运行时护栏在读取前先验（防素材导入后被绕过工具换掉）：文件存在性与大小上限
        （图片 20 MiB / 视频 100 MiB，单一事实源在 llm 层）。

        Args:
            material: 素材文件路径（图片或视频，按扩展名窄清单判定；白名单与上限
                与导入护栏同源）。
            prompt_body: 基础提示词全文（进 system 消息；来自策略快照）。
            skill_texts: 要注入的 skill 全文序列（来自策略快照，按注入序）。
            instruction: 附加指令（批量打标通常为空——任务说明在基础提示词里）。
            video_fps: 视频抽帧 fps（素材是视频时生效）。
            video_max_frames: 视频抽帧帧数上限（素材是视频时生效）。

        Returns:
            MaterialLabelResult：caption + 本次实际读取的素材字节哈希。

        Raises:
            ValueError: prompt_body 为空白（一轮打标必须有基础提示词作 system 底座）；
                或素材扩展名不在白名单内（调用方应只喂窄清单素材；导入后被改名也在此拦下）。
            MaterialReadError: 素材不存在、不是文件或读取失败。
            MaterialOversizeError: 素材超出大小上限（图片 20 MiB / 视频 100 MiB）。
            LLMError: 模型调用失败（本路径不落盘，异常直接冒泡给调用方按运行流水处置）。
        """
        start = perf_counter()
        messages, asset_hash = _prepare_material_messages(
            material,
            prompt_body=prompt_body,
            skill_texts=skill_texts,
            instruction=instruction,
            video_fps=video_fps,
            video_max_frames=video_max_frames,
        )
        assemble_ms = ms_since(start)

        llm_start = perf_counter()
        try:
            caption = self._completer.complete(messages)
        finally:
            llm_ms = ms_since(llm_start)
            logger.info(
                "纯素材打标模型调用结束：%.0fms（素材 %s）", llm_ms, material.name
            )
        logger.info(
            "纯素材打标完成：组装 %.0fms、模型 %.0fms、合计 %.0fms（素材 %s）",
            assemble_ms,
            llm_ms,
            ms_since(start),
            material.name,
        )
        return MaterialLabelResult(caption=caption, asset_hash=asset_hash)

    def label_material_stream(
        self,
        material: Path,
        *,
        prompt_body: str,
        skill_texts: Sequence[str] = (),
        instruction: str = "",
        video_fps: int = 2,
        video_max_frames: int = 16,
        on_delta: Callable[[StreamDelta], None],
    ) -> MaterialLabelResult:
        """纯素材打标的**流式**变体：逐段产出思考 / 正文增量，终稿语义与 complete 同。

        跑批执行器用本路径替代 complete()（2026-09-21 审计 A2）：没有逐字输出，
        用户判断不了「在跑」还是「卡住」。增量经 ``on_delta`` 回调实时交给调用方
        （SSE 逐字转发给界面）；**思考增量只转发不落盘**——打标产物只有 caption
        （PRD F4「txt 只含 caption 本身」），思考不进产物 txt、不进运行流水，
        关掉前端再打开就不显示。正文增量同时累积，流结束拼成终稿 caption。

        Args:
            material: 素材文件路径（图片或视频）。
            prompt_body: 基础提示词全文（来自策略快照）。
            skill_texts: 要注入的 skill 全文序列（来自策略快照）。
            instruction: 附加指令（批量打标通常为空）。
            video_fps: 视频抽帧 fps（素材是视频时生效）。
            video_max_frames: 视频抽帧帧数上限（素材是视频时生效）。
            on_delta: 每个思考 / 正文增量的实时回调（在模型流式线程上同步执行）。

        Returns:
            MaterialLabelResult：caption 终稿 + 素材哈希（与非流式完全同形）。

        Raises:
            LLMError: 与 :meth:`label_material` 同一套失败——流中途失败时增量
                已转发，调用方按失败处置，半截内容只活内存、不落盘。
        """
        start = perf_counter()
        messages, asset_hash = _prepare_material_messages(
            material,
            prompt_body=prompt_body,
            skill_texts=skill_texts,
            instruction=instruction,
            video_fps=video_fps,
            video_max_frames=video_max_frames,
        )

        llm_start = perf_counter()
        content_parts: list[str] = []
        trimmer = _LeadingBlankTrimmer()
        try:
            for delta in self._completer.stream(messages):
                if delta.kind == "content":
                    # 前导空白在源头丢弃（A3）：跑批界面逐字区与产物都不吃空行。
                    text = trimmer.trim(delta.text)
                    if not text:
                        continue
                    content_parts.append(text)
                    on_delta(StreamDelta(kind="content", text=text))
                else:
                    on_delta(delta)
        except LLMError:
            logger.info(
                "纯素材流式打标失败：%.0fms（素材 %s）",
                ms_since(llm_start),
                material.name,
            )
            raise
        llm_ms = ms_since(llm_start)
        caption = "".join(content_parts)
        logger.info(
            "纯素材流式打标完成：组装 %.0fms、模型 %.0fms、合计 %.0fms（素材 %s）",
            ms_since(start),
            llm_ms,
            ms_since(start),
            material.name,
        )
        return MaterialLabelResult(caption=caption, asset_hash=asset_hash)

    def restore(self, session_id: str) -> SessionSnapshot:
        """恢复一个会话：当前设置 + 对话历史（入口层重启 / CLI 续接的起点）。

        Args:
            session_id: 会话 id。

        Returns:
            SessionSnapshot：设置 + 按时间序的 user / assistant 历史消息。

        Raises:
            SessionNotFoundError: 没有这个会话。
            SessionEventError: 事件流损坏。
            SettingsFormatError: 设置事件结构非法。
        """
        events = read_events(session_id)
        messages = tuple(
            HistoryMessage(
                role=event.role,
                text=event.text,
                attachment=event.attachment,
                reasoning=event.reasoning,
                partial=event.partial,
                elapsed_ms=event.elapsed_ms,
                reasoning_ms=event.reasoning_ms,
            )
            for event in events
            if isinstance(event, MessageEvent) and event.role in ("user", "assistant")
        )
        return SessionSnapshot(
            session_id=session_id,
            settings=_fold_settings(events),
            messages=messages,
            strategy_id=read_strategy_id(session_id),
        )


@dataclass(frozen=True)
class StreamStarted:
    """流式打标开始：会话已建立、本轮请求信封已落盘（界面可先拿到会话 id）。"""

    session_id: str


@dataclass(frozen=True)
class StreamFinished:
    """流式打标完成：caption 终稿已落盘（历史照常可恢复）。"""

    result: LabelResult


def _begin_turn(
    *,
    session_id: str | None,
    prompt_id: str | None,
    skill_ids: Sequence[str] | None,
    instruction: str,
    image: Path | None,
    image_bytes: bytes | None,
    image_name: str,
    video_bytes: bytes | None,
    video_name: str,
    strategy_id: str | None = None,
) -> tuple[
    str, Prompt, list[str], tuple[Message, ...], bytes | None, bytes | None, str | None
]:
    """label / label_stream 共用的本轮准备：校验 → 事件落盘（设置 + 用户消息）。

    先校验、后落盘：提示词与 skill 是本轮的两个用户输入，在任何写盘（含新会话建目录）
    之前全部验证完——失败轮零痕迹（不留坏设置、不留孤儿消息、也不留空壳会话）。

    Returns:
        (session_id, 基础提示词, 注入的 skill 全文, 历史消息, 图片字节, 视频字节, 附件名)。

    Raises:
        EmptyTurnError / ValueError / PromptNotSelectedError / PromptNotFoundError /
        SkillNotFoundError / AttachmentReadError / SettingsFormatError /
        SessionNotFoundError: 同 label() 的准备段。
    """
    if image is not None and image_bytes is not None:
        raise ValueError("image 与 image_bytes 只能二选一。")
    if video_bytes is not None and (image is not None or image_bytes is not None):
        raise ValueError("视频与图片只能二选一（一期单素材/次）。")
    if (
        not instruction.strip()
        and image is None
        and image_bytes is None
        and video_bytes is None
    ):
        raise EmptyTurnError(
            "本轮没有任何可打标的内容：请输入指令或附一张图片 / 一段视频。"
        )
    if session_id is None:
        events: list[SessionEvent] = []
    else:
        events = read_events(session_id)
    settings = _fold_settings(events)
    history = _replay_history(events, session_id)

    # 传入引用（ID 或唯一显示名）先规范化为稳定 ID：与折叠出的会话设置同一形状，
    # 比较去重才不会因「同物异形」误判变化而重复追加设置事件。
    wanted_prompt = _to_prompt_id(
        prompt_id if prompt_id is not None else settings.prompt_id
    )
    wanted_skills = tuple(
        _to_skill_id(item)
        for item in (tuple(skill_ids) if skill_ids is not None else settings.skill_ids)
    )
    if wanted_prompt is None:
        raise PromptNotSelectedError(
            "尚未选定基础提示词（一轮打标必须有一个作 system 底座）；请先选择提示词。"
        )
    prompt = read_prompt(wanted_prompt)
    skill_texts = _load_enabled_skill_texts(wanted_skills)
    if session_id is None:
        session_id = create_session(strategy_id=strategy_id)
    if (wanted_prompt, wanted_skills) != (
        settings.prompt_id,
        settings.skill_ids,
    ):
        append_settings(
            session_id,
            {_KEY_PROMPT: wanted_prompt, _KEY_SKILLS: list(wanted_skills)},
        )

    attachment: str | None = None
    sent_image_bytes: bytes | None = None
    sent_video_bytes: bytes | None = None
    if image is not None:
        attachment = save_attachment(session_id, image)
        sent_image_bytes = _read_attachment(session_id, attachment)
    elif image_bytes is not None:
        attachment = save_attachment_bytes(session_id, image_name, image_bytes)
        sent_image_bytes = image_bytes
    elif video_bytes is not None:
        attachment = save_attachment_bytes(session_id, video_name, video_bytes)
        sent_video_bytes = video_bytes
    append_message(session_id, "user", instruction, attachment)
    return (
        session_id,
        prompt,
        skill_texts,
        history,
        sent_image_bytes,
        sent_video_bytes,
        attachment,
    )


def _read_attachment(session_id: str, name: str) -> bytes:
    """读会话附件副本的字节（发送的就是副本内容，会话自包含优先）。

    Raises:
        AttachmentReadError: 副本读取失败（底层 OSError）。
    """
    try:
        return attachment_path(session_id, name).read_bytes()
    except OSError as exc:
        raise AttachmentReadError(
            f"无法读取会话 {session_id!r} 的附件 {name!r}：{exc.strerror or exc}"
        ) from exc


class _LeadingBlankTrimmer:
    """流式正文的前导空白修剪器（A3）：首个非空白字符之前的空白增量整体丢弃。

    部分端点的正文以空行开头——非流式路径在 ``_extract_text`` strip 掉了，流式路径
    的增量到达即转发、事后 strip 救不了已发出去的帧，所以在源头丢弃：不进气泡、
    不产帧、不进落盘。首个非空白字符出现后原样放行（正文内部与结尾的空白不动）。
    """

    def __init__(self) -> None:
        self._started = False

    def trim(self, text: str) -> str:
        """修剪一段正文增量：前导空白阶段返回空串（调用方整段丢弃），之后原样返回。"""
        if self._started:
            return text
        trimmed = text.lstrip()
        if trimmed:
            self._started = True
        return trimmed


def _prepare_material_messages(
    material: Path,
    *,
    prompt_body: str,
    skill_texts: Sequence[str],
    instruction: str,
    video_fps: int,
    video_max_frames: int,
) -> tuple[list[Message], str]:
    """纯素材两条调用路径（``label_material`` / ``label_material_stream``）共用的装配段。

    校验（提示词非空白）→ 运行时护栏读取素材 → 拼一轮消息；「一轮怎么拼」仍只有
    ``_assemble`` 一处，本函数只负责把纯素材路径的取材与拼装收敛成一段。

    Returns:
        (消息序列, 素材字节哈希)。

    Raises:
        ValueError: prompt_body 为空白；或素材扩展名不在白名单内。
        MaterialReadError: 素材不存在、不是文件或读取失败。
        MaterialOversizeError: 素材超出大小上限。
    """
    if not prompt_body.strip():
        raise ValueError(
            "prompt_body 不能为空白——一轮打标必须有一个基础提示词作 system 底座；"
            "请检查策略快照的基础提示词是否为空。"
        )
    sent_bytes = _read_material(material)
    asset_hash = hashlib.sha256(sent_bytes).hexdigest()
    suffix = material.suffix.lower()
    is_video = suffix in VIDEO_EXTENSIONS
    # 信封视图在本路径弃用（runs 的运行流水只记结果与哈希，无信封落盘）。
    messages, _ = _assemble(
        prompt_body=prompt_body,
        skill_texts=skill_texts,
        history=(),
        instruction=instruction,
        image_bytes=None if is_video else sent_bytes,
        video_bytes=sent_bytes if is_video else None,
        video_mime=VIDEO_MIME_BY_SUFFIX.get(suffix, "video/mp4"),
        video_fps=video_fps,
        video_max_frames=video_max_frames,
        attachment=material.name,
    )
    return messages, asset_hash


def _read_material(material: Path) -> bytes:
    """读取素材文件字节（纯素材路径专用）：先过运行时护栏，再整读返回。

    运行时再验（design「素材扫描窄清单与大小护栏」）：导入时已验过一次，但素材可能
    在导入后被绕过工具替换——读取前 stat 一下零成本拦住，避免整读进内存才发现。
    扩展名决定图片 / 视频与对应上限；白名单外直接拒绝（导入护栏与运行时护栏共用
    llm 层的窄清单与上限，单一事实源）。

    Raises:
        MaterialReadError: 素材不存在、不是文件或读取失败。
        MaterialOversizeError: 超出该类素材的大小上限（图片 20 MiB / 视频 100 MiB）。
        ValueError: 扩展名不在窄清单内。
    """
    suffix = material.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        limit = MAX_IMAGE_BYTES
        kind = "图片"
    elif suffix in VIDEO_EXTENSIONS:
        limit = MAX_VIDEO_BYTES
        kind = "视频"
    else:
        raise ValueError(
            f"素材「{material.name}」的扩展名 {suffix!r} 不在白名单内；只接受图片"
            " jpg/jpeg/png/webp/gif 与视频 mp4/m4v/mov/webm/avi/mkv（导入时已按此"
            "窄清单把关，素材可能已被改名）。"
        )
    try:
        size = material.stat().st_size
    except OSError as exc:
        raise MaterialReadError(
            f"无法读取{kind}素材「{material.name}」：{exc.strerror or exc}"
        ) from exc
    if size > limit:
        raise MaterialOversizeError(
            f"{kind}素材「{material.name}」超出大小上限"
            f"（{size / (1024 * 1024):.1f} MiB，上限 {limit // (1024 * 1024)} MiB）；"
            "请压缩或替换后重试。"
        )
    try:
        return material.read_bytes()
    except OSError as exc:
        raise MaterialReadError(
            f"无法读取{kind}素材「{material.name}」：{exc.strerror or exc}"
        ) from exc


def _fold_settings(events: Sequence[SessionEvent]) -> SessionSettings:
    """从事件流折叠出当前设置：取最后一条 settings 事件的值（无则全空）。

    旧版会话事件的设置存的是资产显示名（2026-09-23 前的口径）：折叠时按名解析成
    ID；解析不到（资产已被改名或删除）保留原值，后续读取按「引用不存在」fail loud，
    由用户重新勾选覆盖。
    """
    prompt_id: str | None = None
    skill_ids: tuple[str, ...] = ()
    for event in events:
        if isinstance(event, SettingsEvent):
            prompt_id, skill_ids = _parse_settings_value(event.settings)
    return SessionSettings(prompt_id=prompt_id, skill_ids=skill_ids)


def _parse_settings_value(
    settings: Mapping[str, JsonValue],
) -> tuple[str | None, tuple[str, ...]]:
    """解析一条设置事件的 settings 体（结构由本模块定义，sessions 只忠实存取）。

    Raises:
        SettingsFormatError: prompt / skills 字段缺失或类型不对（正常写入不会产生）。
    """
    raw_prompt = settings.get(_KEY_PROMPT)
    raw_skills = settings.get(_KEY_SKILLS, [])
    if raw_prompt is not None and not isinstance(raw_prompt, str):
        raise SettingsFormatError(
            f"会话设置的 {_KEY_PROMPT!r} 字段应是字符串；请检查会话文件是否被改动。"
        )
    if not isinstance(raw_skills, list) or not all(
        isinstance(item, str) for item in raw_skills
    ):
        raise SettingsFormatError(
            f"会话设置的 {_KEY_SKILLS!r} 字段应是字符串数组；请检查会话文件是否被改动。"
        )
    prompt = _to_prompt_id(raw_prompt)
    skills = tuple(_to_skill_id(item) for item in cast(list[str], raw_skills))
    return prompt, skills


def _to_prompt_id(value: str | None) -> str | None:
    """设置事件里的提示词引用 → ID：已是 ID 形状原样返回；旧版显示名按名解析。

    解析不到保留原值——后续读取按「引用不存在」明确报错，绝不静默换成别的资产。
    """
    if value is None or PROMPT_ID_RE.fullmatch(value):
        return value
    return prompt_id_by_display_name(value) or value


def _to_skill_id(value: str) -> str:
    """设置事件里的 skill 引用 → ID：口径同 _to_prompt_id。"""
    if SKILL_ID_RE.fullmatch(value):
        return value
    return skill_id_by_display_name(value) or value


def _replay_history(
    events: Sequence[SessionEvent], session_id: str | None
) -> tuple[Message, ...]:
    """把事件流里的历史消息重建为 llm 消息（本轮 user 消息落盘前调用，天然不含本轮）。

    历史 user 消息的附件跟随重发（2026-09-23 用户定，方案 A 全量无护栏）：图片 / 视频
    以真字节进入历史——迭代改写（「帽子改成蓝色」）时模型才看得见原图，此前历史附件
    降级为占位文本、模型第二轮起只能对着 `[图片: x]` 瞎编。字节从会话 attachments/ 读
    （与本轮同一来源；当轮参数 fps / 帧上限未随事件落盘，视频按默认值重发）；附件文件
    丢失时降级回占位文本（旧会话不因缺文件打不了字）。代价：每轮 payload 随历史附件数
    线性涨，用户知情选定；上下文压缩是真正的护栏，登记 vision 未排期池另做。
    skill 全文只在当轮注入、不进历史（当轮注入的内容已体现在当时的回复里，且每轮都会
    重新注入当前启用的 skill）。system 角色的消息事件不参与历史（system 每轮从当前
    基础提示词重新渲染），其余意外角色跳过（历史 = 对话，不是任意事件回声）。
    """
    history: list[Message] = []
    for event in events:
        if not isinstance(event, MessageEvent) or event.role not in (
            "user",
            "assistant",
        ):
            continue
        parts: list[ContentPart] = []
        if event.text:
            parts.append(TextPart(event.text))
        if event.attachment is not None and session_id is not None:
            parts.append(_history_media_part(session_id, event.attachment))
        history.append(Message(role=cast(Role, event.role), parts=tuple(parts)))
    return tuple(history)


def _history_media_part(session_id: str, attachment: str) -> ContentPart:
    """把一条历史附件还原成媒体内容块（真字节）；副本读不到时降级为占位文本。"""
    label = f"[{_attachment_label(attachment)}]"
    suffix = Path(attachment).suffix.lower()
    try:
        data = _read_attachment(session_id, attachment)
    except AttachmentReadError:
        logger.warning(
            "历史附件 %r 读取失败，本轮以占位文本降级（会话 %s）。",
            attachment,
            session_id,
        )
        return TextPart(label)
    if suffix in _VIDEO_EXTENSIONS:
        return VideoPart(
            data,
            mime=VIDEO_MIME_BY_SUFFIX.get(suffix, "video/mp4"),
            label=label,
        )
    return ImagePart(data, label=label)


_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS


def _attachment_label(attachment: str) -> str:
    """历史附件的占位标签：按扩展名区分图片 / 视频（视频字节同样不随历史重发）。"""
    suffix = Path(attachment).suffix.lower()
    kind = "视频" if suffix in _VIDEO_EXTENSIONS else "图片"
    return f"{kind}: {attachment}"


def _load_enabled_skill_texts(refs: Sequence[str]) -> list[str]:
    """读出应注入的 skill 全文：会话勾选 ∩ 库级启用（停用的跳过），保持勾选顺序。

    引用接受 skill ID 或唯一显示名（与读取层宽容口径一致），注入前先规范化为 ID。
    库里解析不到的引用（拼错，或会话设置里残留的已删除 skill）直接报错而不是静默
    跳过——静默跳过会让用户以为 skill 生效了、输出却莫名变差，排查成本高；fail loud
    才能当场纠正。
    """
    if not refs:
        return []
    canonical = [get_skill(ref).id for ref in refs]
    skills = list_skills()
    enabled = {skill.id for skill in skills if skill.enabled}
    return [read_skill(sid) for sid in canonical if sid in enabled]


def _assemble(
    *,
    prompt_body: str,
    skill_texts: Sequence[str],
    history: Sequence[Message],
    instruction: str,
    image_bytes: bytes | None,
    video_bytes: bytes | None,
    video_mime: str,
    video_fps: int,
    video_max_frames: int,
    attachment: str | None,
) -> tuple[list[Message], list[JsonValue]]:
    """组装一轮打标，同时产出两个视图。

    同一处逻辑生成、两个视图不会漂移：llm 消息（真实请求，图片 / 视频是真字节）与信封消息
    （人类复盘快照，媒体渲染为占位文本——base64 无人能读且撑爆事件流）。

    Args:
        prompt_body: 基础提示词正文（进 system 消息）。
        skill_texts: 要注入的 skill 全文列表（逐个 <skill> 包裹进当轮 user 消息）。
        history: 回放出的历史消息。
        instruction: 本轮用户指令。
        image_bytes: 本轮图片字节；None 表示无图。
        video_bytes: 本轮视频字节；None 表示无视频（与图片互斥，调用方已校验）。
        video_fps: 视频抽帧 fps（随附件可调）。
        video_max_frames: 视频抽帧帧数上限（随附件可调）。
        attachment: 本轮附件名（进信封占位文本）；None 表示无附件。

    Returns:
        (llm 消息列表, 信封消息视图列表)。
    """
    current_parts: list[TextPart | ImagePart | VideoPart] = []
    current_text_parts: list[str] = []
    for text in skill_texts:
        wrapped = f"{_SKILL_OPEN}\n{text}\n{_SKILL_CLOSE}"
        current_parts.append(TextPart(wrapped))
        current_text_parts.append(wrapped)
    if instruction:
        current_parts.append(TextPart(instruction))
        current_text_parts.append(instruction)
    if image_bytes is not None:
        placeholder = f"[图片: {attachment}]"
        current_parts.append(ImagePart(image_bytes))
        current_text_parts.append(placeholder)
    elif video_bytes is not None:
        placeholder = f"[视频: {attachment}]"
        current_parts.append(
            VideoPart(
                video_bytes, mime=video_mime, fps=video_fps, max_frames=video_max_frames
            )
        )
        current_text_parts.append(placeholder)

    messages: list[Message] = [
        Message(role="system", parts=(TextPart(prompt_body),)),
        *history,
        Message(role="user", parts=tuple(current_parts)),
    ]
    envelope_messages: list[JsonValue] = [
        {"role": "system", "content": prompt_body},
        *(_envelope_view(message) for message in history),
        {"role": "user", "content": "\n".join(current_text_parts)},
    ]
    return messages, envelope_messages


def _envelope_view(message: Message) -> dict[str, JsonValue]:
    """把一条历史消息渲染成信封视图（人类复盘快照；媒体块渲染为占位文本——base64 无人能读）。"""
    chunks: list[str] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            chunks.append(part.text)
        else:
            # ImagePart / VideoPart：信封只留占位标签（label 由历史回放装配带上；
            # 当轮消息不经此视图，装配层自己拼占位）。label 缺失时退回通用占位。
            chunks.append(part.label or "[媒体]")
    return {
        "role": message.role,
        "content": "\n".join(chunks),
    }
