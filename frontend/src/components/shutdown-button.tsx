/**
 * 页头「关闭服务」按钮 + 确认弹窗（F6）。
 *
 * 确认后调停机接口：服务把手头请求做完再退出，会话数据已实时落盘。图标态用于页头，
 * 文字态用于设置页「服务运行」子页的状态卡。
 */
import { PowerIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { api } from "../api";
import { reportError } from "../lib/feedback";
import { FormError } from "./form-error";
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
  sidebar = false,
}: {
  expanded?: boolean;
  sidebar?: boolean;
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
      // 停机被受理 = 服务开始排入手头请求。侧栏的状态点监听此事件转「正在停止」，
      // 并在确认期里逐秒重查，探到不通就定在红点（与脚本直接杀进程的表现收敛到同一处）。
      window.dispatchEvent(new Event("df:service-stopping"));
    } catch (err) {
      const text = reportError(err);
      if (text !== null) setError(text);
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
          variant={sidebar ? "ghost" : "outline"}
          size="icon"
          className={
            sidebar
              ? "text-bad-ink hover:bg-bad-bg hover:text-bad-ink-strong"
              : "border-border bg-card text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          }
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
            <FormError className="text-[12.5px] text-destructive">{error}</FormError>
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
