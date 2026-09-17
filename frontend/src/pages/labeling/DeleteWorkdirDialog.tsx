import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

export function DeleteWorkdirDialog({
  wid,
  onClose,
  onDeleted,
}: {
  wid: string;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [preview, setPreview] = useState<
    components["schemas"]["DeletionPreview"] | null
  >(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const pending = useRef(false);
  const generation = useRef(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision reloads the deletion scope for an explicit retry.
  useEffect(() => {
    const token = ++generation.current;
    setPreview(null);
    setLoading(true);
    setError("");
    void api
      .previewWorkdirDeletion(wid)
      .then(
        (value) => {
          if (token === generation.current) setPreview(value);
        },
        (reason: unknown) => {
          if (token === generation.current) setError(errorMessage(reason));
        },
      )
      .finally(() => {
        if (token === generation.current) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [wid, revision]);

  async function remove() {
    if (pending.current || !preview) return;
    pending.current = true;
    setBusy(true);
    setError("");
    const token = generation.current;
    try {
      const result = await api.deleteWorkdir(wid, preview.path);
      if (token !== generation.current) return;
      if (result.deleted) onDeleted();
      else
        setError(
          `删除未完成，残留目录：${result.remaining_path ?? preview.path}。可重新查看范围后重试。`,
        );
    } catch (reason) {
      if (token === generation.current) setError(errorMessage(reason));
    } finally {
      pending.current = false;
      if (token === generation.current) setBusy(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending.current) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>删除工作目录？</DialogTitle>
          <DialogDescription>删除全部素材、产物与记录</DialogDescription>
        </DialogHeader>
        {loading && <p role="status">正在读取删除范围</p>}
        {preview && (
          <div className="space-y-2 text-t-sm">
            <p className="break-all">{preview.path}</p>
            <p>
              {preview.file_count} 个文件 ·{" "}
              {(preview.total_bytes / 1024 / 1024).toFixed(2)} MiB
            </p>
            <p
              className={preview.original_materials ? "text-bad-ink" : "text-warn-ink"}
            >
              {preview.confirmation}
            </p>
          </div>
        )}
        {error && (
          <p role="alert" className="break-all text-t-sm text-bad-ink">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            取消
          </Button>
          {error && (
            <Button
              variant="outline"
              disabled={busy || loading}
              onClick={() => setRevision((value) => value + 1)}
            >
              重新查看
            </Button>
          )}
          <Button
            variant="destructive-fill"
            disabled={busy || loading || !preview || !!error}
            onClick={() => void remove()}
          >
            {busy ? "正在删除" : "确认删除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
