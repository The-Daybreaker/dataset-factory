/**
 * 媒体大图预览（PRD-0004 交互 6）：图片原件 / 视频播放的浮层。
 *
 * 基于 Radix Dialog：Esc 关闭、点遮罩关闭、焦点锁定与滚动锁定都由 Dialog 承担；
 * 遮罩口径与 ui/dialog 一致（纯黑 32%、暗色 55%，规范 §2.7）。视频解码失败
 * （HEVC 等浏览器不支持的编码）时退封面 / 图标并提示，不弹报错。
 */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { FilmIcon, XIcon } from "lucide-react";
import { type ReactElement, useState } from "react";
import { cn } from "../lib/utils";

/** 预览目标的最小描述：原件地址 + 类型 + 文件名（视频另带封面，播放失败时展示）。 */
export interface MediaPreviewTarget {
  url: string;
  kind: "image" | "video";
  name: string;
  posterUrl?: string;
}

export function MediaLightbox({
  target,
  onClose,
}: {
  target: MediaPreviewTarget | null;
  onClose: () => void;
}): ReactElement {
  // 记「解码失败的那份 URL」而不是布尔值：换一个预览目标自动重试，同目标内保持失败态。
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const videoFailed = target !== null && failedUrl === target.url;
  return (
    <DialogPrimitive.Root
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/32 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 dark:bg-black/55" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 flex items-center justify-center p-6 focus:outline-none"
          // 点在媒体之外的空白区等同点遮罩：内容层铺满视口，自行判定点击落点。
          onClick={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <DialogPrimitive.Title className="sr-only">
            {target?.name ?? "媒体预览"}
          </DialogPrimitive.Title>
          {target?.kind === "image" ? (
            <img
              src={target.url}
              alt={target.name}
              className="max-h-full max-w-full rounded-lg object-contain shadow-lg"
            />
          ) : target !== null && !videoFailed ? (
            // biome-ignore lint/a11y/useMediaCaption: 本地原件播放，无字幕轨道可挂（a11y 不在本项目考虑范围）
            <video
              key={target.url}
              src={target.url}
              controls
              autoPlay
              onError={() => setFailedUrl(target.url)}
              className="max-h-full max-w-full rounded-lg shadow-lg"
            />
          ) : target !== null ? (
            <div className="flex max-w-[80vw] flex-col items-center gap-3 rounded-xl bg-card px-10 py-12 shadow-lg">
              {target.posterUrl !== undefined ? (
                <img
                  src={target.posterUrl}
                  alt=""
                  className="max-h-[60vh] rounded-lg object-contain"
                />
              ) : (
                <FilmIcon className="size-10 text-text-4" aria-hidden />
              )}
              <p className="max-w-full truncate text-t-sm text-text-3">{target.name}</p>
              <p className="text-t-sm text-text-4">浏览器不支持该编码，无法播放</p>
            </div>
          ) : null}
          <DialogPrimitive.Close
            aria-label="关闭预览"
            className={cn(
              "absolute top-4 right-4 flex size-9 items-center justify-center rounded-md",
              "text-white/85 transition-colors hover:bg-white/15 hover:text-white",
            )}
          >
            <XIcon className="size-5" />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
