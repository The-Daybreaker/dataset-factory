/** 输入区：打标指令文本框 + 附件选择 + 视频抽帧参数 + 发送按钮。 */
import { ArrowUpIcon, FilmIcon, PaperclipIcon, XIcon } from "lucide-react";
import {
  type ChangeEvent,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
  useRef,
} from "react";
import { Button } from "../../components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { formatBytesAuto } from "../../lib/format";
import type { PendingMedia } from "./types";

/** MIME → 简短类型标注（待发附件卡信息行；原型口径：JPG · 842 KB 这类一眼可读的摘要）。 */
function mimeLabel(mime: string): string {
  const map: Record<string, string> = {
    "image/jpeg": "JPG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
    "video/mp4": "MP4",
    "video/quicktime": "MOV",
    "video/webm": "WEBM",
    "video/x-msvideo": "AVI",
    "video/x-matroska": "MKV",
    "video/x-m4v": "M4V",
  };
  return map[mime] ?? mime;
}

export function InputArea({
  instruction,
  onInstructionChange,
  onInstructionKeyDown,
  media,
  onPickMedia,
  onMediaFpsChange,
  onMediaMaxFramesChange,
  onClearMedia,
  canSend,
  sending,
  waitSeconds,
  onSend,
  actions,
  selectedSkills,
}: {
  instruction: string;
  onInstructionChange: (value: string) => void;
  onInstructionKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  media: PendingMedia | null;
  onPickMedia: (event: ChangeEvent<HTMLInputElement>) => void;
  onMediaFpsChange: (fps: number) => void;
  onMediaMaxFramesChange: (maxFrames: number) => void;
  onClearMedia: () => void;
  canSend: boolean;
  sending: boolean;
  waitSeconds: number;
  onSend: () => void;
  actions?: ReactNode;
  selectedSkills?: ReactNode;
}): ReactElement {
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <div className="mt-auto pt-4">
      <div className="rounded-xl border border-input bg-card p-2 focus-within:border-n-400">
        {media !== null && (
          <div className="mb-1 flex max-w-full items-center gap-2 rounded-lg border border-border bg-muted/40 p-1 text-t-sm">
            {media.kind === "image" ? (
              <img
                className="size-10 shrink-0 rounded-sm object-cover"
                src={media.dataUrl}
                alt={`待打标图片 ${media.name}`}
              />
            ) : (
              <FilmIcon className="size-10 shrink-0 rounded-sm bg-muted p-2" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate">{media.name}</div>
              <div className="text-t-xs text-muted-foreground">
                {mimeLabel(media.mime)} · {formatBytesAuto(media.byteSize)}
              </div>
              {media.kind === "video" && (
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-1">
                    fps
                    <input
                      type="number"
                      min={1}
                      max={10}
                      step={1}
                      value={media.fps}
                      onChange={(event) =>
                        onMediaFpsChange(Number(event.currentTarget.value))
                      }
                      className="h-(--h-xs) w-12 rounded-md border border-input bg-card px-1 text-t-sm"
                      aria-label="视频抽帧 fps"
                    />
                  </label>
                  <label className="flex items-center gap-1">
                    帧上限
                    <input
                      type="number"
                      min={1}
                      max={256}
                      value={media.maxFrames}
                      onChange={(event) =>
                        onMediaMaxFramesChange(Number(event.currentTarget.value))
                      }
                      className="h-(--h-xs) w-12 rounded-md border border-input bg-card px-1 text-t-sm"
                      aria-label="视频抽帧帧数上限"
                    />
                  </label>
                </div>
              )}
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="移除附件"
                  onClick={onClearMedia}
                >
                  <XIcon />
                </Button>
              </TooltipTrigger>
              <TooltipContent>移除附件</TooltipContent>
            </Tooltip>
          </div>
        )}
        <textarea
          aria-label="打标指令"
          placeholder="输入指令，继续交互……（如：把「燕麦色」改成具体的色号）"
          className="block min-h-14 w-full resize-none border-0 bg-transparent px-1 pt-2 pb-1 text-t-md text-text-2 outline-none"
          value={instruction}
          onInput={(event) => onInstructionChange(event.currentTarget.value)}
          onKeyDown={onInstructionKeyDown}
        />
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="附件"
                  onClick={() => fileInput.current?.click()}
                >
                  <PaperclipIcon />
                </Button>
              </TooltipTrigger>
              <TooltipContent>附图片或视频</TooltipContent>
            </Tooltip>
            <input
              ref={fileInput}
              type="file"
              accept="image/*,video/mp4,video/quicktime,video/webm,video/x-msvideo,video/x-matroska,video/x-m4v"
              className="sr-only"
              aria-label="附图或视频"
              onChange={onPickMedia}
            />
            {actions}
          </div>
          {selectedSkills}
          {sending && (
            <span role="status" className="text-t-xs text-text-4">
              等待模型 · {waitSeconds}s
            </span>
          )}
          <span className="ml-auto shrink-0 text-t-sm text-muted-foreground">
            Enter 发送 · Shift+Enter 换行
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="shrink-0 rounded-full bg-n-200 text-primary-foreground hover:bg-n-300"
                aria-label="发送"
                disabled={!canSend}
                onClick={onSend}
              >
                <ArrowUpIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>发送</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
