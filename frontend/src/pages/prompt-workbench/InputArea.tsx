/** 输入区：打标指令文本框 + 附件选择 + 视频抽帧参数 + 发送按钮。 */
import { FilmIcon, PaperclipIcon } from "lucide-react";
import type { ChangeEvent, KeyboardEvent, ReactElement } from "react";
import { Button } from "../../components/ui/button";
import { Textarea } from "../../components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import type { PendingMedia } from "./types";

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
}): ReactElement {
  return (
    <div className="mt-3.5 pt-0">
      <Textarea
        aria-label="打标指令"
        placeholder="输入指令，继续交互……"
        className="min-h-[62px] rounded-lg border-input bg-card px-3.5 py-[11px] text-[13.5px] leading-[1.6]"
        value={instruction}
        onInput={(event) => onInstructionChange(event.currentTarget.value)}
        onKeyDown={onInstructionKeyDown}
      />
      <div className="mt-2.5 flex items-center gap-2.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <label className="inline-flex h-[30px] cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-[12px] hover:bg-accent">
              <PaperclipIcon className="size-3.5" />
              附件
              <span className="sr-only">附图片或视频（一期单素材 / 次）</span>
              <input
                type="file"
                accept="image/*,video/mp4,video/quicktime,video/webm,video/x-msvideo,video/x-matroska,video/x-m4v"
                className="sr-only"
                aria-label="附图或视频"
                onChange={onPickMedia}
              />
            </label>
          </TooltipTrigger>
          <TooltipContent>附图片或视频（一期单素材 / 次）</TooltipContent>
        </Tooltip>
        {media !== null && media.kind === "image" && (
          <img
            className="size-9 rounded-md border border-border object-cover"
            src={media.dataUrl}
            alt={`待打标图片 ${media.name}`}
          />
        )}
        {media !== null && media.kind === "video" && (
          <span className="inline-flex items-center gap-2 rounded-md border border-border bg-muted/40 px-2 py-1 text-[11.5px] text-muted-foreground">
            <FilmIcon className="size-4" />
            <span className="max-w-40 truncate">{media.name}</span>
            <label className="flex items-center gap-1">
              fps
              <input
                type="number"
                min={1}
                max={10}
                step={1}
                value={media.fps}
                onChange={(event) => {
                  // 先取值再进 setState 更新器：更新器延迟执行时合成事件的
                  // currentTarget 已被 React 置空，更新器内读取会抛错崩树
                  //（2026-09-14 验收实测白屏，memory 59① 同款反模式）。
                  onMediaFpsChange(Number(event.currentTarget.value));
                }}
                className="h-6 w-14 rounded-md border border-input bg-background px-1.5 text-[12px]"
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
                onChange={(event) => {
                  onMediaMaxFramesChange(Number(event.currentTarget.value));
                }}
                className="h-6 w-14 rounded-md border border-input bg-background px-1.5 text-[12px]"
                aria-label="视频抽帧帧数上限"
              />
            </label>
            <button
              type="button"
              aria-label="移除附件"
              className="text-muted-foreground hover:text-foreground"
              onClick={onClearMedia}
            >
              ×
            </button>
          </span>
        )}
        <span className="ml-auto text-[11.5px] text-muted-foreground">
          Enter 发送 · Shift+Enter 换行
        </span>
        <Button type="button" size="sm" disabled={!canSend} onClick={onSend}>
          {sending
            ? waitSeconds < 3
              ? "已发送，等待模型…"
              : `等待模型响应…（已 ${waitSeconds}s）`
            : "发送"}
        </Button>
      </div>
    </div>
  );
}
