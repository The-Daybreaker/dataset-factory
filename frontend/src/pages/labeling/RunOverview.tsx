import { useEffect, useMemo, useState } from "react";
import { ApiError, api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

type History = components["schemas"]["RunHistoryView"];
type RunLogTab = "run.log" | "items.jsonl";

interface Props {
  wid: string;
  batch: string;
  refreshKey: unknown;
  fallback: { total: number; done: number; failed: number };
}

/** items.jsonl 单行事件里「逐条流水」表格用得到的字段；未知字段忽略。 */
interface ItemRunRecord {
  item: string;
  status: string;
  attempt?: number;
  elapsed_ms?: number;
  asset_hash?: string;
  message?: string;
}

/** 日志展示口径：尾部 200 行、最新在最上（原型定案，说明行同步告知）。 */
const LOG_TAIL_LINES = 200;

function parseItemRecords(text: string): ItemRunRecord[] {
  const records: ItemRunRecord[] = [];
  for (const line of text.replace(/\n$/, "").split("\n")) {
    if (!line.trim()) continue;
    try {
      const raw = JSON.parse(line) as Record<string, unknown>;
      if (typeof raw.item === "string" && typeof raw.status === "string") {
        records.push({
          item: raw.item,
          status: raw.status,
          attempt: typeof raw.attempt === "number" ? raw.attempt : undefined,
          elapsed_ms: typeof raw.elapsed_ms === "number" ? raw.elapsed_ms : undefined,
          asset_hash: typeof raw.asset_hash === "string" ? raw.asset_hash : undefined,
          message: typeof raw.message === "string" ? raw.message : undefined,
        });
      }
    } catch {
      // 残缺行跳过：逐条流水只是给人看的视图，不参与判定。
    }
  }
  return records;
}

/** 每个条目取最新一条记录，返回顺序 = 最新处理的条目在前。 */
function latestByItem(records: ItemRunRecord[]): ItemRunRecord[] {
  const byItem = new Map<string, ItemRunRecord>();
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record && !byItem.has(record.item)) byItem.set(record.item, record);
  }
  return [...byItem.values()];
}

function formatElapsed(ms: number | undefined): string {
  return ms === undefined ? "—" : `${(ms / 1000).toFixed(1)}s`;
}

