/** 消息流：历史消息渲染 + 流式增量面板（本轮生成中的临时消息）。 */
import { CheckIcon, CopyIcon, FilmIcon, ImageIcon, PlayIcon } from "lucide-react";
import type { ReactElement } from "react";
import { Button } from "../../components/ui/button";
import { Tip } from "../../components/ui/tooltip";
import logo from "../../logo-speed-d.png";
import type { ChatMessage } from "./types";

/** 视频扩展名清单（与后端 MIME 映射同一份）：历史消息只带文件名，靠它认素材类型。 */
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".webm", ".m4v"];

function isVideoAttachment(name: string): boolean {
  const lowered = name.toLowerCase();
  return VIDEO_EXTENSIONS.some((extension) => lowered.endsWith(extension));
}

/**
 * 思考过程折叠区（历史消息与本轮流式面板共用一种形态）。
 *
 * 两处数据来源不同：本轮的在页面内存里逐段累加，历史恢复的随消息一起从落盘快照取回——
 * 都汇到 `reasoning` 这一个字段，所以这里只问「有没有」。
 */
function Thinking({
  reasoning,
}: {
  reasoning: string | null | undefined;
}): ReactElement | null {
  if (reasoning === null || reasoning === undefined || reasoning === "") {
    return null;
  }
  return (
    <details className="mb-2 max-w-full overflow-hidden rounded-lg border border-border bg-muted/40">
      <summary className="cursor-pointer px-3 py-2 text-t-sm text-text-3">
        思考过程
      </summary>
      <p className="px-3 pb-3 text-t-md leading-(--lh-loose) text-text-3 wrap-anywhere whitespace-pre-wrap">
        {reasoning}
      </p>
    </details>
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
        <p className="mt-8 text-center text-t-sm text-text-4">暂无消息</p>
      )}
      {messages.map((message) =>
        message.role === "user" ? (
          <div key={message.id} className="flex justify-end">
            {/* 原型口径：附件在气泡外上方，只显示缩略图/封面，文件名悬停浮现 */}
            <div className="flex max-w-[94%] min-w-0 flex-col items-end gap-1">
              {message.attachment !== null && (
                <Tip label={message.attachment}>
                  <span className="relative flex size-14 items-center justify-center overflow-hidden rounded-lg bg-muted shadow-xs">
                    {isVideoAttachment(message.attachment) ? (
                      <>
                        <FilmIcon className="size-5 text-text-3" />
                        <span className="absolute flex size-4 items-center justify-center rounded-sm bg-black/60 text-white">
                          <PlayIcon className="size-2.5" />
                        </span>
                      </>
                    ) : message.attachmentDataUrl !== undefined ? (
                      <img
                        src={message.attachmentDataUrl}
                        alt=""
                        className="size-full object-cover"
                      />
                    ) : (
                      <ImageIcon className="size-5 text-text-3" />
                    )}
                  </span>
                </Tip>
              )}
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
              <Thinking reasoning={message.reasoning} />
              <div className="inline-block max-w-full rounded-xl rounded-bl-sm border border-input bg-muted/55 px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
                {message.text}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-3 text-t-sm text-muted-foreground">
                {(message.model !== undefined ||
                  message.durationSeconds !== undefined) && (
                  <span className="min-w-0 truncate">
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
            <Thinking reasoning={streaming.reasoning} />
            <div className="inline-block max-w-full rounded-xl rounded-bl-sm border border-input bg-muted/55 px-4 py-3 text-t-md wrap-anywhere whitespace-pre-wrap">
              {streaming.content}
              <span
                className="ml-0.5 inline-block h-[14px] w-[7px] animate-pulse bg-primary align-[-2px]"
                aria-hidden
              />
            </div>
            <div className="mt-2 text-t-sm text-text-4">生成中…</div>
          </div>
        </div>
      )}
    </div>
  );
}
