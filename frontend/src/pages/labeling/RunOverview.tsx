import { useEffect, useState } from "react";
import { ApiError, api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

type History = components["schemas"]["RunHistoryView"];
interface Props {
  wid: string;
  batch: string;
  refreshKey: unknown;
  fallback: { total: number; done: number; failed: number };
}

function RunLogDialog({
  wid,
  batch,
  runId,
  file,
  onClose,
}: {
  wid: string;
  batch: string;
  runId: string;
  file: "run.log" | "items.jsonl";
  onClose: () => void;
}) {
  const [result, setResult] = useState<components["schemas"]["RunTextView"] | null>(
    null,
  );
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  // biome-ignore lint/correctness/useExhaustiveDependencies: revision explicitly reloads the selected file.
  useEffect(() => {
    let current = true;
    setError("");
    api
      .readRunText(wid, batch, runId, file)
      .then((value) => {
        if (current) setResult(value);
      })
      .catch((reason) => {
        if (current) setError(errorMessage(reason));
      });
    return () => {
      current = false;
    };
  }, [wid, batch, runId, file, revision]);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] w-[min(860px,100%)] max-w-none overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{file === "run.log" ? "运行日志" : "逐条流水"}</DialogTitle>
          <DialogDescription className="break-all">
            {result?.path ?? runId}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p role="alert" className="text-bad-ink">
            {error}
          </p>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setRevision((value) => value + 1)}
        >
          刷新
        </Button>
        <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-sans text-t-sm whitespace-pre-wrap break-all">
          {result ? result.text || "暂无记录" : "正在读取"}
        </pre>
      </DialogContent>
    </Dialog>
  );
}

export function RunOverview({ wid, batch, refreshKey, fallback }: Props) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [log, setLog] = useState<{
    runId: string;
    file: "run.log" | "items.jsonl";
  } | null>(null);
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
                size="xs"
                onClick={() => setLog({ runId: record.run_id, file: "run.log" })}
              >
                查看 run.log
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setLog({ runId: record.run_id, file: "items.jsonl" })}
              >
                逐条流水
              </Button>
            </div>
          </>
        )}
      </div>
      {error && (
        <div className="mb-3 flex items-center gap-2">
          <p role="alert" className="text-bad-ink">
            {error}
          </p>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setRevision((value) => value + 1)}
          >
            重新查询
          </Button>
        </div>
      )}
      <dl className="grid grid-cols-3 gap-3">
        {[
          ["总数", counts.total],
          [record ? "成功" : "已完成", counts.done],
          ["未完成", counts.failed],
        ].map(([label, count]) => (
          <div key={label} className="rounded-lg border border-border bg-card p-3">
            <dt className="text-t-xs text-text-4">{label}</dt>
            <dd
              className={`mt-1 text-t-2xl font-medium tabular-nums ${label === "未完成" && count !== 0 ? "text-bad-ink" : "text-text-1"}`}
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
          key={`${wid}/${batch}/${log.runId}/${log.file}`}
          wid={wid}
          batch={batch}
          {...log}
          onClose={() => setLog(null)}
        />
      )}
    </section>
  );
}