function RunLogDialog({
  wid,
  batch,
  runId,
  onClose,
}: {
  wid: string;
  batch: string;
  runId: string;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<RunLogTab>("run.log");
  const [result, setResult] = useState<components["schemas"]["RunTextView"] | null>(
    null,
  );
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [copyState, setCopyState] = useState<"ok" | "fail" | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: revision explicitly reloads the selected tab.
  useEffect(() => {
    let current = true;
    setError("");
    api
      .readRunText(wid, batch, runId, tab)
      .then((value) => {
        if (current) setResult(value);
      })
      .catch((reason) => {
        if (current) setError(errorMessage(reason));
      });
    return () => {
      current = false;
    };
  }, [wid, batch, runId, tab, revision]);
  const lines = useMemo(
    () => (result?.text ? result.text.replace(/\n$/, "").split("\n") : []),
    [result],
  );
  const displayText = useMemo(
    () => lines.slice(-LOG_TAIL_LINES).reverse().join("\n"),
    [lines],
  );
  const itemRows = useMemo(
    () =>
      tab === "items.jsonl" ? latestByItem(parseItemRecords(result?.text ?? "")) : null,
    [tab, result],
  );
  const copy = async () => {
    const text =
      tab === "run.log"
        ? displayText
        : [
            ["素材", "状态", "尝试", "耗时", "素材哈希", "说明"].join("\t"),
            ...(itemRows ?? []).map((row) =>
              [
                row.item,
                row.status === "succeeded" ? "成功" : "失败",
                row.attempt ?? "—",
                formatElapsed(row.elapsed_ms),
                row.asset_hash ? `${row.asset_hash.slice(0, 6)}…` : "—",
                row.message || "—",
              ].join("\t"),
            ),
          ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("ok");
    } catch {
      setCopyState("fail");
    }
    window.setTimeout(() => setCopyState(null), 1500);
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="grid-cols-[minmax(0,1fr)] max-h-[90dvh] w-[min(780px,100%)] max-w-none overflow-y-auto">
        <DialogHeader className="flex-row items-center gap-3 space-y-0">
          <DialogTitle className="shrink-0">运行日志</DialogTitle>
          <div
            role="tablist"
            aria-label="日志视图"
            className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-muted p-0.5"
          >
            {(
              [
                ["run.log", "运行日志"],
                ["items.jsonl", "逐条流水"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={tab === value}
                className={`h-(--h-sm) rounded-sm px-3 text-t-sm ${
                  tab === value
                    ? "bg-card font-medium text-text-1 shadow-(--sh-1)"
                    : "text-text-3"
                }`}
                onClick={() => setTab(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <DialogDescription className="min-w-0 flex-1 truncate">
            {result?.path ?? runId}
          </DialogDescription>
        </DialogHeader>
        {error && <FormError className="text-bad-ink">{error}</FormError>}
        {tab === "run.log" ? (
          <pre
            data-testid="run-log-body"
            className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-sans text-t-sm whitespace-pre-wrap break-all"
          >
            {result ? displayText || "暂无记录" : "正在读取"}
          </pre>
        ) : (
          <div className="max-h-96 overflow-auto rounded-md border border-border">
            <table className="w-full text-t-sm">
              <thead className="sticky top-0 bg-muted text-left text-text-3">
                <tr>
                  {["素材", "状态", "尝试", "耗时", "素材哈希", "说明"].map((label) => (
                    <th key={label} className="px-3 py-2 font-medium">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {itemRows?.length ? (
                  itemRows.map((row) => (
                    <tr key={row.item} className="border-t border-border/60">
                      <td className="px-3 py-1.5 break-all">{row.item}</td>
                      <td className="px-3 py-1.5">
                        <span
                          className={
                            row.status === "succeeded" ? "text-ok-ink" : "text-bad-ink"
                          }
                        >
                          {row.status === "succeeded" ? "成功" : "失败"}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 tabular-nums">{row.attempt ?? "—"}</td>
                      <td className="px-3 py-1.5 tabular-nums">
                        {formatElapsed(row.elapsed_ms)}
                      </td>
                      <td className="px-3 py-1.5 tabular-nums">
                        {row.asset_hash ? `${row.asset_hash.slice(0, 6)}…` : "—"}
                      </td>
                      <td className="min-w-0 max-w-60 truncate px-3 py-1.5 text-text-3">
                        {row.message || "—"}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6} className="px-3 py-4 text-center text-text-3">
                      {result ? "暂无记录" : "正在读取"}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center gap-2">
          {tab === "run.log" && (
            <span className="text-t-sm text-text-3">
              最新在最上 · 显示尾部 {LOG_TAIL_LINES} 行
            </span>
          )}
          <div className="ml-auto flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => void copy()}>
              {copyState === "ok"
                ? "已复制"
                : copyState === "fail"
                  ? "复制失败"
                  : "复制"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRevision((value) => value + 1)}
            >
              刷新
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-text-1"
              onClick={() => onClose()}
            >
              关闭
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function RunOverview({ wid, batch, refreshKey, fallback }: Props) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [log, setLog] = useState<{ runId: string } | null>(null);
  const [revision, setRevision] = useState(0);
  // biome-ignore lint/correctness/useExhaustiveDependencies: changing batch identity clears the previous summary and selected log.
  useEffect(() => {
    setHistory(null);
    setLog(null);
  }, [wid, batch]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: item refresh and manual revision refresh the disk summary.
  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retryDelay = 500;
    async function load() {
      try {
        const value = await api.latestRun(wid, batch);
        if (!current) return;
        setHistory(value);
        setError("");
        retryDelay = 500;
        if (value.record?.status === "running")
          timer = setTimeout(() => void load(), 2000);
      } catch (reason) {
        if (!current) return;
        setError(errorMessage(reason));
        if (
          reason instanceof ApiError &&
          (reason.status === 409 ||
            reason.kind === "network" ||
            reason.kind === "timeout")
        ) {
          timer = setTimeout(() => void load(), retryDelay);
          retryDelay = Math.min(retryDelay * 2, 5000);
        }
      }
    }
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [wid, batch, refreshKey, revision]);
  const record = history?.record;
  const counts = record
    ? {
        total: record.counters.attempted + record.counters.skipped,
        done: record.counters.succeeded,
        failed: record.counters.failed,
      }
    : fallback;
  const elapsed = record?.finished_at
    ? Math.max(
        0,
        (Date.parse(record.finished_at) - Date.parse(record.started_at)) / 1000,
      )
    : null;
  return (
    <section aria-label={record ? "本次运行" : "条目汇总"}>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h3 className="text-t-md font-medium">{record ? "本次运行" : "条目汇总"}</h3>
        {record && (
          <>
            <span className="text-t-xs text-text-4">
              {new Date(record.started_at).toLocaleString()} →{" "}
              {record.finished_at
                ? new Date(record.finished_at).toLocaleTimeString()
                : "未记录结束时间"}
              {elapsed !== null ? ` · ${elapsed.toFixed(1)} 秒` : ""}
            </span>
            <div className="ml-auto flex gap-2">
              <Button
                variant="ghost"
                size="mini"
                onClick={() => setLog({ runId: record.run_id })}
              >
                查看日志
              </Button>
            </div>
          </>
        )}
      </div>
      {error && (
        <div className="mb-3 flex items-center gap-2">
          <FormError className="text-bad-ink">{error}</FormError>
          <Button
            variant="ghost"
            size="mini"
            onClick={() => setRevision((value) => value + 1)}
          >
            重新查询
          </Button>
        </div>
      )}
      <dl className="grid max-w-115 grid-cols-3 gap-3">
        {[
          ["总数", counts.total],
          [record ? "成功" : "已完成", counts.done],
          ["未完成", counts.failed],
        ].map(([label, count]) => (
          <div key={label} className="rounded-lg border border-border bg-card p-3">
            <dt className="text-t-sm text-muted-foreground">{label}</dt>
            <dd
              className={`mt-1 text-t-2xl leading-(--lh-tight) font-medium tabular-nums ${label === "未完成" && count !== 0 ? "text-bad-ink" : "text-text-1"}`}
            >
              {count}
            </dd>
          </div>
        ))}
      </dl>
      {record && (
        <dl className="mt-3 space-y-1 text-t-sm">
          {[
            [
              "计划",
              `${record.counters.planned} 条 · 跳过 ${record.counters.skipped} 条 · 未跑 ${Math.max(0, record.counters.planned - record.counters.attempted)} 条`,
            ],
            ["模式", record.mode === "retry" ? "重试" : "全量"],
            ["触发方", record.trigger === "cli" ? "CLI" : "Web"],
            [
              "状态",
              (
                {
                  completed: "已完成",
                  interrupted: "已中断",
                  failed: "失败",
                  running: "运行中",
                } as Record<string, string>
              )[record.status] ?? record.status,
            ],
            ["快照哈希", record.strategy_hash],
            ["日志路径", history.log_path ?? ""],
          ].map(([label, value]) => (
            <div key={label} className="flex gap-3">
              <dt className="w-21 shrink-0 text-text-4">{label}</dt>
              <dd className="min-w-0 break-all text-text-2">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {log && (
        <RunLogDialog
          key={`${wid}/${batch}/${log.runId}`}
          wid={wid}
          batch={batch}
          runId={log.runId}
          onClose={() => setLog(null)}
        />
      )}
    </section>
  );
}
