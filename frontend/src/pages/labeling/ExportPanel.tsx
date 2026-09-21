import {
  ArrowRightIcon,
  ChevronDownIcon,
  DownloadIcon,
  PackageIcon,
  SquareIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, api, errorMessage, type TaskView } from "../../api";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";
import { Switch } from "../../components/ui/switch";
import { formatBytes } from "../../lib/format";

type Plan = components["schemas"]["ExportPlanView"];
type Row = components["schemas"]["ExportPlanRow"];

interface Props {
  wid: string;
  batch: string;
  refreshKey: unknown;
  onImport?: () => void;
  /**
   * 发车前摘要的媒体维度（V5）：plan 响应里没有图片 / 视频计数（契约如此），
   * 这些只能由父组件从条目列表与完整性报告汇出后传入——export/plan 不为此扩契约。
   */
  mediaSummary?: {
    images: number;
    videos: number;
    changed: number;
  };
}

function ExportList({
  rows,
  excluded,
  busy,
  onChange,
  onImport,
}: {
  rows: Row[];
  excluded: boolean;
  busy: boolean;
  onChange: (items: string[], excluded: boolean) => Promise<boolean>;
  onImport?: () => void;
}) {
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState(false);
  const eligible = rows.filter((row) => !excluded || row.reason === "用户排除");
  const chosen = eligible
    .filter((row) => selected.has(row.item))
    .map((row) => row.item);
  const visible = expanded || selecting ? rows : rows.slice(0, 10);
  async function change(items: string[]) {
    if (await onChange(items, !excluded)) {
      setSelected(new Set());
      setSelecting(false);
    }
  }
  return (
    <section
      aria-label={excluded ? "被排除清单" : "将入包清单"}
      className={excluded ? "mt-2 border-t border-dashed border-border pt-2" : ""}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2 text-t-sm text-text-3">
        <span>
          {excluded ? "被排除" : "将入包"} {rows.length} 条
        </span>
        <div className="ml-auto flex items-center gap-2">
          {selecting && (
            <>
              <span className="tabular-nums">已选 {chosen.length}</span>
              <Button
                variant="ghost"
                size="mini"
                disabled={busy}
                onClick={() =>
                  setSelected(
                    new Set(
                      chosen.length === eligible.length
                        ? []
                        : eligible.map((row) => row.item),
                    ),
                  )
                }
              >
                {/* L10：随态换文案（与打标页左列同一口径）。 */}
                {chosen.length === eligible.length ? "清空本组" : "全选"}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                disabled={busy || !chosen.length}
                onClick={() => void change(chosen)}
              >
                {excluded ? "撤销排除" : "排除选中的"}
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="xs"
            disabled={busy || !eligible.length}
            onClick={() => {
              setSelecting(!selecting);
              setSelected(new Set());
            }}
          >
            {selecting ? "退出选择" : "选择"}
          </Button>
        </div>
      </div>
      <ul className="space-y-1 text-t-sm text-text-3">
        {visible.map((row) => (
          <li
            key={`${row.item}/${row.name}`}
            className="flex min-w-0 flex-wrap items-center gap-2"
          >
            {selecting && (!excluded || row.reason === "用户排除") && (
              <input
                type="checkbox"
                className="cb"
                aria-label={`选择 ${row.name}`}
                disabled={busy}
                checked={selected.has(row.item)}
                onChange={(event) => {
                  const checked = event.currentTarget.checked;
                  setSelected((old) => {
                    const next = new Set(old);
                    if (checked) next.add(row.item);
                    else next.delete(row.item);
                    return next;
                  });
                }}
              />
            )}
            <span className="break-all">{row.name}</span>
            {excluded ? (
              <>
                <span className="text-bad-ink">{row.reason}</span>
                {row.reason === "用户排除" ? (
                  <Button
                    className="ml-auto"
                    variant="ghost"
                    size="xs"
                    disabled={busy}
                    onClick={() => void change([row.item])}
                  >
                    撤销排除
                  </Button>
                ) : (
                  ["缺失", "未登记", "无配对产物"].includes(row.reason ?? "") &&
                  onImport && (
                    <Button
                      className="ml-auto"
                      variant="ghost"
                      size="mini"
                      onClick={onImport}
                    >
                      导入素材
                    </Button>
                  )
                )}
              </>
            ) : (
              <>
                <ArrowRightIcon
                  aria-hidden="true"
                  className="size-3 shrink-0 text-text-4"
                />
                <span className="break-all">
                  {row.asset_name} + {row.caption_name}
                </span>
                {row.integrity === "changed" && (
                  <span className="text-warn-ink">打标后素材已变更</span>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
      {rows.length > 10 && !selecting && (
        <Button
          variant="ghost"
          size="mini"
          className="mt-1"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起" : `展开全部 ${rows.length} 条`}
        </Button>
      )}
    </section>
  );
}

export function ExportPanel({ wid, batch, refreshKey, onImport, mediaSummary }: Props) {
  const sequentialId = useId();
  const [sequential, setSequential] = useState(true);
  const [expanded, setExpanded] = useState(true);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [planError, setPlanError] = useState("");
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<TaskView | null>(null);
  const [pollFailed, setPollFailed] = useState(false);
  const [pollRevision, setPollRevision] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const cancelPending = useRef(false);
  const [download, setDownload] = useState<{ url: string; path: string } | null>(null);
  const generation = useRef(0);
  const pending = useRef(false);
  const taskId = task?.id;
  const taskStatus = task?.status;
  const currentTask = useRef(task);
  currentTask.current = task;
  const locked = busy || taskStatus === "running";

  // biome-ignore lint/correctness/useExhaustiveDependencies: identity changes reset task ownership and invalidate pending responses.
  useEffect(() => {
    generation.current += 1;
    pending.current = false;
    setPlan(null);
    setSequential(true);
    setTask(null);
    setDownload(null);
    setBusy(false);
    setError("");
    setPlanError("");
    setPollFailed(false);
    setCancelling(false);
    setCancelRequested(false);
    setCancelError("");
    cancelPending.current = false;
    return () => {
      generation.current += 1;
    };
  }, [wid, batch]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: completed full synchronization and explicit refresh invalidate the export plan.
  useEffect(() => {
    let current = true;
    setLoading(true);
    setPlan(null);
    setPlanError("");
    api
      .exportPlan(wid, batch, sequential)
      .then((value) => {
        if (current) setPlan(value);
      })
      .catch((reason) => {
        if (current) setPlanError(errorMessage(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [wid, batch, sequential, refreshKey, revision]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: requery observes the accepted task without submitting another export.
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
        if (view.id !== id) throw new Error("任务响应与当前导出不一致");
        if (view.status === "running") timer = setTimeout(() => void poll(), 2000);
        else if (view.status === "succeeded") {
          const result = view.result;
          if (
            !result ||
            typeof result !== "object" ||
            !("download_url" in result) ||
            typeof result.download_url !== "string" ||
            !("path" in result) ||
            typeof result.path !== "string"
          )
            throw new Error("导出结果格式无效");
          const prefix = `/api/workdirs/${encodeURIComponent(wid)}/export/files/`;
          if (
            !result.download_url.startsWith(prefix) ||
            !/^s[1-9][0-9]*-[0-9a-f]{24}\.zip$/.test(
              result.download_url.slice(prefix.length),
            )
          )
            throw new Error("导出下载地址无效");
          setDownload({ url: result.download_url, path: result.path });
          setRevision((value) => value + 1);
        } else
          setError(
            view.error || (view.status === "cancelled" ? "导出已取消" : "导出失败"),
          );
        setTask(view);
      } catch (reason) {
        if (current) {
          if (reason instanceof ApiError && reason.status === 404) {
            setTask(null);
            setPollFailed(false);
            setError("导出任务已丢失，请重新查询导出计划后重试");
            return;
          }
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
  }, [taskId, taskStatus, wid, pollRevision]);

  async function exclusions(items: string[], excluded: boolean) {
    if (pending.current || locked) return false;
    const version = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await api.setExclusions(wid, batch, items, excluded);
      if (version !== generation.current) return false;
      setDownload(null);
      setRevision((value) => value + 1);
      return true;
    } catch (reason) {
      if (version === generation.current) setError(errorMessage(reason));
      return false;
    } finally {
      if (version === generation.current) {
        pending.current = false;
        setBusy(false);
      }
    }
  }

  async function start() {
    if (pending.current || locked || loading || !plan?.included.length) return;
    const version = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    setDownload(null);
    setCancelRequested(false);
    setCancelError("");
    try {
      const accepted = await api.startExport(wid, batch, sequential);
      if (!accepted || typeof accepted.task_id !== "string" || !accepted.task_id.trim())
        throw new Error("导出任务编号无效");
      if (version === generation.current)
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
      if (version === generation.current) {
        pending.current = false;
        setBusy(false);
      }
    }
  }

  async function cancel() {
    if (!taskId || taskStatus !== "running" || cancelPending.current || cancelRequested)
      return;
    const version = generation.current;
    const id = taskId;
    cancelPending.current = true;
    setCancelling(true);
    setCancelError("");
    try {
      await api.cancelTask(id);
      if (
        version !== generation.current ||
        currentTask.current?.id !== id ||
        currentTask.current.status !== "running"
      )
        return;
      setCancelRequested(true);
      setError("");
      setPollRevision((value) => value + 1);
    } catch (reason) {
      if (
        version === generation.current &&
        currentTask.current?.id === id &&
        currentTask.current.status === "running"
      )
        setCancelError(errorMessage(reason));
    } finally {
      if (version === generation.current) {
        cancelPending.current = false;
        setCancelling(false);
      }
    }
  }

  return (
    <section aria-label="打包与导出" className="mt-4 border-t border-border pt-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Button
          variant="ghost"
          size="xs"
          aria-label="折叠或展开打包清单"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          <ChevronDownIcon className={expanded ? "" : "-rotate-90"} />
        </Button>
        <h3 className="text-t-md font-medium">打包与导出</h3>
        {plan && (
          <span className="ml-auto text-t-xs text-text-4 tabular-nums">
            {/* V5（2026-09-21 审计）：发车前最该核对的维度一次给全——
                将入包 N 条 · 图片 N · 视频 N · 含变更素材 N · 共 X MiB。 */}
            {[
              `${plan.included.length} 条`,
              mediaSummary !== undefined
                ? `图片 ${mediaSummary.images} · 视频 ${mediaSummary.videos} · 含变更素材 ${mediaSummary.changed}`
                : null,
              formatBytes(plan.total_bytes, "MiB", 2),
            ]
              .filter((part) => part !== null)
              .join(" · ")}
          </span>
        )}
      </div>
      {planError && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <FormError className="text-t-sm text-bad-ink">{planError}</FormError>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setRevision((value) => value + 1)}
          >
            重查导出计划
          </Button>
        </div>
      )}
      {error && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <FormError className="text-t-sm text-bad-ink">{error}</FormError>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => {
              setError("");
              if (pollFailed && taskStatus === "running")
                setPollRevision((value) => value + 1);
              else setRevision((value) => value + 1);
            }}
          >
            重新查询
          </Button>
        </div>
      )}
      {expanded && (
        <div aria-busy={loading}>
          <label
            htmlFor={sequentialId}
            className="mb-2 flex items-center justify-between gap-2 text-t-sm text-text-3"
          >
            顺序重命名（001 起 · 3 位定宽）
            <Switch
              id={sequentialId}
              aria-label="顺序重命名"
              checked={sequential}
              disabled={locked}
              onCheckedChange={(checked) => {
                setSequential(checked);
                setDownload(null);
              }}
            />
          </label>
          {loading && (
            <p role="status" className="text-t-sm text-text-4">
              正在计算导出计划
            </p>
          )}
          {!sequential && plan?.non_ascii_names && (
            <p className="mb-2 text-t-sm text-warn-ink">
              文件名含非 ASCII 字符，跨平台解压可能导致素材与 txt 配对失败。
            </p>
          )}
          <div hidden={!plan || loading}>
            <ExportList
              key={`${wid}/${batch}/included`}
              rows={plan?.included ?? []}
              excluded={false}
              busy={locked || loading}
              onChange={exclusions}
            />
            <ExportList
              key={`${wid}/${batch}/excluded`}
              rows={plan?.excluded ?? []}
              excluded
              busy={locked || loading}
              onChange={exclusions}
              onImport={onImport}
            />
          </div>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          disabled={locked || loading || !!error || !plan?.included.length}
          onClick={() => void start()}
        >
          <PackageIcon />
          导出当前策略
        </Button>
        {taskStatus === "running" && (
          <>
            <span role="status" className="text-t-sm text-text-3">
              {cancelRequested ? "正在取消" : "正在打包"} ·{" "}
              {Math.round((task?.progress ?? 0) * 100)}%
            </span>
            <Button
              variant="ghost"
              size="xs"
              disabled={cancelling || cancelRequested}
              onClick={() => void cancel()}
            >
              <SquareIcon />
              取消导出
            </Button>
            {cancelError && (
              <FormError className="text-t-sm text-bad-ink">{cancelError}</FormError>
            )}
          </>
        )}
        {download && (
          <>
            <a
              href={download.url}
              download
              className="inline-flex items-center gap-2 text-t-sm text-text-2 underline"
            >
              <DownloadIcon className="size-4" />
              下载 ZIP
            </a>
            <span className="min-w-0 break-all text-t-xs text-text-4">
              {download.path}
            </span>
          </>
        )}
      </div>
    </section>
  );
}
