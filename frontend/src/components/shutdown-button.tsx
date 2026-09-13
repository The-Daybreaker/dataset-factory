/**
 * 页头「关闭服务」按钮 + 确认弹窗（F6）。
 *
 * 确认后调停机接口：服务把手头请求做完再退出，会话数据已实时落盘。图标态用于页头，
 * 文字态用于设置页「服务运行」子页的状态卡。
 */
import { PowerIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { api, errorMessage } from "../api";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";

export function ShutdownButton({
  expanded = false,
}: {
  expanded?: boolean;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const shutdown = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await api.shutdownService();
      setOpen(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {expanded ? (
        <Button
          type="button"
          variant="destructive"
          size="sm"
          aria-label="关闭服务"
          onClick={() => setOpen(true)}
        >
          <PowerIcon className="size-4" />
          关闭服务
        </Button>
      ) : (
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="border-border bg-card text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          aria-label="关闭服务"
          onClick={() => setOpen(true)}
        >
          <PowerIcon className="size-4" />
        </Button>
      )}
      <Dialog
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (!value) {
            setError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>关闭服务？</DialogTitle>
            <DialogDescription>
              关闭前会等正在进行的请求跑完，不会中断生成；会话数据已实时保存、不受影响。
            </DialogDescription>
          </DialogHeader>
          {error !== null && (
            <p className="text-[12.5px] text-destructive" role="alert">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              type="button"
              variant="destructive-fill"
              disabled={busy}
              onClick={() => void shutdown()}
            >
              关闭服务
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
