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
import { Tip } from "../../components/ui/tooltip";
import { formatBytes } from "../../lib/format";
import { BatchConfiguration } from "./BatchConfiguration";
import { CleanupDialog } from "./CleanupDialog";
import { DeleteWorkdirDialog } from "./DeleteWorkdirDialog";
import { ImportMaterialsDialog } from "./ImportMaterialsDialog";
import { NewStrategyDialog } from "./NewStrategyDialog";
import { RelocateWorkdirDialog } from "./RelocateWorkdirDialog";
import { SnapshotDialog } from "./SnapshotDialog";

type Batch = components["schemas"]["BatchView"];
type Directory = components["schemas"]["WorkdirInfo"];

/** L15：稳定的 ref 回调——只在元素挂载时聚焦一次，重渲染不抢焦点。 */
function autoFocusRef(element: HTMLInputElement | null): void {
  element?.focus();
}

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
      let anyRunning = false;
      await Promise.all(
        batches.map(async (batch) => {
          try {
            const state = await api.currentRun(wid, batch.id);
            // 空闲 = 200 + null（L3 后端语义）：不是错误，照常记 false。
            next[batch.id] =
              state !== null &&
              (state.status === "pending" || state.status === "running");
            if (next[batch.id]) anyRunning = true;
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
      // L4/B9（2026-09-21 审计）：有运行才 3 秒紧轮询；全部空闲时降到 15 秒——
      // 空闲页面的高频全批次轮询是无谓请求，还把真错误淹在日志里。
      timer = setTimeout(() => void poll(), anyRunning ? 3000 : 15000);
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
  // Q7（2026-09-21 审计）：同一份运行时两端点相同 → 压缩成单日期，跨天才写区间。
  const runDateRange = runDates.length
    ? (() => {
        const min = localDate(Math.min(...runDates));
        const max = localDate(Math.max(...runDates));
        return min === max ? min : `${min} → ${max}`;
      })()
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
      {error && <FormError className="py-3 text-bad-ink">{error}</FormError>}
      {loading && (
        <p role="status" className="py-3 text-text-3">
          正在读取工作目录
        </p>
      )}
      {!loading && directory && !error && (
        <>
          <section
            className="mt-4 rounded-xl border border-border bg-card p-4"
            aria-label="基本信息"
          >
            <h2 className="mb-2 text-t-md font-medium">基本信息</h2>
            <div className="flex flex-wrap items-center gap-3 py-2 text-t-sm">
              <span className="w-[76px] shrink-0 text-text-4">路径</span>
              <span className="min-w-0 flex-1 break-all">{directory.path}</span>
              <Button variant="ghost" size="sm" onClick={() => setBrowse(true)}>
                打开
              </Button>
              <Tip label="复制路径">
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-(--h-sm) p-0"
                  aria-label="复制路径"
                  onClick={() => void copyPath()}
                >
                  <CopyIcon />
                </Button>
              </Tip>
              {copied && <span role="status">已复制</span>}
              <Button variant="ghost" size="sm" onClick={() => setRelocating("")}>
                修改路径
              </Button>
            </div>
            {copyError && (
              <FormError className="text-t-sm text-bad-ink">{copyError}</FormError>
            )}
            <div className="flex items-center gap-3 py-2 text-t-sm text-text-3">
              <span className="w-[76px] shrink-0 text-text-4">统计</span>
              <span>
                策略 {batches.length}（活跃{" "}
                {batches.filter((batch) => batch.active).length} · 已停用{" "}
                {batches.filter((batch) => !batch.active).length}）
                {stats && ` · 素材 ${stats.asset_count}`} · 产物{" "}
                {batches.reduce((total, batch) => total + batch.product_count, 0)}
                {stats && ` · ${formatBytes(stats.asset_bytes, "MiB", 2)}`}
              </span>
            </div>
            {statsError && (
              <FormError className="text-t-sm text-bad-ink">{statsError}</FormError>
            )}
            <div className="flex items-center gap-3 py-2 text-t-sm">
              <span className="w-[76px] shrink-0 text-text-4">导入</span>
              <Button variant="ghost" size="sm" onClick={() => setImporting(true)}>
                导入素材
              </Button>
            </div>
          </section>
          <section
            className="mt-4 rounded-xl border border-border bg-card p-4"
            aria-label="清理"
          >
            <h2 className="mb-2 text-t-md font-medium">清理</h2>
            {summaryError && (
              <FormError className="text-t-sm text-bad-ink">{summaryError}</FormError>
            )}
            {maintenanceError && (
              <FormError className="break-all text-t-sm text-bad-ink">
                {maintenanceError}
              </FormError>
            )}
            <div className="flex items-center gap-3 py-2 text-t-sm">
              <span className="flex-1">清理无素材产物</span>
              {cleanupSummary && (
                <span className="text-text-3">
                  {cleanupSummary.products.products.length} 个 txt（
                  {cleanupBatches.size} 套策略，含 {hiddenCleanupBatches} 套已停用）·{" "}
                  {formatBytes(cleanupSummary.products.total_bytes, "KiB", 1)}
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
                  {formatBytes(
                    cleanupSummary.runs.reduce((total, entry) => total + entry.size, 0),
                    "KiB",
                    1,
                  )}
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
            className="mt-4 rounded-xl border border-border bg-card p-4"
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
              <FormError className="pb-2 text-t-sm text-bad-ink">{runError}</FormError>
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
                    className={`inline-flex h-4.5 shrink-0 items-center rounded-full px-2 text-t-xs font-medium ${runStates[batch.id] ? "bg-info-ink text-on-ink" : batch.active ? "bg-ok-ink text-on-ink" : "bg-muted text-text-3"}`}
                  >
                    {/* Q3/Q7（ui-spec §5.2）：状态章一律实底彩色 + 白字——
                        此前的带色文字是与原型 shell.css 同源的规范漂移。 */}
                    {runStates[batch.id] ? "运行中" : batch.active ? "活跃" : "已停用"}
                  </span>
                  {action?.kind === "rename" && action.batch.id === batch.id ? (
                    <Input
                      aria-label={`策略名称 ${batch.id}`}
                      className="h-(--h-sm) w-48 max-w-full"
                      value={name}
                      disabled={busy}
                      // L15（2026-09-21 审计）：稳定的 ref 回调只在挂载时聚焦一次——
                      // 内联箭头每次渲染重建会把光标反复拉回输入框。
                      ref={autoFocusRef}
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
                    <Tip label="改名">
                      <button
                        type="button"
                        className="min-w-0 break-all rounded-sm text-left font-medium hover:bg-accent"
                        aria-label={`改名策略 ${batch.name}`}
                        disabled={busy}
                        onClick={() => requestAction(batch, "rename")}
                      >
                        {batch.name}
                      </button>
                    </Tip>
                  )}
                  <span className="text-t-sm text-text-4">{batch.id}</span>
                  <span className="ml-auto text-t-sm text-text-3">
                    产物 {batch.product_count}
                    {stats && ` / ${stats.asset_count}`}
                  </span>
                  <Tip label="查看快照">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-(--h-sm) p-0"
                      aria-label={`查看快照 ${batch.id}`}
                      onClick={() => setSnapshot(batch.id)}
                    >
                      <EyeIcon />
                    </Button>
                  </Tip>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => requestAction(batch, "hide")}
                  >
                    {/* Q3：同一动作统一为「停用 / 启用」（PRD F3/F9 与后端 docstring 同口径），
                        不再与「隐藏 / 显示」三种写法并存。 */}
                    {batch.active ? "停用" : "启用"}
                  </Button>
                  <Tip
                    label={
                      runStates[batch.id]
                        ? "运行中不可删除"
                        : runStates[batch.id] === false
                          ? ""
                          : "正在确认运行状态"
                    }
                  >
                    <Button
                      variant="destructive"
                      size="sm"
                      className="w-(--h-sm) p-0"
                      aria-label={`删除策略 ${batch.id}`}
                      disabled={runStates[batch.id] !== false || busy}
                      onClick={() => requestAction(batch, "delete")}
                    >
                      <Trash2Icon />
                    </Button>
                  </Tip>
                </div>
                {action?.kind === "rename" &&
                  action.batch.id === batch.id &&
                  actionError && (
                    <FormError className="pb-2 text-t-sm text-bad-ink">
                      {actionError}
                    </FormError>
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
          className="mt-4 rounded-xl border border-border bg-card p-4"
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
          nextSeq={
            batches.length ? Math.max(...batches.map((entry) => entry.seq)) + 1 : 1
          }
          existingCount={batches.length}
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
                    ? "停用策略？"
                    : "启用策略？"}
              </DialogTitle>
              <DialogDescription>
                {action.batch.name} · {action.batch.id}
              </DialogDescription>
            </DialogHeader>
            <p className="text-t-sm text-text-3">
              {action.kind === "delete"
                ? `将删除本策略的 ${action.batch.product_count} 个产物、快照和重试记录，素材保留。`
                : action.batch.active
                  ? "若此策略正在运行，确认后将停止本次运行；已完成条目与产物保留。停用后可在此处重新启用。"
                  : "重新启用后可在打标页选择此策略。"}
            </p>
            {actionError && (
              <FormError className="text-bad-ink">{actionError}</FormError>
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
