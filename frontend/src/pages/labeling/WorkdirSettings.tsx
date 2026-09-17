import {
  ArrowLeftIcon,
  ChevronDownIcon,
  CopyIcon,
  EyeIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
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
import { BatchConfiguration } from "./BatchConfiguration";
import { CleanupDialog } from "./CleanupDialog";
import { DeleteWorkdirDialog } from "./DeleteWorkdirDialog";
import { ImportMaterialsDialog } from "./ImportMaterialsDialog";
import { NewStrategyDialog } from "./NewStrategyDialog";
import { RelocateWorkdirDialog } from "./RelocateWorkdirDialog";
import { SnapshotDialog } from "./SnapshotDialog";

type Batch = components["schemas"]["BatchView"];
type Directory = components["schemas"]["WorkdirInfo"];

function localDate(seconds: number): string {
  const date = new Date(seconds * 1000);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

export function WorkdirSettings({
  wid,
  onBack,
  onChanged,
  onDeleted,
}: {
  wid: string;
  onBack: () => void;
  onChanged: () => void;
  onDeleted?: () => void;
}) {
  const [directory, setDirectory] = useState<Directory | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [browse, setBrowse] = useState(false);
  const [importing, setImporting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [action, setAction] = useState<{
    batch: Batch;
    kind: "hide" | "delete" | "rename";
  } | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [copied, setCopied] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [cleanup, setCleanup] = useState<"products" | "runs" | null>(null);
  const [copyError, setCopyError] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [cleanupSummary, setCleanupSummary] = useState<{
    products: components["schemas"]["CleanupPreview"];
    runs: components["schemas"]["RunCleanupEntry"][];
  } | null>(null);
  const [summaryError, setSummaryError] = useState("");
  const [stats, setStats] = useState<components["schemas"]["WorkdirStatsView"] | null>(
    null,
  );
  const [statsError, setStatsError] = useState("");
  const [runStates, setRunStates] = useState<Record<string, boolean>>({});
  const [runError, setRunError] = useState("");
  const mounted = useRef(false);
  const actionPending = useRef(false);
  const cleanupPending = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setRunStates({});
    setRunError("");
    async function poll() {
      const next: Record<string, boolean> = {};
      let failure = "";
      await Promise.all(
        batches.map(async (batch) => {
          try {
            const state = await api.currentRun(wid, batch.id);
            next[batch.id] = state.status === "pending" || state.status === "running";
          } catch (reason) {
            if (reason instanceof ApiError && reason.status === 404)
              next[batch.id] = false;
            else failure = errorMessage(reason);
          }
        }),
      );
      if (disposed) return;
      setRunStates(next);
      setRunError(failure);
      timer = setTimeout(() => void poll(), 3000);
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [wid, batches]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision invalidates the maintenance summaries after mutations.
  useEffect(() => {
    let current = true;
    setCleanupSummary(null);
    setSummaryError("");
    void Promise.all([api.previewProductCleanup(wid), api.previewRunCleanup(wid)]).then(
      ([products, runs]) => {
        if (current) setCleanupSummary({ products, runs });
      },
      (reason: unknown) => {
        if (current) setSummaryError(errorMessage(reason));
      },
    );
    return () => {
      current = false;
    };
  }, [wid, revision]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision refreshes physical material statistics after mutations.
  useEffect(() => {
    let current = true;
    setStats(null);
    setStatsError("");
    void api.getWorkdirStats(wid).then(
      (value) => {
        if (current) setStats(value);
      },
      (reason: unknown) => {
        if (current) setStatsError(errorMessage(reason));
      },
    );
    return () => {
      current = false;
    };
  }, [wid, revision]);
  const [relocating, setRelocating] = useState<string | null>(null);
  const [relocations, setRelocations] = useState<
    components["schemas"]["WorkdirRelocationStatus"][]
  >([]);
  const [maintenanceError, setMaintenanceError] = useState("");
  const [cleaningOld, setCleaningOld] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision refreshes persistent relocation records after maintenance.
  useEffect(() => {
    let current = true;
    setMaintenanceError("");
    void api.relocationStatus(wid).then(
      (value) => {
        if (current) setRelocations(value);
      },
      (reason: unknown) => {
        if (current) setMaintenanceError(errorMessage(reason));
      },
    );
    return () => {
      current = false;
    };
  }, [wid, revision]);

  async function cleanOld(oldPath: string) {
    if (cleanupPending.current) return;
    cleanupPending.current = true;
    setCleaningOld(oldPath);
    setMaintenanceError("");
    try {
      const result = await api.retryRelocationCleanup(wid, oldPath);
      if (!mounted.current) return;
      if (result.cleanup_pending)
        setMaintenanceError(`旧位置仍未清理：${result.old_path}`);
      else refresh();
    } catch (reason) {
      if (mounted.current) setMaintenanceError(errorMessage(reason));
    } finally {
      cleanupPending.current = false;
      if (mounted.current) setCleaningOld(null);
    }
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision refreshes directory metadata after mutations.
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void Promise.all([api.getWorkdir(wid), api.listBatches(wid)])
      .then(
        ([entry, list]) => {
          if (current) {
            setDirectory(entry);
            setBatches(list);
          }
        },
        (reason: unknown) => {
          if (current) setError(errorMessage(reason));
        },
      )
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [wid, revision]);

  function refresh() {
    setRevision((value) => value + 1);
    onChanged();
  }

  function requestAction(batch: Batch, kind: "hide" | "delete" | "rename") {
    setAction({ batch, kind });
    setName(batch.name);
    setActionError("");
  }

  async function apply() {
    if (!action || actionPending.current || (action.kind === "rename" && !name.trim()))
      return;
    actionPending.current = true;
    setBusy(true);
    setActionError("");
    try {
      if (action.kind === "delete") await api.deleteBatch(wid, action.batch.id);
      else if (action.kind === "hide")
        await api.setBatchActive(wid, action.batch.id, !action.batch.active);
      else await api.updateBatch(wid, action.batch.id, { name: name.trim() });
      if (!mounted.current) return;
      setAction(null);
      refresh();
    } catch (reason) {
      if (mounted.current) setActionError(errorMessage(reason));
    } finally {
      actionPending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function copyPath() {
    if (!directory) return;
    setCopyError("");
    setCopied(false);
    try {
      await navigator.clipboard.writeText(directory.path);
      setCopied(true);
    } catch {
      setCopyError("复制失败，请检查浏览器剪贴板权限。");
    }
  }

  const cleanupBatches = new Set(
    cleanupSummary?.products.products.map((entry) => entry.batch),
  );
  const hiddenCleanupBatches = batches.filter(
    (batch) => !batch.active && cleanupBatches.has(batch.seq),
  ).length;
  const runDates = cleanupSummary?.runs.map((entry) => entry.modified_at) ?? [];
  const runDateRange = runDates.length
    ? `${localDate(Math.min(...runDates))} → ${localDate(Math.max(...runDates))}`
    : "";

  return (
    <section
      className="h-full overflow-y-auto px-6 pt-4 pb-6"
      aria-label="工作目录设置"
    >
      <header className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeftIcon />
          返回打标页
        </Button>
        <h1 className="text-t-xl font-medium">工作目录设置</h1>
        <Button
          className="ml-auto"
          variant="ghost"
          size="icon"
          aria-label="刷新工作目录"
          disabled={loading || busy}
          onClick={refresh}
        >
          <RefreshCwIcon />
        </Button>
      </header>
      {error && (
        <p role="alert" className="py-3 text-bad-ink">
          {error}
        </p>
      )}
      {loading && (
        <p role="status" className="py-3 text-text-3">
          正在读取工作目录
        </p>
      )}
      {!loading && directory && !error && (
        <>
          <section
            className="mt-4 border-b border-border bg-card p-4"
            aria-label="基本信息"
          >
            <h2 className="mb-2 text-t-md font-medium">基本信息</h2>
            <div className="flex flex-wrap items-center gap-3 py-2 text-t-sm">
              <span className="w-[76px] shrink-0 text-text-4">路径</span>
              <span className="min-w-0 flex-1 break-all">{directory.path}</span>
              <Button variant="ghost" size="sm" onClick={() => setBrowse(true)}>
                打开
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="w-(--h-sm) p-0"
                aria-label="复制路径"
                title="复制路径"
                onClick={() => void copyPath()}
              >
                <CopyIcon />
              </Button>
              {copied && <span role="status">已复制</span>}
              <Button variant="ghost" size="sm" onClick={() => setRelocating("")}>
                修改路径
              </Button>
            </div>
            {copyError && (
              <p role="alert" className="text-t-sm text-bad-ink">
                {copyError}
              </p>
            )}
            <div className="flex items-center gap-3 py-2 text-t-sm text-text-3">
              <span className="w-[76px] shrink-0 text-text-4">统计</span>
              <span>
                策略 {batches.length}（活跃{" "}
                {batches.filter((batch) => batch.active).length} · 已隐藏{" "}
                {batches.filter((batch) => !batch.active).length}）
                {stats && ` · 素材 ${stats.asset_count}`} · 产物{" "}
                {batches.reduce((total, batch) => total + batch.product_count, 0)}
                {stats && ` · ${(stats.asset_bytes / 1024 / 1024).toFixed(2)} MiB`}
              </span>
            </div>
            {statsError && (
              <p role="alert" className="text-t-sm text-bad-ink">
                {statsError}
              </p>
            )}
            <div className="flex items-center gap-3 py-2 text-t-sm">
              <span className="w-[76px] shrink-0 text-text-4">导入</span>
              <Button variant="ghost" size="sm" onClick={() => setImporting(true)}>
                导入素材
              </Button>
            </div>
          </section>
          <section
            className="mt-4 border-b border-border bg-card p-4"
            aria-label="清理"
          >
            <h2 className="mb-2 text-t-md font-medium">清理</h2>
            {summaryError && (
              <p role="alert" className="text-t-sm text-bad-ink">
                {summaryError}
              </p>
            )}
            {maintenanceError && (
              <p role="alert" className="break-all text-t-sm text-bad-ink">
                {maintenanceError}
              </p>
            )}
            <div className="flex items-center gap-3 py-2 text-t-sm">
              <span className="flex-1">清理无素材产物</span>
              {cleanupSummary && (
                <span className="text-text-3">
                  {cleanupSummary.products.products.length} 个 txt（
                  {cleanupBatches.size} 套策略，含 {hiddenCleanupBatches} 套已隐藏）·{" "}
                  {(cleanupSummary.products.total_bytes / 1024).toFixed(1)} KiB
                </span>
              )}
              <Button
                variant="ghost"
                size="sm"
                aria-label="查看无素材产物清单"
                onClick={() => setCleanup("products")}
              >
                查看清单
              </Button>
            </div>
            <div className="flex items-center gap-3 py-2 text-t-sm">
              <span className="flex-1">清理运行记录</span>
              {cleanupSummary && (
                <span className="text-text-3">
                  {cleanupSummary.runs.length} 份 ·{" "}
                  {(
                    cleanupSummary.runs.reduce(
                      (total, entry) => total + entry.size,
                      0,
                    ) / 1024
                  ).toFixed(1)}{" "}
                  KiB
                  {runDateRange && ` · ${runDateRange}`}
                </span>
              )}
              <Button
                variant="ghost"
                size="sm"
                aria-label="查看运行记录清单"
                onClick={() => setCleanup("runs")}
              >
                查看清单
              </Button>
            </div>
            {relocations.map((record) => (
              <div
                key={record.old_path}
                className="flex flex-wrap items-center gap-3 py-2 text-t-sm"
              >
                <span className="min-w-0 flex-1 break-all">
                  {record.status === "cleanup-pending"
                    ? `旧位置尚未清理：${record.old_path}`
                    : record.status === "copy-retained"
                      ? `搬迁副本保留：${record.path}`
                      : `工作目录位置已变化：${record.path}`}
                </span>
                {record.status === "cleanup-pending" && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={cleaningOld !== null}
                    onClick={() => void cleanOld(record.old_path)}
                  >
                    {cleaningOld === record.old_path ? "正在清理" : "重试清理旧位置"}
                  </Button>
                )}
                {record.status === "copy-retained" && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setRelocating(record.path)}
                  >
                    重试搬迁
                  </Button>
                )}
              </div>
            ))}
          </section>
          <section
            className="mt-4 border-b border-border bg-card p-4"
            aria-label="目录策略"
          >
            <div className="mb-2 flex items-center justify-between gap-3">
              <h2 className="text-t-md font-medium">策略（{batches.length}）</h2>
              <Button size="sm" onClick={() => setCreating(true)}>
                <PlusIcon />
                新增策略
              </Button>
            </div>
            {runError && (
              <p role="alert" className="pb-2 text-t-sm text-bad-ink">
                {runError}
              </p>
            )}
            {batches.length === 0 && (
              <p className="py-2 text-t-sm text-text-3">还没有策略</p>
            )}
            {batches.map((batch) => (
              <div key={batch.id} className="border-b border-border last:border-0">
                <div key={batch.id} className="flex flex-wrap items-center gap-3 py-3">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-(--h-sm) p-0"
                    aria-label={`展开策略配置 ${batch.id}`}
                    aria-expanded={expanded.has(batch.id)}
                    onClick={() =>
                      setExpanded((previous) => {
                        const next = new Set(previous);
                        if (next.has(batch.id)) next.delete(batch.id);
                        else next.add(batch.id);
                        return next;
                      })
                    }
                  >
                    <ChevronDownIcon
                      className={expanded.has(batch.id) ? "" : "-rotate-90"}
                    />
                  </Button>
                  <span
                    className={`text-t-sm ${runStates[batch.id] ? "text-info-ink" : batch.active ? "text-ok-ink" : "text-text-4"}`}
                  >
                    {runStates[batch.id] ? "运行中" : batch.active ? "活跃" : "已隐藏"}
                  </span>
                  {action?.kind === "rename" && action.batch.id === batch.id ? (
                    <Input
                      aria-label={`策略名称 ${batch.id}`}
                      className="h-(--h-sm) w-48 max-w-full"
                      value={name}
                      disabled={busy}
                      ref={(element) => element?.focus()}
                      onChange={(event) => setName(event.currentTarget.value)}
                      onBlur={() => {
                        if (name.trim()) void apply();
                      }}
                      onKeyDown={(event) => {
                        if (event.nativeEvent.isComposing) return;
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void apply();
                        }
                        if (event.key === "Escape" && !actionPending.current) {
                          event.preventDefault();
                          setAction(null);
                        }
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      className="min-w-0 break-all rounded-sm text-left font-medium hover:bg-accent"
                      aria-label={`改名策略 ${batch.name}`}
                      title="改名"
                      disabled={busy}
                      onClick={() => requestAction(batch, "rename")}
                    >
                      {batch.name}
                    </button>
                  )}
                  <span className="text-t-sm text-text-4">{batch.id}</span>
                  <span className="ml-auto text-t-sm text-text-3">
                    产物 {batch.product_count}
                    {stats && ` / ${stats.asset_count}`}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-(--h-sm) p-0"
                    aria-label={`查看快照 ${batch.id}`}
                    title="查看快照"
                    onClick={() => setSnapshot(batch.id)}
                  >
                    <EyeIcon />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => requestAction(batch, "hide")}
                  >
                    {batch.active ? "隐藏" : "显示"}
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    className="w-(--h-sm) p-0"
                    aria-label={`删除策略 ${batch.id}`}
                    disabled={runStates[batch.id] !== false || busy}
                    title={
                      runStates[batch.id]
                        ? "运行中不可删除"
                        : runStates[batch.id] === false
                          ? "删除"
                          : "正在确认运行状态"
                    }
                    onClick={() => requestAction(batch, "delete")}
                  >
                    <Trash2Icon />
                  </Button>
                </div>
                {action?.kind === "rename" &&
                  action.batch.id === batch.id &&
                  actionError && (
                    <p role="alert" className="pb-2 text-t-sm text-bad-ink">
                      {actionError}
                    </p>
                  )}
                {expanded.has(batch.id) && (
                  <BatchConfiguration wid={wid} batch={batch.id} />
                )}
              </div>
            ))}
          </section>
        </>
      )}
      {!loading && directory && !error && (
        <section
          className="mt-4 border-b border-border bg-card p-4"
          aria-label="危险操作"
        >
          <h2 className="mb-2 text-t-md font-medium">危险操作</h2>
          <Button
            variant="destructive-soft"
            size="sm"
            onClick={() => setDeleting(true)}
          >
            <Trash2Icon />
            删除工作目录
          </Button>
        </section>
      )}
      {deleting && (
        <DeleteWorkdirDialog
          wid={wid}
          onClose={() => setDeleting(false)}
          onDeleted={() => {
            setDeleting(false);
            if (onDeleted) onDeleted();
            else {
              onChanged();
              onBack();
            }
          }}
        />
      )}
      {creating && directory && (
        <NewStrategyDialog
          wid={wid}
          title={directory.title}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            refresh();
          }}
        />
      )}
      {relocating !== null && directory && (
        <RelocateWorkdirDialog
          wid={wid}
          source={directory.path}
          initialTarget={relocating}
          onClose={() => setRelocating(null)}
          onChanged={refresh}
        />
      )}
      {browse && directory && (
        <DirectoryPicker
          initialPath={directory.path}
          browseOnly
          onClose={() => setBrowse(false)}
          onSelect={() => {}}
        />
      )}
      {cleanup && (
        <CleanupDialog
          wid={wid}
          kind={cleanup}
          onClose={() => setCleanup(null)}
          onCleaned={refresh}
        />
      )}
      {importing && (
        <ImportMaterialsDialog
          wid={wid}
          onClose={() => setImporting(false)}
          onImported={refresh}
        />
      )}
      {snapshot && (
        <SnapshotDialog wid={wid} batch={snapshot} onClose={() => setSnapshot(null)} />
      )}
      {action && action.kind !== "rename" && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open && !busy) setAction(null);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {action.kind === "delete"
                  ? "删除策略？"
                  : action.batch.active
                    ? "隐藏策略？"
                    : "显示策略？"}
              </DialogTitle>
              <DialogDescription>
                {action.batch.name} · {action.batch.id}
              </DialogDescription>
            </DialogHeader>
            <p className="text-t-sm text-text-3">
              {action.kind === "delete"
                ? `将删除本策略的 ${action.batch.product_count} 个产物、快照和重试记录，素材保留。`
                : action.batch.active
                  ? "若此策略正在运行，确认后将停止本次运行；已完成条目与产物保留。隐藏后可在此处重新显示。"
                  : "重新显示后可在打标页选择此策略。"}
            </p>
            {actionError && (
              <p role="alert" className="text-bad-ink">
                {actionError}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" disabled={busy} onClick={() => setAction(null)}>
                取消
              </Button>
              <Button
                variant={action.kind === "delete" ? "destructive-fill" : "default"}
                disabled={busy}
                onClick={() => void apply()}
              >
                {busy ? "正在保存" : "确认"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </section>
  );
}
