import { FolderIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, api, errorMessage, type TaskView } from "../../api";
import { DirectoryPicker } from "../../components/DirectoryPicker";
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
import { Input } from "../../components/ui/input";
import {
  Tip,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../../components/ui/tooltip";
import { formatBytesAuto } from "../../lib/format";
import {
  type ImportReport,
  parseImportReport,
  parseReimportReport,
} from "./import-report";

interface Props {
  wid: string;
  onClose: () => void;
  onImported: () => void;
  names?: string[];
  initialMode?: "copy" | "inplace" | "restore";
  initialSource?: string;
  initialReport?: ImportReport;
}

function ContentFingerprint({ size, hash }: { size: number; hash: string }) {
  const amount = formatBytesAuto(size);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="tabular-nums">
          {amount}（{hash.slice(0, 8)}…）
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80 break-all">SHA-256 {hash}</TooltipContent>
    </Tooltip>
  );
}

/** 任务只在真实终态后回报完成；网络异常保留句柄，允许继续查询。 */
export function ImportMaterialsDialog({
  wid,
  onClose,
  onImported,
  names,
  initialMode = "inplace",
  initialSource = "",
  initialReport,
}: Props) {
  const [mode, setMode] = useState(initialMode);
  const [source, setSource] = useState(initialSource);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [task, setTask] = useState<TaskView | null>(null);
  const [report, setReport] = useState<ImportReport | null>(initialReport ?? null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(0);
  const [pollFailed, setPollFailed] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const sourceId = useId();
  const reportSource = useRef<string | null>(
    initialMode === "copy" ? initialSource : null,
  );
  const forcedName = useRef<string | undefined>(undefined);
  const submittingRef = useRef(false);
  const generation = useRef(0);
  const importedRef = useRef(onImported);
  importedRef.current = onImported;
  const active = submitting || task?.status === "running";
  const taskId = task?.id;
  const taskStatus = task?.status;

  useEffect(() => {
    generation.current += 1;
    return () => {
      generation.current += 1;
    };
  }, []);

  // checking 表示用户显式重查，进度变化不重新建立轮询。
  // biome-ignore lint/correctness/useExhaustiveDependencies: checking is the explicit retry trigger.
  useEffect(() => {
    if (!taskId || taskStatus !== "running") return;
    const id = taskId;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setPollFailed(false);
    async function poll() {
      try {
        const view = await api.getTask(id);
        if (!current) return;
        if (view.id !== id) throw new Error("任务响应与当前导入不一致");
        const incoming =
          view.status === "succeeded"
            ? mode === "restore"
              ? parseReimportReport(view.result)
              : parseImportReport(view.result)
            : null;
        setTask(view);
        if (view.status === "running") {
          timer = setTimeout(() => void poll(), 2000);
        } else {
          setCancelling(false);
          importedRef.current();
          if (incoming) {
            const name = forcedName.current;
            setReport((previous) => {
              if (!previous || !name) return incoming;
              return {
                ...previous,
                imported: [...new Set([...previous.imported, ...incoming.imported])],
                skipped_identical: [
                  ...new Set([
                    ...previous.skipped_identical,
                    ...incoming.skipped_identical,
                  ]),
                ],
                skipped_conflict: [
                  ...previous.skipped_conflict.filter((row) => row.name !== name),
                  ...incoming.skipped_conflict,
                ],
                skipped_duplicate: [
                  ...previous.skipped_duplicate.filter((row) => row.name !== name),
                  ...incoming.skipped_duplicate,
                ],
                rejected: [
                  ...previous.rejected.filter((row) => row.name !== name),
                  ...incoming.rejected,
                ],
              };
            });
          } else
            setError(
              view.error || (view.status === "cancelled" ? "导入已取消" : "导入失败"),
            );
        }
      } catch (reason) {
        if (!current) return;
        if (reason instanceof ApiError && reason.status === 404) {
          setTask(null);
          setCancelling(false);
          setPollFailed(false);
          setError("上次任务已丢失（服务重启过），可重新执行");
          importedRef.current();
          return;
        }
        setError(errorMessage(reason));
        setPollFailed(true);
      }
    }
    void poll();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [taskId, taskStatus, checking]);

  async function submit(forceName?: string) {
    if (active || submittingRef.current) return;
    submittingRef.current = true;
    const token = generation.current;
    setSubmitting(true);
    setError("");
    if (!forceName) setReport(null);
    forcedName.current = forceName;
    try {
      const requestSource = forceName
        ? reportSource.current
        : mode === "copy"
          ? source.trim()
          : null;
      reportSource.current = requestSource;
      const accepted =
        mode === "restore"
          ? forceName
            ? await api.reimportMaterials(wid, [forceName], [forceName])
            : await api.reimportMaterials(wid, names ?? [])
          : await api.importMaterials(wid, {
              source: requestSource,
              ...(forceName
                ? { names: [forceName], force_names: [forceName] }
                : names
                  ? { names }
                  : {}),
            });
      if (generation.current !== token) return;
      if (!accepted.task_id) throw new Error("导入任务编号无效");
      setTask({
        id: accepted.task_id,
        status: "running",
        progress: 0,
        result: null,
        error: null,
      });
    } catch (reason) {
      if (generation.current === token) setError(errorMessage(reason));
    } finally {
      submittingRef.current = false;
      if (generation.current === token) setSubmitting(false);
    }
  }

  async function cancel() {
    if (!task || cancelling) return;
    const token = generation.current;
    setCancelling(true);
    try {
      await api.cancelTask(task.id);
      if (generation.current === token) setChecking((value) => value + 1);
    } catch (reason) {
      if (generation.current === token) {
        setCancelling(false);
        setError(errorMessage(reason));
      }
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !active) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] max-w-xl overflow-y-auto">
        {pickerOpen && (
          <DirectoryPicker
            initialPath={source}
            onClose={() => setPickerOpen(false)}
            onSelect={(value) => {
              setSource(value);
              setPickerOpen(false);
            }}
          />
        )}
        <DialogHeader>
          <DialogTitle>
            {mode === "restore" ? "重新导入缺失素材" : "导入素材"}
          </DialogTitle>
          <DialogDescription>当前工作目录</DialogDescription>
        </DialogHeader>
        {names && (
          <ul className="max-h-64 overflow-auto text-t-sm">
            {names.map((name) => (
              <li key={name} className="break-all">
                {name}
              </li>
            ))}
          </ul>
        )}
        {initialMode !== "restore" && (
          <fieldset className="flex gap-4" disabled={active}>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="import-mode"
                checked={mode === "inplace"}
                onChange={() => setMode("inplace")}
              />
              就地补登记
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="import-mode"
                checked={mode === "copy"}
                onChange={() => setMode("copy")}
              />
              复制导入
            </label>
          </fieldset>
        )}
        {mode === "copy" && (
          <div className="space-y-2">
            <label htmlFor={sourceId}>来源目录</label>
            <div className="flex items-center gap-2">
              <Input
                id={sourceId}
                value={source}
                disabled={active}
                onChange={(event) => setSource(event.currentTarget.value)}
              />
              <Tip label="选择来源目录">
                <Button
                  type="button"
                  variant="outline"
                  size="icon-lg"
                  disabled={active}
                  aria-label="选择来源目录"
                  onClick={() => setPickerOpen(true)}
                >
                  <FolderIcon />
                </Button>
              </Tip>
            </div>
          </div>
        )}
        {error && <FormError className="text-t-sm text-bad-ink">{error}</FormError>}
        {task?.status === "running" && (
          <div role="status" className="space-y-2">
            <span>
              {cancelling ? "正在取消" : "正在导入"} · {Math.round(task.progress * 100)}
              %
            </span>
            <progress
              className="w-full"
              value={task.progress}
              max={1}
              aria-label="导入进度"
            />
            {pollFailed && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setError("");
                  setChecking((value) => value + 1);
                }}
              >
                重新查询
              </Button>
            )}
          </div>
        )}
        {report && (
          <div className="space-y-3 text-t-sm">
            <section className="overflow-hidden rounded-lg border border-ok-bd">
              <h3 className="bg-ok-bg px-4 py-3 text-t-md font-medium text-ok-ink">
                导入完成 · 新增 {report.imported.length} 项 · 同名同容跳过{" "}
                {report.skipped_identical.length} 项
              </h3>
              {report.skipped_identical.map((name) => (
                <p key={name} className="px-4 py-2 break-all">
                  {name} · 内容相同，已跳过
                </p>
              ))}
            </section>
            {!!report.skipped_conflict.length && (
              <section className="overflow-hidden rounded-lg border border-warn-bd">
                <h3 className="bg-warn-bg px-4 py-3 text-t-md font-medium text-warn-ink">
                  {report.skipped_conflict.length} 个同名文件内容不同，已跳过、未覆盖
                </h3>
                {report.skipped_conflict.map((row) => (
                  <div key={row.name} className="space-y-1 px-4 py-2 break-all">
                    <p className="font-medium">{row.name}</p>
                    <div className="flex flex-wrap gap-2 text-muted-foreground">
                      <span>
                        已登记{" "}
                        <ContentFingerprint
                          size={row.existing_size}
                          hash={row.existing_sha256}
                        />
                      </span>
                      <span>
                        本次{" "}
                        <ContentFingerprint
                          size={row.incoming_size}
                          hash={row.incoming_sha256}
                        />
                      </span>
                    </div>
                  </div>
                ))}
                <p className="px-4 pb-3 text-muted-foreground">
                  要保留本次文件，请先在来源目录改名，再导入。
                </p>
              </section>
            )}
            {!!report.skipped_duplicate.length && (
              <section className="overflow-hidden rounded-lg border border-warn-bd">
                <h3 className="bg-warn-bg px-4 py-3 text-t-md font-medium text-warn-ink">
                  {report.skipped_duplicate.length} 个异名文件内容相同，已跳过
                </h3>
                {report.skipped_duplicate.map((row) => (
                  <div
                    key={row.name}
                    className="flex flex-wrap items-center gap-2 px-4 py-2"
                  >
                    <span className="min-w-0 flex-1 break-all">
                      {row.name} · 与 {row.duplicate_of} 相同
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={active}
                      onClick={() => void submit(row.name)}
                    >
                      仍按新名导入
                    </Button>
                  </div>
                ))}
              </section>
            )}
            {report.rejected.map((row) => (
              <p key={row.name} className="break-all text-warn-ink">
                {row.name} · {row.reason}
              </p>
            ))}
          </div>
        )}
        <DialogFooter>
          {active ? (
            <Button
              variant="outline"
              disabled={submitting || cancelling}
              onClick={() => void cancel()}
            >
              取消导入
            </Button>
          ) : (
            <>
              <Button variant="ghost" onClick={onClose}>
                关闭
              </Button>
              <Button
                disabled={mode === "copy" && !source.trim()}
                onClick={() => void submit()}
              >
                导入
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
