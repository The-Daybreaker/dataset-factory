import {
  ArrowLeftIcon,
  ChevronDownIcon,
  FileIcon,
  FileImageIcon,
  FilmIcon,
  Maximize2Icon,
  Minimize2Icon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";
import { Tip } from "../../components/ui/tooltip";
import { usePersistedState } from "../../hooks/use-persisted-state";
import { formatBytesAuto } from "../../lib/format";
import {
  LABELING_QUERY_KEY,
  LABELING_SELECTED_ITEM_KEY,
  readStoredString,
  writeStoredJson,
} from "../../lib/ui-storage";
import { BatchConfiguration } from "./BatchConfiguration";
import { BatchOverview } from "./BatchOverview";
import {
  type BatchSelection,
  BatchSelector,
  type WorkdirBatches,
} from "./BatchSelector";
import { CaptionPreview } from "./CaptionPreview";
import { ImportMaterialsDialog } from "./ImportMaterialsDialog";
import {
  groupedItems,
  ITEM_GROUPS,
  type ItemMap,
  itemKey,
  itemsFromGroups,
  withItemUpdate,
  withRetryItems,
} from "./items-state";
import { NewBatchForm } from "./NewBatchForm";
import { NewStrategyDialog } from "./NewStrategyDialog";
import { RemoveUnimportedDialog } from "./RemoveUnimportedDialog";
import { RunControl } from "./RunControl";
import { WorkdirSettings } from "./WorkdirSettings";

type ItemRow = components["schemas"]["ItemRowView"];

/** localStorage 里恢复的 selection 要过形状检查：脏 JSON 不许流进取数链路。 */
function isBatchSelection(value: unknown): value is BatchSelection {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as BatchSelection).workdirId === "string" &&
    typeof (value as BatchSelection).batchId === "string"
  );
}

async function loadWorkdirBatches(): Promise<WorkdirBatches[]> {
  const directories = await api.listWorkdirs();
  return Promise.all(
    directories.map(async (entry) => {
      try {
        return {
          id: entry.id,
          title: entry.title,
          path: entry.path,
          batches: await api.listBatches(entry.id),
        };
      } catch (reason) {
        return {
          id: entry.id,
          title: entry.title,
          path: entry.path,
          batches: [],
          error: errorMessage(reason),
        };
      }
    }),
  );
}

