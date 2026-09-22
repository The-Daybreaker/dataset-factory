/** 消息流：历史消息渲染 + 流式增量面板（本轮生成中的临时消息）+ 媒体大图预览挂点。 */
import {
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  FilmIcon,
  ImageIcon,
  PlayIcon,
} from "lucide-react";
import { type ReactElement, useState } from "react";
import type { MediaPreviewTarget } from "../../components/media-lightbox";
import { Button } from "../../components/ui/button";
import { Tip } from "../../components/ui/tooltip";
import { formatDuration } from "../../lib/format";
import logo from "../../logo-speed-d.png";
import type { ChatMessage } from "./types";

/** 视频扩展名清单（与后端 MIME 映射同源：llm/messages.py 的 VIDEO_MIME_BY_SUFFIX）：历史消息只带文件名，靠它认素材类型。 */
const VIDEO_EXTENSIONS = [".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv"];

function isVideoAttachment(name: string): boolean {
  const lowered = name.toLowerCase();
  return VIDEO_EXTENSIONS.some((extension) => lowered.endsWith(extension));
}

/** 秒数 → `0:08` 形态的时长角标（视频封面右上角；原型 :1049-1083 口径）。 */
function durationBadge(seconds: number | undefined): string | null {
  if (seconds === undefined || !Number.isFinite(seconds)) return null;
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** AI 回复的整框（原型 v20 `.ai-box`）：白底无边框、大圆角 + 左下角 4px，思考与正文同框。 */
function AiBox({ children }: { children: React.ReactNode }): ReactElement {
  return (
    <div className="max-w-[94%] overflow-hidden rounded-xl rounded-bl-sm bg-card">
      {children}
    </div>
  );
}

/**
 * 思考过程折叠区（AI 整框内的透明分区，原型 `.ai-box > .think`）。
 *
 * 流式期间恒展开（N1③），结束后收起可回看；与正文之间的横线由 AiBox 内容层
 * 按「两侧都有内容」显式渲染，这里不画——仅思考无正文的半截消息不出悬空线。
 */
function Thinking({
  reasoning,
  streaming,
  seconds,
}: {
  reasoning: string | null | undefined;
  streaming?: boolean;
  seconds?: number;
}): ReactElement | null {
  if (reasoning === null || reasoning === undefined || reasoning === "") {
    return null;
  }
  return (
    <details className="group" {...(streaming ? { open: true } : {})}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-t-sm text-text-3 hover:text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronDownIcon
          className="size-3.5 shrink-0 transition-transform duration-150 group-open:rotate-180"
          aria-hidden
        />
        <span className="font-medium">思考过程</span>
        {seconds !== undefined && !streaming ? (
          <span className="ml-auto tabular-nums">
            已思考 {formatDuration(seconds * 1000)}
          </span>
        ) : null}
      </summary>
      <p className="px-3 pb-3 text-t-md leading-(--lh-loose) text-text-3 wrap-anywhere whitespace-pre-wrap">
        {reasoning}
      </p>
    </details>
  );
}

/** 视频封面缺失时的现解码兜底（仅历史消息走得到）：`#t=0.1` 媒体片段让浏览器渲染首帧；解不出落图标。 */
function VideoThumbFrame({ src }: { src: string }): ReactElement {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <FilmIcon className="size-5 text-text-3" />;
  }
  return (
    <video
      src={`${src}#t=0.1`}
      preload="metadata"
      muted
      playsInline
      tabIndex={-1}
      aria-hidden
      onError={() => setFailed(true)}
      className="pointer-events-none size-full object-cover"
    />
  );
}

/** 用户消息的附件缩略图：当轮有封面/字节、历史有字节地址，都没有再落图标。点击开大图预览。 */
function AttachmentThumb({
  message,
  onPreview,
}: {
  message: ChatMessage;
  onPreview: (target: MediaPreviewTarget) => void;
}): ReactElement {
  if (message.attachment === null) {
    return <span />;
  }
  const video = isVideoAttachment(message.attachment);
  const src = message.attachmentDataUrl ?? message.attachmentUrl;
  const cover = message.attachmentPosterUrl;
  const badge = video ? durationBadge(message.attachmentDurationSec) : null;
  const preview: MediaPreviewTarget | null =
    src === undefined
      ? null
      : {
          url: src,
          kind: video ? "video" : "image",
          name: message.attachment,
          ...(cover !== undefined ? { posterUrl: cover } : {}),
        };
  return (
    <Tip label={message.attachment}>
      <button
        type="button"
        aria-label={
          preview !== null ? `预览 ${message.attachment}` : message.attachment
        }
        disabled={preview === null}
        onClick={() => {
          if (preview !== null) onPreview(preview);
        }}
        className="relative flex size-14 cursor-zoom-in items-center justify-center overflow-hidden rounded-lg bg-muted shadow-xs disabled:cursor-default"
      >
        {video ? (
          cover !== undefined ? (
            <>
              <img src={cover} alt="" className="size-full object-cover" />
              <span className="absolute flex size-4 items-center justify-center rounded-sm bg-black/60 text-white">
                <PlayIcon className="size-2.5" />
              </span>
            </>
          ) : message.attachmentUrl !== undefined ? (
            <VideoThumbFrame src={message.attachmentUrl} />
          ) : (
            // 当轮没抽到封面 = 本浏览器解不了这段视频（同一套解码器），<video> 兜底没有意义。
            <FilmIcon className="size-5 text-text-3" />
          )
        ) : src !== undefined ? (
          <img src={src} alt="" className="size-full object-cover" />
        ) : (
          <ImageIcon className="size-5 text-text-3" />
        )}
        {badge !== null && (
          <span className="absolute right-0.5 bottom-0.5 rounded-sm bg-black/60 px-1 text-[10px] leading-4 text-white">
            {badge}
          </span>
        )}
      </button>
    </Tip>
  );
}

