import { useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage, type TaskView } from "../../api";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

interface Props {
  wid: string;
  onClose: () => void;
  onRebuilt: () => void;
}

export function RebuildImportsDialog({ wid, onClose, onRebuilt }: Props) {
  const [task, setTask] = useState<TaskView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [pollFailed, setPollFailed] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  const generation = useRef(0);
  const submittingRef = useRef(false);
  const onRebuiltRef = useRef(onRebuilt);
  onRebuiltRef.current = onRebuilt;
  const taskId = task?.id;
  const taskStatus = task?.status;
  const busy = submitting || taskStatus === "running";

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  // biome-ignore lint/correctness/useExhaustiveDependencies: retry explicitly restarts observation of the same task.
  useEffect(() => {
    if (!taskId || taskStatus !== "running") return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const id = taskId;
    setPollFailed(false);
    async function poll() {
      try {
        const view = await api.getTask(id);
        if (!current) return;
        if (view.id !== id) throw new Error("任务响应与当前重建不一致");
        setTask(view);
        if (view.status === "running") {
          timer = setTimeout(() => void poll(), 2000);
        } else if (view.status === "succeeded") {
          onRebuiltRef.current();
          const result = view.result;
          if (
            typeof result !== "object" ||
            result === null ||
            !("file_count" in result) ||
            typeof result.file_count !== "number" ||
            !Number.isSafeInteger(result.file_count) ||
            result.file_count < 0
          )
            throw new Error("重建结果格式无效，请重新校验素材完整性");
          setCount(result.file_count);
        } else {
          setError(
            view.error || (view.status === "cancelled" ? "重建已取消" : "重建失败"),
          );
        }
      } catch (reason) {
        if (!current) return;
        if (reason instanceof ApiError && reason.status === 404) {
          setTask(null);
          setError("上次任务已丢失（服务重启过），请先校验当前记录");
          onRebuiltRef.current();
        } else {
          setError(errorMessage(reason));
          setPollFailed(true);
        }
      }
    }
    void poll();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [taskId, taskStatus, retry]);

  async function submit() {
    if (busy || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    const version = generation.current;
    try {
      const accepted = await api.rebuildImportRecords(wid);
      if (version !== generation.current) return;
      setTask({
        id: accepted.task_id,
        status: "running",
        progress: 0,
        result: null,
        error: null,
      });
    } catch (reason) {
      if (version === generation.current) setError(errorMessage(reason));
    } finally {
      submittingRef.current = false;
      if (version === generation.current) setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] w-[min(540px,100%)] max-w-none overflow-y-auto">
        <DialogHeader>
          <DialogTitle>重建导入记录</DialogTitle>
          <DialogDescription>当前工作目录</DialogDescription>
        </DialogHeader>
        <p className="rounded-md border border-warn-bd bg-warn-bg p-3 text-t-sm text-warn-ink">
          重建会按工作目录现状补一条导入记录，缺失对账以当前素材为准。来源记为空，无法再按原来源找回素材。现有产物不变；缺少打标时的素材哈希时，产物时效仍无法校验。
        </p>
        {error && (
          <p role="alert" className="text-t-sm text-bad-ink">
            {error}
          </p>
        )}
        {busy && <p role="status">正在重建导入记录</p>}
        {count !== null && (
          <p role="status" className="text-ok-ink">
            重建完成 · 登记素材 {count} 条
          </p>
        )}
        <DialogFooter>
          {!busy && (
            <Button variant="ghost" onClick={onClose}>
              {count === null ? "取消" : "关闭"}
            </Button>
          )}
          {pollFailed && taskStatus === "running" && (
            <Button
              variant="outline"
              onClick={() => {
                setError("");
                setRetry((value) => value + 1);
              }}
            >
              重新查询
            </Button>
          )}
          {count === null && (
            <Button disabled={busy} onClick={() => void submit()}>
              重建导入记录
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