const MaterialRow = memo(function MaterialRow({
  row,
  selected,
  onSelect,
  selectionMode,
  checked,
  onCheck,
  retryGroup,
  saving,
  onRemoveRetry,
  onRecover,
  onRemoveUnimported,
}: {
  row: ItemRow;
  selected: boolean;
  onSelect: (row: ItemRow) => void;
  selectionMode: boolean;
  checked: boolean;
  onCheck: (item: string) => void;
  retryGroup: boolean;
  saving: boolean;
  onRemoveRetry: (item: string) => void;
  onRecover: (row: ItemRow) => void;
  onRemoveUnimported: (row: ItemRow) => void;
}) {
  return (
    <div className="group flex items-center gap-2 rounded-md px-2 hover:bg-accent">
      {selectionMode &&
        !retryGroup &&
        (row.status === "done" || row.status === "failed") && (
          <input
            type="checkbox"
            className="cb"
            aria-label={`选择 ${row.name}`}
            checked={checked}
            disabled={!row.can_retry || row.in_retry || saving}
            onChange={() => onCheck(row.item)}
          />
        )}
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(row)}
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-md py-2 text-left text-t-md ${selected ? "bg-primary/10" : ""} ${row.status === "missing" ? "opacity-[0.62]" : ""}`}
      >
        <span className="flex h-[27px] w-9 shrink-0 items-center justify-center rounded-sm border border-border bg-muted/60 text-text-3">
          {row.media === "video" ? (
            <FilmIcon className="size-3.5" />
          ) : row.media === "image" ? (
            <FileImageIcon className="size-3.5" />
          ) : (
            <FileIcon className="size-3.5" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate">{row.name}</span>
          {(row.message || row.reason) && (
            <span className="block break-words text-t-xs text-text-3">
              {row.message || row.reason}
              {/* V2（2026-09-21 审计）：契约里一直带着 size / limit——超出大小上限
                  这类拒绝要让用户看到具体数值（原型口径：412 MiB ＞ 100 MiB）。 */}
              {row.status === "unimported" &&
                row.size !== null &&
                row.size !== undefined &&
                row.limit !== null &&
                row.limit !== undefined &&
                ` · ${formatBytesAuto(row.size)} ＞ ${formatBytesAuto(row.limit)}`}
            </span>
          )}
          {row.in_retry && (
            <span className="text-t-xs text-muted-foreground">已排重试</span>
          )}
          {row.status === "missing" && !row.recoverable && (
            <span className="block text-t-xs text-text-3">原始来源不可用</span>
          )}
        </span>
      </button>
      {row.status === "missing" && (
        <Tip
          label={
            row.recoverable ? `来源：${row.source}` : "原始来源不可用，请从别处导入"
          }
        >
          <Button
            variant="ghost"
            size="mini"
            className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
            onClick={() => onRecover(row)}
          >
            {row.recoverable ? "重新导入" : "从别处导入"}
          </Button>
        </Tip>
      )}
      {row.status === "unimported" && (
        <Tip
          label={row.reason === "未登记" ? "导入此文件" : (row.reason ?? "不能导入")}
        >
          <Button
            variant="ghost"
            size="mini"
            disabled={row.reason !== "未登记"}
            onClick={() => onRecover(row)}
          >
            导入
          </Button>
        </Tip>
      )}
      {retryGroup && (
        <Tip label="移出重试列表">
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={saving}
            aria-label={`移出重试 ${row.name}`}
            onClick={() => onRemoveRetry(row.item)}
          >
            <XIcon />
          </Button>
        </Tip>
      )}
      {row.status === "unimported" && (
        <Tip label="删除">
          <Button
            variant="destructive"
            size="icon-xs"
            className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
            aria-label={`删除未导入 ${row.name}`}
            onClick={() => onRemoveUnimported(row)}
          >
            <Trash2Icon />
          </Button>
        </Tip>
      )}
    </div>
  );
});

/** 目录与批次切换以成对身份取数，过期请求不能覆盖新选择。 */
export function LabelingPage({
  onNavigateToSettings,
  onOpenWorkbench,
}: {
  /** 跳应用级设置页（V16：新建跑批的三个下拉要有「去设置」的出口）。 */
  onNavigateToSettings?: () => void;
  /** 跳对话工作台（V16：「拿不准效果？先试标」动线）。 */
  onOpenWorkbench?: () => void;
} = {}) {
  const [workdirs, setWorkdirs] = useState<WorkdirBatches[]>([]);
  // selection 与左列的折叠 / 组内展开状态落 localStorage：三期起页面用 Activity
  // 保活（切页不再卸载），这里的职责是活过「整页刷新 / 应用重启」。恢复的旧批次
  // 若已被删 / 停用，由 loadWorkdirBatches 完成后的既有校验回退默认选择，无需新增分支。
  const [selection, setSelection] = usePersistedState<BatchSelection | null>(
    "dsf-labeling-selection",
    null,
    isBatchSelection,
  );
  const [items, setItems] = useState<ItemMap>(new Map());
  const [exportRevision, setExportRevision] = useState(0);
  const [externalRun, setExternalRun] = useState<{
    identity: string;
    id: string;
  } | null>(null);
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  // 选中素材跨重启恢复（三期）：只在启动后的首次装载生效一次，换批次 / 换条目的
  // 既有清空语义不变。素材 id 批次内有效，恢复时对装载结果校验，对不上就放弃。
  const restoredItemRef = useRef<string | null>(
    readStoredString(LABELING_SELECTED_ITEM_KEY),
  );
  const followRun = useRef(true);
  const [foldedItem, setFoldedItem] = useState<string | null>(null);
  /** 预览舞台 HUD 的媒体元信息：从加载后的媒体元素读取，无需额外接口。 */
  const [mediaMeta, setMediaMeta] = useState<{
    w: number;
    h: number;
    duration?: number;
  } | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [retryRequest, setRetryRequest] = useState(0);
  const [batchRunState, setBatchRunState] = useState<string | null>(null);
  // 状态章重读令牌：RunControl 的空闲上报（挂载探测 / 15 秒慢轮）让它递增，
  // 触发下面的 latestRun 重读——「空闲」的正确语义是回读磁盘终态，不是抹空。
  const [runStateRevision, setRunStateRevision] = useState(0);
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [removal, setRemoval] = useState<{ identity: string; name: string } | null>(
    null,
  );
  const [recovery, setRecovery] = useState<{
    names: string[];
    mode: "copy" | "restore" | "inplace";
  } | null>(null);
  const [creating, setCreating] = useState(false);
  const [settingsWid, setSettingsWid] = useState<string | null>(null);
  const [newStrategyWid, setNewStrategyWid] = useState<string | null>(null);
  const [directoriesRevision, setDirectoriesRevision] = useState(0);
  // 左列筛选词跨重启持久化（三期）：搜索到一半重启，回来还在。
  const [query, setQuery] = usePersistedState<string>(LABELING_QUERY_KEY, "");
  const [collapsed, setCollapsed] = usePersistedState<ReadonlySet<string>>(
    "dsf-labeling-collapsed",
    new Set(),
  );
  // V3（2026-09-21 审计）：组内默认只展示 4 行，点开才全量——与分组折叠（collapsed）
  // 是两个独立维度：collapsed 管「整个组收不收」，expanded 管「组内截断放不放开」。
  const [expandedGroups, setExpandedGroups] = usePersistedState<ReadonlySet<string>>(
    "dsf-labeling-expanded-groups",
    new Set(),
  );
  // L2：跑批中点条目会停跟随——给可见提示 + 「继续跟随」钮，不再静默停。
  const [followPaused, setFollowPaused] = useState(false);
  // V15 + A2：跑批中的「当前产出」逐字呈现（思考只展示不落盘，关掉页面即没）。
  const [liveOutput, setLiveOutput] = useState<{
    item: string;
    reasoning: string;
    content: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const identity = `${selection?.workdirId}/${selection?.batchId}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const mounted = useRef(false);
  const refreshVersion = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const selected = selectedItem ? (items.get(selectedItem) ?? null) : null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: 换条目（identity 或选中项）即丢上一条的媒体元信息，等新媒体加载时重新读取。
  useEffect(() => {
    setMediaMeta(null);
  }, [identity, selectedItem]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: directoriesRevision refreshes the registry after directory settings mutations.
  useEffect(() => {
    let current = true;
    void loadWorkdirBatches()
      .then((loaded) => {
        if (!current) return;
        setWorkdirs(loaded);
        const directory = loaded.find((entry) =>
          entry.batches.some((batch) => batch.active),
        );
        const batch = directory?.batches.find((entry) => entry.active);
        setSelection((previous) => {
          if (
            previous &&
            loaded.some(
              (entry) =>
                entry.id === previous.workdirId &&
                entry.batches.some(
                  (candidate) => candidate.id === previous.batchId && candidate.active,
                ),
            )
          )
            return previous;
          return directory && batch
            ? { workdirId: directory.id, batchId: batch.id }
            : null;
        });
      })
      .catch((reason: unknown) => {
        if (current) setError(errorMessage(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [directoriesRevision]);

  useEffect(() => {
    if (!selection) return;
    let current = true;
    setLoading(true);
    setError("");
    setItems(new Map());
    setSelectedItem(null);
    followRun.current = true;
    setFollowPaused(false);
    setLiveOutput(null);
    setChecked(new Set());
    setSelectionMode(false);
    setRetryRequest(0);
    setSaving(false);
    setRecovery(null);
    setRemoval(null);
    void api
      .listItems(selection.workdirId, selection.batchId)
      .then((view) => {
        if (!current) return;
        const loaded = itemsFromGroups(view.groups);
        setItems(loaded);
        // 跨重启恢复选中素材：只在首次装载生效一次；对装载结果校验不过就放弃。
        const wanted = restoredItemRef.current;
        if (wanted !== null) {
          restoredItemRef.current = null;
          if (loaded.has(wanted)) setSelectedItem(wanted);
        }
      })
      .catch((reason: unknown) => {
        if (current) setError(errorMessage(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [selection]);

  const filtered = useMemo(() => groupedItems(items, query), [items, query]);
  // L11：搜索计数要能看出「一共多少条」——无过滤分组是总数基准。
  const unfiltered = useMemo(() => groupedItems(items, ""), [items]);

  // 顶栏状态章的存量事实：进页面 / 换批次时回读磁盘上本批次最近一次运行的终态。
  // 运行中的状态不在这里轮询——由 RunControl 受理即报、SSE 终态也报（见其
  // onRunStatus），避免两处各轮一份。
  // biome-ignore lint/correctness/useExhaustiveDependencies: runStateRevision is a deliberate re-read token bumped by RunControl's idle reports.
  useEffect(() => {
    if (!selection) {
      setBatchRunState(null);
      return;
    }
    let current = true;
    api
      .latestRun(selection.workdirId, selection.batchId)
      .then((history) => {
        if (current) setBatchRunState(history.record?.status ?? null);
      })
      .catch(() => {
        // 摘要读不到（批次刚被删、历史被清）不影响主流程：章留空，主区自己会报错。
      });
    return () => {
      current = false;
    };
  }, [selection, runStateRevision]);
  const choose = useCallback((row: ItemRow) => {
    if (followRun.current) {
      // 从「跟随」切到「手动看」：跑批中这不是静默的——出可见提示（L2）。
      setFollowPaused(true);
    }
    followRun.current = false;
    setSelectedItem(itemKey(row));
  }, []);
  // 选中素材落盘（跨重启恢复的写入侧）：换批次 / 换条目的清空也如实记 null。
  useEffect(() => {
    writeStoredJson(LABELING_SELECTED_ITEM_KEY, selectedItem);
  }, [selectedItem]);
  const recover = useCallback((row: ItemRow) => {
    setRecovery({
      names: [row.name],
      mode:
        row.status === "unimported" ? "inplace" : row.recoverable ? "restore" : "copy",
    });
  }, []);
  const requestRemoval = useCallback(
    (row: ItemRow) => {
      setRemoval({ identity, name: row.name });
    },
    [identity],
  );
  const toggleCheck = useCallback((item: string) => {
    setChecked((previous) => {
      const next = new Set(previous);
      if (next.has(item)) next.delete(item);
      else next.add(item);
      return next;
    });
  }, []);
  const retryChanged = useCallback(
    (retry: string[]) => {
      if (mounted.current && identityRef.current === identity) {
        setItems((previous) => withRetryItems(previous, retry));
      }
    },
    [identity],
  );
  const refreshItems = useCallback(
    async (requireSuccess = false) => {
      if (!selection) return;
      const version = ++refreshVersion.current;
      try {
        const view = await api.listItems(selection.workdirId, selection.batchId);
        if (
          !mounted.current ||
          identityRef.current !== identity ||
          version !== refreshVersion.current
        )
          return;
        setItems(itemsFromGroups(view.groups));
        setExportRevision((value) => value + 1);
        setError("");
      } catch (reason) {
        if (
          mounted.current &&
          identityRef.current === identity &&
          version === refreshVersion.current
        )
          setError(errorMessage(reason));
        if (requireSuccess) throw reason;
      }
    },
    [selection, identity],
  );
  const removeRetry = useCallback(
    async (item?: string) => {
      if (!selection || saving) return;
      setSaving(true);
      try {
        const result = item
          ? await api.removeRetryItem(selection.workdirId, selection.batchId, item)
          : await api.clearRetryItems(selection.workdirId, selection.batchId);
        if (!mounted.current || identityRef.current !== identity) return;
        retryChanged(result.items);
        setError("");
      } catch (reason) {
        if (mounted.current && identityRef.current === identity)
          setError(errorMessage(reason));
      } finally {
        if (mounted.current && identityRef.current === identity) setSaving(false);
      }
    },
    [selection, saving, identity, retryChanged],
  );
  async function addSelected() {
    if (!selection || saving || !checked.size) return;
    setSaving(true);
    setError("");
    try {
      const result = await api.addRetryItems(selection.workdirId, selection.batchId, [
        ...checked,
      ]);
      if (!mounted.current || identityRef.current !== identity) return;
      retryChanged(result.items);
      setChecked(new Set());
    } catch (reason) {
      if (mounted.current && identityRef.current === identity)
        setError(errorMessage(reason));
    } finally {
      if (mounted.current && identityRef.current === identity) setSaving(false);
    }
  }
  const asset =
    selected && selection
      ? `/api/workdirs/${encodeURIComponent(selection.workdirId)}/items/${encodeURIComponent(selected.item)}/asset`
      : null;

  if (settingsWid)
    return (
      <WorkdirSettings
        key={settingsWid}
        wid={settingsWid}
        onBack={() => {
          setSettingsWid(null);
          void refreshItems();
        }}
        onChanged={() => setDirectoriesRevision((value) => value + 1)}
        onDeleted={() => {
          setSettingsWid(null);
          setDirectoriesRevision((value) => value + 1);
        }}
      />
    );

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="打标">
      {newStrategyWid && (
        <NewStrategyDialog
          key={newStrategyWid}
          wid={newStrategyWid}
          title={
            workdirs.find((entry) => entry.id === newStrategyWid)?.title ??
            newStrategyWid
          }
          nextSeq={(() => {
            const batches =
              workdirs.find((entry) => entry.id === newStrategyWid)?.batches ?? [];
            return batches.length
              ? Math.max(...batches.map((entry) => entry.seq)) + 1
              : 1;
          })()}
          existingCount={
            workdirs.find((entry) => entry.id === newStrategyWid)?.batches.length ?? 0
          }
          onClose={() => setNewStrategyWid(null)}
          onCreated={(batch) => {
            setWorkdirs((previous) =>
              previous.map((entry) =>
                entry.id === newStrategyWid
                  ? { ...entry, batches: [...entry.batches, batch] }
                  : entry,
              ),
            );
            setSelection({ workdirId: newStrategyWid, batchId: batch.id });
            setNewStrategyWid(null);
            setDirectoriesRevision((value) => value + 1);
          }}
        />
      )}
      {selection && removal?.identity === identity && (
        <RemoveUnimportedDialog
          key={`${identity}/${removal.name}`}
          wid={selection.workdirId}
          name={removal.name}
          onClose={() => setRemoval(null)}
          onRemoved={() => void refreshItems()}
        />
      )}
      {selection && recovery && (
        <ImportMaterialsDialog
          key={`recover/${identity}`}
          wid={selection.workdirId}
          names={recovery.names}
          initialMode={recovery.mode}
          onClose={() => setRecovery(null)}
          onImported={() => void refreshItems()}
        />
      )}
      {selection && importOpen && (
        <ImportMaterialsDialog
          key={identity}
          wid={selection.workdirId}
          onClose={() => setImportOpen(false)}
          onImported={() => void refreshItems()}
        />
      )}
      <header className="flex shrink-0 flex-wrap items-center gap-3 px-4 pt-4 pb-3 lg:flex-nowrap lg:gap-4 lg:px-6">
        <div className="flex w-full min-w-0 shrink-0 items-center gap-3 lg:w-(--w-col-left) lg:pr-3">
          <div className="min-w-0 flex-1">
            <BatchSelector
              workdirs={workdirs}
              value={selection}
              onChange={setSelection}
              onSettings={setSettingsWid}
              onNewStrategy={setNewStrategyWid}
              runState={batchRunState}
            />
          </div>
          <Button
            variant="ghost"
            size="icon-lg"
            aria-label="新建跑批"
            onClick={() => setCreating(true)}
          >
            <PlusIcon />
          </Button>
        </div>
        {selection && (
          <BatchConfiguration
            key={`config/${identity}`}
            wid={selection.workdirId}
            batch={selection.batchId}
            compact
          />
        )}
        {selection && (
          <RunControl
            key={`${selection.workdirId}/${selection.batchId}`}
            wid={selection.workdirId}
            batch={selection.batchId}
            externalRunId={
              externalRun?.identity === identity ? externalRun.id : undefined
            }
            retryRequest={retryRequest}
            onRunStatus={(status) => {
              // "idle" = RunControl 探测到空闲（V15 连带的哨兵）：不抹状态章，改触发
              // latestRun 重读——抹空会把刚从磁盘读到的「已完成」等终态一并抹掉
              //（挂载探测与 15 秒慢轮每次空闲都会报一次 idle，徽标最多活 15 秒）；
              // 回读拿到的就是真实终态，V15 要防的「卡 running」照样被复位，语义更准。
              if (status === "idle") setRunStateRevision((value) => value + 1);
              else setBatchRunState(status);
              // 终态 = 跟随提示与「当前产出」都收场（运行结束后回到普通概览）。
              if (status !== "running" && status !== "pending") {
                setFollowPaused(false);
                setLiveOutput(null);
              }
            }}
            onFinish={() => refreshItems(true)}
            onCurrentItem={(item) => {
              if (followRun.current) setSelectedItem(item);
            }}
            onItemUpdate={(event) => {
              refreshVersion.current += 1;
              if (event.status === "started") {
                setLiveOutput({ item: event.item, reasoning: "", content: "" });
              }
              setItems((previous) => withItemUpdate(previous, event));
            }}
            onItemDelta={(event) => {
              setLiveOutput((current) =>
                current === null || current.item !== event.item
                  ? current
                  : event.delta === "reasoning"
                    ? { ...current, reasoning: current.reasoning + event.text }
                    : { ...current, content: current.content + event.text },
              );
            }}
            onImport={() => setImportOpen(true)}
          />
        )}
        <Button
          variant="ghost"
          size="icon"
          aria-label="刷新条目"
          disabled={!selection || loading}
          onClick={() => void refreshItems()}
        >
          <RefreshCwIcon />
        </Button>
      </header>
      {error && (
        <FormError className="mx-6 mb-3 text-t-sm text-bad-ink">{error}</FormError>
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-4 pb-6 lg:flex-row lg:overflow-hidden lg:px-6">
        <aside
          className="flex max-h-80 w-full shrink-0 flex-col overflow-hidden rounded-xl border border-border bg-card lg:max-h-none lg:w-(--w-col-left)"
          aria-label="素材条目"
        >
          <div className="flex min-h-10 items-center gap-2 px-3 py-2 text-t-sm">
            <span className="mr-auto text-t-md font-medium">条目</span>
            {selectionMode && (
              <>
                <span className="shrink-0 tabular-nums">已选 {checked.size}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={saving || !checked.size}
                  onClick={() => setChecked(new Set())}
                >
                  清空选择
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={saving || !checked.size}
                  onClick={() => void addSelected()}
                >
                  加入重试
                </Button>
              </>
            )}
            <Button
              variant="ghost"
              size="sm"
              disabled={saving || !selection || loading}
              onClick={() => {
                setSelectionMode((value) => !value);
                setChecked(new Set());
              }}
            >
              {selectionMode ? "退出选择" : "选择"}
            </Button>
          </div>
          <div className="mr-3 mb-2 ml-4 flex h-(--h-sm) shrink-0 items-center gap-2 rounded-md border border-input bg-card px-2">
            <SearchIcon className="size-3 shrink-0 text-text-3" />
            <input
              className="min-w-0 flex-1 border-0 bg-transparent text-t-sm text-text-2 outline-none placeholder:text-n-400"
              aria-label="搜索条目"
              placeholder="搜索条目"
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-auto" aria-busy={loading}>
            {loading && <p className="p-3 text-t-sm text-muted-foreground">正在加载</p>}
            {!loading && !selection && (
              <p className="p-3 text-t-sm text-muted-foreground">还没有可用批次</p>
            )}
            {selection &&
              ITEM_GROUPS.map(([key, label]) => (
                <section key={key} aria-label={label}>
                  <div
                    className={`sticky top-0 z-10 flex items-center gap-2 border-b border-border/60 border-l-[3px] bg-card px-4 py-2 text-t-md font-medium ${key === "queued" ? "border-l-primary" : key === "done" ? "border-l-ok-ink" : key === "failed" ? "border-l-bad-ink" : key === "retry" ? "border-l-info-ink" : "border-l-n-400"}`}
                  >
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      aria-expanded={!!query || !collapsed.has(key)}
                      onClick={() =>
                        setCollapsed((previous) => {
                          const next = new Set(previous);
                          if (next.has(key)) next.delete(key);
                          else next.add(key);
                          return next;
                        })
                      }
                    >
                      <ChevronDownIcon
                        className={`size-3.5 shrink-0 text-text-3 ${!query && collapsed.has(key) ? "-rotate-90" : ""}`}
                      />
                      {label}
                      <span
                        className={`text-t-xs tabular-nums ${key === "queued" ? "text-primary" : key === "done" ? "text-ok-ink" : key === "failed" ? "text-bad-ink" : key === "retry" ? "text-info-ink" : "text-text-4"}`}
                      >
                        {/* L11：搜索时给「命中 / 共 N」双口径，别让「剩 3 条」被读成「一共 3 条」。 */}
                        {query.trim() !== ""
                          ? `命中 ${filtered[key]?.length ?? 0} / 共 ${unfiltered[key]?.length ?? 0}`
                          : (filtered[key]?.length ?? 0)}
                      </span>
                    </button>
                    {key === "retry" && !!filtered.retry?.length && (
                      <>
                        <Button
                          variant="ghost"
                          size="mini"
                          className="shrink-0"
                          disabled={saving}
                          onClick={() => void removeRetry()}
                        >
                          清空名单
                        </Button>
                        <Tip label="冻结本轮名单发车：名单里的条目转入排队中并打「重打」标记">
                          <Button
                            variant="accent"
                            size="xs"
                            className="shrink-0"
                            disabled={saving}
                            onClick={() => setRetryRequest((value) => value + 1)}
                          >
                            {`开始重试（${filtered.retry?.length ?? 0}）`}
                          </Button>
                        </Tip>
                      </>
                    )}
                    {key === "missing" && !!filtered.missing?.length && (
                      <Tip label="把当前清单中可从来源找回的缺失素材重新导入">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="shrink-0"
                          disabled={!filtered.missing.some((row) => row.recoverable)}
                          onClick={() =>
                            setRecovery({
                              names: (filtered.missing ?? [])
                                .filter((row) => row.recoverable)
                                .map((row) => row.name),
                              mode: "restore",
                            })
                          }
                        >
                          一键导入
                        </Button>
                      </Tip>
                    )}
                    {key === "unimported" && !!filtered.unimported?.length && (
                      <Tip label="导入当前清单中符合格式与大小限制的文件">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="shrink-0"
                          disabled={
                            !filtered.unimported.some((row) => row.reason === "未登记")
                          }
                          onClick={() =>
                            setRecovery({
                              names: (filtered.unimported ?? [])
                                .filter((row) => row.reason === "未登记")
                                .map((row) => row.name),
                              mode: "inplace",
                            })
                          }
                        >
                          一键导入
                        </Button>
                      </Tip>
                    )}
                    {selectionMode && (key === "done" || key === "failed") && (
                      <button
                        type="button"
                        className="float-right text-t-xs"
                        disabled={saving}
                        aria-label={`${label}${
                          (filtered[key] ?? [])
                            .filter((row) => row.can_retry && !row.in_retry)
                            .every((row) => checked.has(row.item))
                            ? "清空本组"
                            : "全选"
                        }`}
                        onClick={(event) => {
                          event.preventDefault();
                          const eligible = (filtered[key] ?? []).filter(
                            (row) => row.can_retry && !row.in_retry,
                          );
                          const allChecked = eligible.every((row) =>
                            checked.has(row.item),
                          );
                          setChecked((previous) => {
                            const next = new Set(previous);
                            for (const row of eligible) {
                              if (allChecked) next.delete(row.item);
                              else next.add(row.item);
                            }
                            return next;
                          });
                        }}
                      >
                        {/* L10（PRD F7 口径）：同一颗钮随态换文案——点下去是反选，
                            文案就必须能预告结果；六种叫法收敛为「全选 / 清空本组」。 */}
                        {(filtered[key] ?? [])
                          .filter((row) => row.can_retry && !row.in_retry)
                          .every((row) => checked.has(row.item))
                          ? "清空本组"
                          : "全选"}
                      </button>
                    )}
                  </div>
                  {(query || !collapsed.has(key)) && (
                    <div className="p-2">
                      {(() => {
                        // V3（2026-09-21 审计 / PRD F6 不冲突）：全量渲染不虚拟化，
                        // 但组内默认只呈现 4 行 + 「其余 N 条」展开钮——几百条时
                        // 一屏滚不到头是呈现层问题，截断即可，不必上虚拟化。
                        const rows = filtered[key] ?? [];
                        const expanded = query.trim() !== "" || expandedGroups.has(key);
                        const shown = expanded ? rows : rows.slice(0, 4);
                        const rest = rows.length - shown.length;
                        return (
                          <>
                            {shown.map((row) => (
                              <MaterialRow
                                key={itemKey(row)}
                                row={row}
                                selected={selectedItem === itemKey(row)}
                                onSelect={choose}
                                selectionMode={selectionMode}
                                checked={checked.has(row.item)}
                                onCheck={toggleCheck}
                                retryGroup={key === "retry"}
                                saving={saving}
                                onRemoveRetry={removeRetry}
                                onRecover={recover}
                                onRemoveUnimported={requestRemoval}
                              />
                            ))}
                            {rest > 0 && (
                              <button
                                type="button"
                                className="w-full rounded-md px-2 py-2 text-left text-t-sm text-muted-foreground hover:bg-accent"
                                onClick={() =>
                                  setExpandedGroups((previous) => {
                                    const next = new Set(previous);
                                    next.add(key);
                                    return next;
                                  })
                                }
                              >
                                … 其余 {rest} 条（点任意条目可预览）
                              </button>
                            )}
                            {expandedGroups.has(key) && rows.length > 4 && (
                              <button
                                type="button"
                                className="w-full rounded-md px-2 py-2 text-left text-t-sm text-muted-foreground hover:bg-accent"
                                onClick={() =>
                                  setExpandedGroups((previous) => {
                                    const next = new Set(previous);
                                    next.delete(key);
                                    return next;
                                  })
                                }
                              >
                                收起
                              </button>
                            )}
                          </>
                        );
                      })()}
                    </div>
                  )}
                </section>
              ))}
          </div>
        </aside>
        <div className="flex min-w-0 shrink-0 flex-col lg:flex-1 lg:overflow-auto">
          {creating ? (
            // L1（2026-09-21 审计）：新建跑批改为画布内换块——顶栏与左列保留，
            // 返回后选择、折叠与滚动位置都还在（不再整页卸载）。无选中批次时同样可用。
            <NewBatchForm
              onBack={() => setCreating(false)}
              onNavigateToSettings={onNavigateToSettings}
              onOpenWorkbench={onOpenWorkbench}
              onCreated={(value) => {
                setCreating(false);
                setSelection(value);
                void loadWorkdirBatches()
                  .then((loaded) => {
                    if (mounted.current) setWorkdirs(loaded);
                  })
                  .catch((reason: unknown) => {
                    if (mounted.current) setError(errorMessage(reason));
                  });
              }}
            />
          ) : (
            <>
              {followPaused && batchRunState === "running" && (
                // L2：停跟随从「静默停」改成「明说 + 一键恢复」。
                <div className="mb-3 flex items-center gap-3 rounded-lg border border-border bg-muted/50 px-3 py-2 text-t-sm text-text-3">
                  <span>已暂停跟随正在打标的条目。</span>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => {
                      followRun.current = true;
                      setFollowPaused(false);
                      setSelectedItem(null);
                    }}
                  >
                    继续跟随
                  </Button>
                </div>
              )}
              {!selected && selection && !loading && (
                <BatchOverview
                  key={identity}
                  wid={selection.workdirId}
                  batch={selection.batchId}
                  items={items}
                  exportRevision={exportRevision}
                  running={batchRunState === "running" || batchRunState === "pending"}
                  liveOutput={liveOutput}
                  onRunStarted={(id) => setExternalRun({ identity, id })}
                  onSelect={choose}
                  onImported={() => void refreshItems()}
                  onImport={() => setImportOpen(true)}
                />
              )}
              {!selected && !selection && (
                <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
                  <FileImageIcon className="size-6" />
                  <p>选择素材</p>
                </div>
              )}
              {selected && (
                <>
                  <div className="mb-3 flex min-w-0 items-center gap-3">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        followRun.current = false;
                        setSelectedItem(null);
                      }}
                    >
                      <ArrowLeftIcon />
                      返回概览
                    </Button>
                    <h2 className="min-w-0 truncate text-t-md font-medium">
                      {selected.name}
                    </h2>
                    <span
                      className={`shrink-0 text-t-sm ${selected.status === "done" ? "text-ok-ink" : selected.status === "failed" ? "text-bad-ink" : "text-text-3"}`}
                    >
                      {ITEM_GROUPS.find(([key]) => key === selected.status)?.[1] ??
                        selected.status}
                    </span>
                  </div>
                  {asset &&
                    selected.status !== "missing" &&
                    selected.status !== "unimported" && (
                      <div
                        className={`relative w-full shrink-0 overflow-hidden rounded-xl border border-border bg-muted/45 ${foldedItem === `${identity}/${selected.item}` ? "h-[76px]" : "h-96"}`}
                      >
                        {selected.media === "video" ? (
                          <video
                            controls
                            src={asset}
                            aria-label={selected.name}
                            className="h-full w-full object-contain"
                            onLoadedMetadata={(event) => {
                              const el = event.currentTarget;
                              setMediaMeta({
                                w: el.videoWidth,
                                h: el.videoHeight,
                                duration: el.duration,
                              });
                            }}
                          >
                            <track kind="captions" />
                          </video>
                        ) : (
                          <img
                            src={asset}
                            alt={selected.name}
                            className="h-full w-full object-contain"
                            onLoad={(event) => {
                              const el = event.currentTarget;
                              setMediaMeta({ w: el.naturalWidth, h: el.naturalHeight });
                            }}
                          />
                        )}
                        <div
                          className={`absolute right-3 flex items-center gap-1.5 rounded-md bg-card/95 py-1 pr-1 pl-2.5 text-t-xs text-text-3 shadow-(--sh-1) ${
                            selected.media === "video" ? "bottom-14" : "bottom-3"
                          }`}
                        >
                          {mediaMeta && (
                            <span className="tabular-nums">
                              {mediaMeta.w}×{mediaMeta.h}
                              {mediaMeta.duration !== undefined
                                ? ` · ${mediaMeta.duration.toFixed(1)} 秒`
                                : ""}
                              {` · ${(selected.name.split(".").pop() ?? "").toUpperCase()}`}
                            </span>
                          )}
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={
                              foldedItem === `${identity}/${selected.item}`
                                ? "展开素材"
                                : "折叠为小图"
                            }
                            aria-expanded={
                              foldedItem !== `${identity}/${selected.item}`
                            }
                            onClick={() =>
                              setFoldedItem((previous) =>
                                previous === `${identity}/${selected.item}`
                                  ? null
                                  : `${identity}/${selected.item}`,
                              )
                            }
                          >
                            {foldedItem === `${identity}/${selected.item}` ? (
                              <Maximize2Icon />
                            ) : (
                              <Minimize2Icon />
                            )}
                          </Button>
                        </div>
                      </div>
                    )}
                  {(selected.message || selected.reason) && (
                    <p className="mt-3 text-warn-ink">
                      {selected.message || selected.reason}
                    </p>
                  )}
                  {selection && selected.status !== "unimported" && (
                    <CaptionPreview
                      key={`${selection.workdirId}/${selection.batchId}/${selected.item}`}
                      wid={selection.workdirId}
                      batch={selection.batchId}
                      batches={
                        workdirs.find((entry) => entry.id === selection.workdirId)
                          ?.batches
                      }
                      row={selected}
                      onRetryChange={retryChanged}
                    />
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
