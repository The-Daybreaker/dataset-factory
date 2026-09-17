import { FolderIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage, type TaskView } from "../../api";
import { DirectoryPicker } from "../../components/DirectoryPicker";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";

type Result = { path: string; old_path: string; cleanup_pending: boolean };

function parseResult(value: unknown): Result {
  if (
    !value ||
    typeof value !== "object" ||
    !("path" in value) ||
    typeof value.path !== "string" ||
    !("old_path" in value) ||
    typeof value.old_path !== "string" ||
    !("cleanup_pending" in value) ||
    typeof value.cleanup_pending !== "boolean"
  )
    throw new Error("搬迁结果格式无效");
  return {
    path: value.path,
    old_path: value.old_path,
    cleanup_pending: value.cleanup_pending,
  };
}

export function RelocateWorkdirDialog({
  wid,
  source,
  initialTarget = "",
  onClose,
  onChanged,
}: {
  wid: string;
  source: string;
  initialTarget?: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [target, setTarget] = useState(initialTarget);
  const [picker, setPicker] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [task, setTask] = useState<TaskView | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [pollFailed, setPollFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const pending = useRef(false);
  const cancelPending = useRef(false);
  const currentTask = useRef(task);
  currentTask.current = task;
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const taskId = task?.id;
  const status = task?.status;
  const active = busy || status === "running";

  useEffect(() => {
    generation.current += 1;
    return () => {
      generation.current += 1;
    };
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision rechecks the existing task, not a new relocation.
  useEffect(() => {
    if (!taskId || status !== "running") return;
    const id = taskId;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setPollFailed(false);
    async function poll() {
      try {
        const view = await api.getTask(id);
        if (!current) return;
        if (view.id !== id) throw new Error("任务响应与当前搬迁不一致");
        const value = view.status === "succeeded" ? parseResult(view.result) : null;
        setTask(view);
        if (view.status === "running") timer = setTimeout(() => void poll(), 2000);
        else {
          setResult(value);
          if (!value)
            setError(
              view.error || (view.status === "cancelled" ? "搬迁已取消" : "搬迁失败"),
            );
          changed.current();
        }
      } catch (reason) {
        if (!current) return;
        if (reason instanceof ApiError && reason.status === 404) {
          setTask(null);
          setConfirmed(false);
          setError("上次任务已丢失（服务重启过），可重新执行");
          changed.current();
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
  }, [taskId, status, revision]);

  async function start() {
    if (pending.current || active || !confirmed || !target.trim()) return;
    const token = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    setCancelRequested(false);
    try {
      const accepted = await api.relocateWorkdir(wid, target.trim());
      if (token !== generation.current) return;
      if (!accepted.task_id?.trim()) throw new Error("搬迁任务编号无效");
      setTask({
        id: accepted.task_id,
        status: "running",
        progress: 0,
        error: null,
        result: null,
      });
    } catch (reason) {
      if (token === generation.current) setError(errorMessage(reason));
    } finally {
      pending.current = false;
      if (token === generation.current) setBusy(false);
    }
  }

  async function cancel() {
    if (!taskId || status !== "running" || cancelPending.current || cancelRequested)
      return;
    const token = generation.current;
    const id = taskId;
    cancelPending.current = true;
    setCancelling(true);
    try {
      await api.cancelTask(id);
      if (
        token === generation.current &&
        currentTask.current?.id === id &&
        currentTask.current.status === "running"
      ) {
        setCancelRequested(true);
        setRevision((value) => value + 1);
      }
    } catch (reason) {
      if (token === generation.current && currentTask.current?.status === "running")
        setError(errorMessage(reason));
    } finally {
      cancelPending.current = false;
      if (token === generation.current) setCancelling(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !active && !pending.current) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>修改路径</DialogTitle>
          <DialogDescription className="break-all">{source}</DialogDescription>
        </DialogHeader>
        {!result && (
          <div className="space-y-2">
            <label htmlFor="relocation-target" className="text-t-sm">
              目标目录
            </label>
            <div className="flex gap-2">
              <Input
                id="relocation-target"
                value={target}
                disabled={active || confirmed}
                onChange={(event) => setTarget(event.currentTarget.value)}
              />
              <Button
                variant="ghost"
                size="icon-lg"
                aria-label="选择目标父目录"
                disabled={active || confirmed}
                onClick={() => setPicker(true)}
              >
                <FolderIcon />
              </Button>
            </div>
            {confirmed && (
              <p className="text-t-sm text-warn-ink">
                将全部内容复制到目标目录，校验通过后删除旧位置。目标目录必须尚不存在；中断搬迁时保留原位置。
              </p>
            )}
          </div>
        )}
        {error && (
          <p role="alert" className="break-all text-t-sm text-bad-ink">
            {error}
          </p>
        )}
        {status === "running" && (
          <div role="status" className="space-y-2">
            <p>
              {cancelRequested ? "正在取消" : "正在搬迁"} ·{" "}
              {Math.round((task?.progress ?? 0) * 100)}%
            </p>
            <progress
              className="w-full"
              value={task?.progress ?? 0}
              max={1}
              aria-label="搬迁进度"
            />
          </div>
        )}
        {result && (
          <div role="status" className="space-y-2 text-t-sm">
            <p className="break-all">已搬迁到 {result.path}</p>
            {result.cleanup_pending && (
              <p className="break-all text-warn-ink">
                旧位置尚未清理：{result.old_path}
              </p>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" disabled={active} onClick={onClose}>
            关闭
          </Button>
          {pollFailed && status === "running" && (
            <Button
              variant="outline"
              onClick={() => {
                setError("");
                setRevision((value) => value + 1);
              }}
            >
              重新查询
            </Button>
          )}
          {status === "running" && (
            <Button
              variant="outline"
              disabled={cancelling || cancelRequested}
              onClick={() => void cancel()}
            >
              取消搬迁
            </Button>
          )}
          {!active && !result && confirmed && (
            <Button variant="outline" onClick={() => setConfirmed(false)}>
              返回修改
            </Button>
          )}
          {!active && !result && (
            <Button
              disabled={!target.trim()}
              onClick={() => (confirmed ? void start() : setConfirmed(true))}
            >
              {confirmed ? "确认搬迁" : "修改路径"}
            </Button>
          )}
        </DialogFooter>
        {picker && (
          <DirectoryPicker
            allowCreate
            allowRename
            onClose={() => setPicker(false)}
            onSelect={(parent, listing) => {
              const windows = listing.system === "Windows";
              const separator = windows ? "\\" : "/";
              const normalizedSource = windows ? source.replaceAll("/", "\\") : source;
              const name =
                normalizedSource.split(separator).filter(Boolean).at(-1) ?? "dataset";
              const base = windows
                ? parent.replace(/[\\/]$/, "")
                : parent.replace(/\/$/, "");
              setTarget(`${base}${separator}${name}`);
              setPicker(false);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