export function MessageList({
  messages,
  streaming,
  waitSeconds,
  copiedId,
  onCopy,
  onPreview,
}: {
  messages: ChatMessage[];
  streaming: { reasoning: string; content: string } | null;
  waitSeconds: number;
  copiedId: number | null;
  onCopy: (message: ChatMessage) => void;
  onPreview: (target: MediaPreviewTarget) => void;
}): ReactElement {
  return (
    <div
      role="log"
      aria-label="消息流"
      className="min-h-0 flex-1 space-y-4 overflow-y-auto py-1"
    >
      {messages.length === 0 && (
        <p className="mt-8 text-center text-t-sm text-text-4">
          暂无消息。旧会话仍保存在磁盘上，本页只显示最近一次会话。
        </p>
      )}
      {messages.map((message) =>
        message.role === "user" ? (
          <div key={message.id} className="flex justify-end">
            {/* 原型口径：附件在气泡外上方；仅附件不打字时只显示缩略图，不出空气泡 */}
            <div className="flex max-w-[94%] min-w-0 flex-col items-end gap-1">
              {message.attachment !== null && (
                <AttachmentThumb message={message} onPreview={onPreview} />
              )}
              {message.text !== "" && (
                <div className="rounded-xl rounded-br-sm bg-primary/10 px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                  {message.text}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div key={message.id} className="flex items-start">
            <span
              className="mt-0.5 mr-3 flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-sm bg-black"
              aria-hidden
            >
              <img src={logo} className="size-5 object-contain" alt="" />
            </span>
            <div className="min-w-0 flex-1">
              {(() => {
                const hasReasoning =
                  message.reasoning !== null &&
                  message.reasoning !== undefined &&
                  message.reasoning !== "";
                const hasText = message.text !== "";
                if (!hasReasoning && !hasText) return null;
                return (
                  <AiBox>
                    {hasReasoning && (
                      <Thinking
                        reasoning={message.reasoning}
                        seconds={
                          message.reasoningSeconds ??
                          (message.reasoning_ms !== null &&
                          message.reasoning_ms !== undefined
                            ? message.reasoning_ms / 1000
                            : undefined)
                        }
                      />
                    )}
                    {hasReasoning && hasText && (
                      <div className="mx-3.5 h-px bg-border" aria-hidden />
                    )}
                    {hasText && (
                      <div className="px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                        {message.text}
                      </div>
                    )}
                  </AiBox>
                );
              })()}
              <div className="mt-2 flex flex-wrap items-center gap-3 text-t-sm text-muted-foreground">
                {message.partial === true && (
                  <span className="rounded-sm bg-amber-100 px-1.5 py-0.5 text-t-xs text-amber-700">
                    未完成 · 生成中断，只保留已收到的部分
                  </span>
                )}
                {(message.model !== undefined ||
                  message.durationSeconds !== undefined ||
                  message.elapsed_ms !== null) && (
                  <span className="min-w-0 truncate">
                    {[
                      message.model,
                      message.durationSeconds !== undefined
                        ? formatDuration(message.durationSeconds * 1000)
                        : message.elapsed_ms !== null &&
                            message.elapsed_ms !== undefined
                          ? formatDuration(message.elapsed_ms)
                          : null,
                    ]
                      .filter((part) => part !== null && part !== undefined)
                      .join(" · ")}
                  </span>
                )}
                <Tip label={copiedId === message.id ? "已复制" : "复制"}>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="复制 caption"
                    onClick={() => onCopy(message)}
                  >
                    {copiedId === message.id ? <CheckIcon /> : <CopyIcon />}
                  </Button>
                </Tip>
                {message.createdAt !== undefined && (
                  <span className="ml-auto">
                    {message.createdAt.toLocaleTimeString("zh-CN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                )}
              </div>
            </div>
          </div>
        ),
      )}
      {streaming !== null && (
        <div className="flex items-start">
          <span
            className="mt-0.5 mr-3 flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-sm bg-black"
            aria-hidden
          >
            <img src={logo} className="size-5 object-contain" alt="" />
          </span>
          <div className="min-w-0 flex-1">
            <AiBox>
              <Thinking reasoning={streaming.reasoning} streaming={true} />
              {streaming.reasoning !== "" && streaming.content !== "" && (
                <div className="mx-3.5 h-px bg-border" aria-hidden />
              )}
              {streaming.content === "" ? (
                // 首个增量到达前的唯一状态行（N1② + 状态收敛：此时 meta 行不出现）。
                <div className="px-4 py-3 text-t-md text-text-4 tabular-nums">
                  响应中 · 已用时 {waitSeconds}s
                </div>
              ) : (
                <div className="px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                  {streaming.content}
                  {/* 流式光标用静态样式（L14：全站唯一持续动画是 .dot--run 的呼吸点） */}
                  <span
                    className="ml-0.5 inline-block h-[14px] w-[7px] bg-primary align-[-2px]"
                    aria-hidden
                  />
                </div>
              )}
            </AiBox>
            {streaming.content !== "" && (
              <div className="mt-2 text-t-sm text-text-4 tabular-nums">
                生成中 · 已用时 {waitSeconds}s · {streaming.content.length} 字
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
