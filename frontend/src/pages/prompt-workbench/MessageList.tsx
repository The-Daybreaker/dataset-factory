/** 消息流：历史消息渲染 + 流式增量面板（本轮生成中的临时消息）。 */
import { CheckIcon, CopyIcon, FilmIcon, ImageIcon, PlayIcon } from "lucide-react";
import type { ReactElement } from "react";
import { Button } from "../../components/ui/button";
import { Tip } from "../../components/ui/tooltip";
import { formatDuration } from "../../lib/format";
import logo from "../../logo-speed-d.png";
import type { ChatMessage } from "./types";

/** 视频扩展名清单（与后端 MIME 映射同一份）：历史消息只带文件名，靠它认素材类型。 */
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".webm", ".m4v"];

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

/**
 * 思考过程折叠区（历史消息与本轮流式面板共用一种形态）。
 *
 * 两处数据来源不同：本轮的在页面内存里逐段累加，历史恢复的随消息一起从落盘快照取回——
 * 都汇到 `reasoning` 这一个字段，所以这里只问「有没有」。
 * 流式期间恒展开（N1③，2026-09-21 审计：思考框到达即被折叠，用户看到的是「什么都没有」），
 * 结束后收起可回看（原型 `class="think open"` 的意图）。
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
    <details
      className="mb-2 max-w-full overflow-hidden rounded-lg border border-border bg-muted/40"
      {...(streaming ? { open: true } : {})}
    >
      <summary className="cursor-pointer px-3 py-2 text-t-sm text-text-3">
        思考过程
        {seconds !== undefined && !streaming
          ? ` · 已思考 ${formatDuration(seconds * 1000)}`
          : ""}
      </summary>
      <p className="px-3 pb-3 text-t-md leading-(--lh-loose) text-text-3 wrap-anywhere whitespace-pre-wrap">
        {reasoning}
      </p>
    </details>
  );
}

/** 用户消息的附件缩略图：当轮有 data URL、历史有字节地址，都没有再落图标。 */
function AttachmentThumb({ message }: { message: ChatMessage }): ReactElement {
  const src = message.attachmentDataUrl ?? message.attachmentUrl;
  if (message.attachment === null) {
    return <span />;
  }
  const video = isVideoAttachment(message.attachment);
  const badge = video ? durationBadge(message.attachmentDurationSec) : null;
  return (
    <Tip label={message.attachment}>
      <span className="relative flex size-14 items-center justify-center overflow-hidden rounded-lg bg-muted shadow-xs">
        {video ? (
          src !== undefined ? (
            <>
              <img src={src} alt="" className="size-full object-cover" />
              <span className="absolute flex size-4 items-center justify-center rounded-sm bg-black/60 text-white">
                <PlayIcon className="size-2.5" />
              </span>
            </>
          ) : (
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
      </span>
    </Tip>
  );
}

export function MessageList({
  messages,
  streaming,
  copiedId,
  onCopy,
}: {
  messages: ChatMessage[];
  streaming: { reasoning: string; content: string } | null;
  copiedId: number | null;
  onCopy: (message: ChatMessage) => void;
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
            {/* 原型口径：附件在气泡外上方，只显示缩略图/封面，文件名悬停浮现 */}
            <div className="flex max-w-[94%] min-w-0 flex-col items-end gap-1">
              {message.attachment !== null && <AttachmentThumb message={message} />}
              <div className="rounded-xl rounded-br-sm bg-primary/10 px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                {message.text}
              </div>
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
              <Thinking
                reasoning={message.reasoning}
                seconds={
                  message.reasoningSeconds ??
                  (message.reasoning_ms !== null && message.reasoning_ms !== undefined
                    ? message.reasoning_ms / 1000
                    : undefined)
                }
              />
              <div className="inline-block max-w-full rounded-xl rounded-bl-sm border border-input bg-muted/55 px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                {message.text}
              </div>
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
            <Thinking reasoning={streaming.reasoning} streaming={true} />
            {streaming.content === "" ? (
              // 首个增量到达前的占位行（N1②：不再渲染只有内边距的空气泡）。
              <div className="text-t-md text-text-4">响应中…</div>
            ) : (
              <div className="inline-block max-w-full rounded-xl rounded-bl-sm border border-input bg-muted/55 px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                {streaming.content}
                {/* 流式光标用静态样式（L14：全站唯一持续动画是 .dot--run 的呼吸点） */}
                <span
                  className="ml-0.5 inline-block h-[14px] w-[7px] bg-primary align-[-2px]"
                  aria-hidden
                />
              </div>
            )}
            <div className="mt-2 text-t-sm text-text-4">生成中…</div>
          </div>
        </div>
      )}
    </div>
  );
}
