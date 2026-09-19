import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../api";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

export function RemoveUnimportedDialog({
  wid,
  name,
  onClose,
  onRemoved,
}: {
  wid: string;
  name: string;
  onClose: () => void;
  onRemoved: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [recoveryPath, setRecoveryPath] = useState<string | null>(null);
  const active = useRef(false);
  const submitting = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  async function remove() {
    if (submitting.current || recoveryPath) return;
    submitting.current = true;
    setPending(true);
    setError("");
    try {
      const result = await api.removeUnimported(wid, [name]);
      if (!active.current) return;
      if (
        result.count !== 1 ||
        typeof result.recovery_path !== "string" ||
        !result.recovery_path.trim()
      )
        throw new Error("删除结果格式无效，请刷新清单核对文件位置");
      setRecoveryPath(result.recovery_path);
      onRemoved();
    } catch (reason) {
      if (active.current) setError(errorMessage(reason));
    } finally {
      submitting.current = false;
      if (active.current) setPending(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{recoveryPath ? "已删除文件" : "删除未导入文件"}</DialogTitle>
          <DialogDescription className="break-all">{name}</DialogDescription>
        </DialogHeader>
        {recoveryPath ? (
          <div role="status" className="space-y-2 text-t-md text-text-3">
            <p>文件已从工作目录移出。原文件保留在以下位置：</p>
            <p className="break-all rounded-md bg-muted p-3">{recoveryPath}</p>
          </div>
        ) : (
          <p className="text-t-md text-text-3">
            此文件将从工作目录移出，不再出现在未导入清单中。原文件会保留在恢复目录；就地采用的文件也会从原位置移出。
          </p>
        )}
        {error && <FormError className="text-t-sm text-bad-ink">{error}</FormError>}
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            {recoveryPath ? "关闭" : "取消"}
          </Button>
          {!recoveryPath && (
            <Button
              variant="destructive-fill"
              disabled={pending}
              onClick={() => void remove()}
            >
              {pending ? "删除中" : "删除"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
