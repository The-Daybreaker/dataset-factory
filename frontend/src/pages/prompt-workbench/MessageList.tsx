/** 消息流：历史消息渲染 + 流式增量面板（本轮生成中的临时消息）。 */
import { FilmIcon, ImageIcon, SparklesIcon } from "lucide-react";
import type { ReactElement } from "react";
import type { ChatMessage } from "./types";

/** 视频扩展名清单（与后端 MIME 映射同一份）：历史消息只带文件名，靠它认素材类型。 */
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".webm", ".m4v"];

function isVideoAttachment(name: string): boolean {
  const lowered = name.toLowerCase();
  return VIDEO_EXTENSIONS.some((extension) => lowered.endsWith(extension));
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
      className="mt-1 min-h-0 flex-1 space-y-[18px] overflow-y-auto pr-1"
    >
      {messages.length === 0 && (
        <p className="mt-8 text-center text-[12px] text-muted-foreground">
          （还没有消息——发图片或视频 + 指令开始打标）
        </p>
      )}
      {messages.map((message) =>
        message.role === "user" ? (
          <div key={message.id} className="flex justify-end">
            <div className="max-w-[94%] rounded-xl rounded-br-[4px] bg-primary/10 px-3.5 py-[11px] text-[13.5px] whitespace-pre-wrap">
              {message.text}
              {message.attachment !== null && (
                <span className="mt-2.5 flex items-center gap-2.5 rounded-lg bg-card py-2 pr-3.5 pl-2 text-[12px] text-muted-foreground shadow-sm">
                  {isVideoAttachment(message.attachment) ? (
                    <FilmIcon className="size-7 shrink-0 rounded-md bg-muted p-1.5" />
                  ) : (
                    <ImageIcon className="size-7 shrink-0 rounded-md bg-muted p-1.5" />
                  )}
                  <span className="truncate">{message.attachment}</span>
                </span>
              )}
            </div>
          </div>
        ) : (
          <div key={message.id} className="flex items-start">
            <span
              className="mt-0.5 mr-2.5 flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground"
              aria-hidden
            >
              <SparklesIcon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              {/* 生成结束后的思考过程保留可回看（本轮内存态，不落盘；历史恢复的消息没有）。 */}
              {message.reasoning !== undefined && message.reasoning !== "" && (
                <details className="mb-2 max-w-full overflow-hidden rounded-lg border border-border bg-muted/40">
                  <summary className="cursor-pointer px-3 py-1.5 text-[12px] text-muted-foreground">
                    思考过程
                  </summary>
                  <p className="px-3 pb-2.5 text-[12.5px] leading-[1.65] text-muted-foreground whitespace-pre-wrap">
                    {message.reasoning}
                  </p>
                </details>
              )}
              <div className="inline-block max-w-full rounded-xl rounded-bl-[4px] bg-muted/55 px-3.5 py-[11px] text-[13.5px] whitespace-pre-wrap">
                {message.text}
              </div>
              <div className="mt-2 flex items-center gap-2.5 text-[11.5px] text-muted-foreground">
                {(message.model !== undefined ||
                  message.durationSeconds !== undefined) && (
                  <span>
                    {[
                      message.model,
                      message.durationSeconds !== undefined
                        ? `${message.durationSeconds}s`
                        : null,
                    ]
                      .filter((part) => part !== null && part !== undefined)
                      .join(" · ")}
                  </span>
                )}
                <button
                  type="button"
                  className="text-primary underline underline-offset-[3px] hover:opacity-80"
                  aria-label="复制 caption"
                  onClick={() => onCopy(message)}
                >
                  {copiedId === message.id ? "已复制" : "复制"}
                </button>
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
            className="mt-0.5 mr-2.5 flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground"
            aria-hidden
          >
            <SparklesIcon className="size-3.5" />
          </span>
          <div className="min-w-0 flex-1">
            {streaming.reasoning !== "" && (
              <details className="mb-2 max-w-full overflow-hidden rounded-lg border border-border bg-muted/40">
                <summary className="cursor-pointer px-3 py-1.5 text-[12px] text-muted-foreground">
                  思考过程
                </summary>
                <p className="px-3 pb-2.5 text-[12.5px] leading-[1.65] text-muted-foreground whitespace-pre-wrap">
                  {streaming.reasoning}
                </p>
              </details>
            )}
            <div className="inline-block max-w-full rounded-xl rounded-bl-[4px] bg-muted/55 px-3.5 py-[11px] text-[13.5px] whitespace-pre-wrap">
              {streaming.content}
              <span
                className="ml-0.5 inline-block h-[14px] w-[7px] animate-pulse bg-primary align-[-2px]"
                aria-hidden
              />
            </div>
            <div className="mt-2 text-[11.5px] text-muted-foreground">生成中…</div>
          </div>
        </div>
      )}
    </div>
  );
}
