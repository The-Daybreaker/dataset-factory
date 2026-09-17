import { Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { type ItemUpdate, parseItemUpdate } from "./items-state";

type RunStatus = components["schemas"]["RunStatusView"];

interface Props {
  wid: string;
  batch: string;
  onFinish: () => void | Promise<void>;
  onItemUpdate?: (event: ItemUpdate) => void;
  onCurrentItem?: (item: string | null) => void;
  onImport?: () => void;
  externalRunId?: string;
}

/** 断流不代表运行结束，重连前用 current 确认运行并刷新条目。 */
export function RunControl({
  wid,
  batch,
  onFinish,
  onImport,
  onItemUpdate,
  onCurrentItem,
  externalRunId,
}: Props) {
  const [unimported, setUnimported] = useState<
    readonly { name: string; reason: string | null }[]
  >([]);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [acceptedRunId, setAcceptedRunId] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [known, setKnown] = useState(false);
  const lifecycle = useRef(0);
  const actionPending = useRef(false);
  const finishRef = useRef(onFinish);
  finishRef.current = onFinish;
  const itemRef = useRef(onItemUpdate);
  itemRef.current = onItemUpdate;
  const currentItemRef = useRef(onCurrentItem);
  currentItemRef.current = onCurrentItem;

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let delay = 1000;
    let observed = acceptedRunId !== null || !!externalRunId;
    let streamFailed = false;
    let connection = 0;
    const generation = ++lifecycle.current;
    setKnown(false);

    function finish() {
      connection += 1;
      clearTimeout(timer);
      source?.close();
      source = null;
      setStatus(null);
      setCurrent(null);
      currentItemRef.current?.(null);
      setReconnecting(false);
      setKnown(true);
      if (observed) {
        observed = false;
        void Promise.resolve(finishRef.current()).catch((reason: unknown) => {
          if (!disposed) setError(errorMessage(reason));
        });
      }
      timer = setTimeout(() => void connect(), 5000);
    }

    function retry() {
      if (disposed) return;
      connection += 1;
      clearTimeout(timer);
      source?.close();
      source = null;
      streamFailed = true;
      setReconnecting(true);
      timer = setTimeout(() => void connect(), delay);
      delay = Math.min(delay * 2, 10000);
    }

    async function connect() {
      try {
        const view = await api.currentRun(wid, batch);
        if (disposed) return;
        setKnown(true);
        if (view.status !== "running" && view.status !== "pending") {
          if (view.error) setError(view.error);
          finish();
          return;
        }
        observed = true;
        setStatus(view);
        setCurrent(view.current_item);
        if (view.current_item) currentItemRef.current?.(view.current_item);
        if (streamFailed) {
          await finishRef.current();
          if (disposed) return;
        }
        const token = ++connection;
        const stream = new EventSource(
          `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs/stream`,
        );
        source = stream;
        const isCurrent = () => !disposed && token === connection;
        let syncing = false;
        let syncAgain = false;
        // 订阅前的事件不会重放；按服务端快照校准，避免客户端增量漏计或重复计数。
        async function syncProgress() {
          syncAgain = true;
          if (syncing) return;
          syncing = true;
          try {
            while (syncAgain && isCurrent()) {
              syncAgain = false;
              const latest = await api.currentRun(wid, batch);
              if (!isCurrent()) return;
              if (latest.run_id !== view.run_id) {
                retry();
                return;
              }
              if (latest.status !== "running" && latest.status !== "pending") {
                if (latest.error) setError(latest.error);
                finish();
                return;
              }
              setStatus(latest);
              if (latest.current_item) currentItemRef.current?.(latest.current_item);
            }
          } catch (reason) {
            if (!isCurrent()) return;
            setError(errorMessage(reason));
            retry();
          } finally {
            syncing = false;
          }
        }
        let ready = false;
        const pending: ItemUpdate[] = [];
        stream.onopen = async () => {
          if (!isCurrent()) return;
          void syncProgress();
          try {
            await finishRef.current();
            if (!isCurrent()) return;
            for (const update of pending) itemRef.current?.(update);
            pending.length = 0;
            ready = true;
            streamFailed = false;
            delay = 1000;
            setError("");
            setReconnecting(false);
          } catch (reason) {
            if (!isCurrent()) return;
            setError(errorMessage(reason));
            retry();
          }
        };
        stream.addEventListener("run-started", () => {
          if (isCurrent()) void syncProgress();
        });
        stream.addEventListener("item-updated", (event) => {
          if (!isCurrent()) return;
          try {
            const data = parseItemUpdate(
              JSON.parse((event as MessageEvent<string>).data),
            );
            if (data.batch === Number(batch.slice(1))) {
              delay = 1000;
              setCurrent(data.item);
              if (data.status === "started") currentItemRef.current?.(data.item);
              if (ready) itemRef.current?.(data);
              else pending.push(data);
              void syncProgress();
            }
          } catch {
            setError("运行事件格式异常，正在重新同步");
            retry();
          }
        });
        stream.addEventListener("run-finished", (event) => {
          if (!isCurrent()) return;
          try {
            const data: unknown = JSON.parse((event as MessageEvent<string>).data);
            if (
              data &&
              typeof data === "object" &&
              "run_id" in data &&
              data.run_id === view.run_id &&
              "batch" in data &&
              data.batch === Number(batch.slice(1)) &&
              "status" in data &&
              ["completed", "interrupted", "failed"].includes(String(data.status))
            ) {
              if (data.status === "failed")
                setError(
                  "error" in data && typeof data.error === "string"
                    ? data.error
                    : "运行失败，请查看运行日志。",
                );
              finish();
            }
          } catch {
            retry();
          }
        });
        stream.onerror = () => {
          if (isCurrent()) retry();
        };
      } catch (reason) {
        if (disposed) return;
        if (reason instanceof ApiError && reason.status === 404) {
          finish();
        } else {
          setError(errorMessage(reason));
          retry();
        }
      }
    }

    void connect();
    return () => {
      disposed = true;
      if (lifecycle.current === generation) lifecycle.current += 1;
      clearTimeout(timer);
      source?.close();
    };
  }, [wid, batch, acceptedRunId, externalRunId]);

  const start = useCallback(
    async (mode: "full" | "retry") => {
      if (actionPending.current) return;
      const generation = lifecycle.current;
      actionPending.current = true;
      setBusy(true);
      setError("");
      try {
        const accepted = await api.startRun(wid, batch, mode);
        if (lifecycle.current !== generation) return;
        setStatus({
          run_id: accepted.run_id,
          status: "running",
          mode,
          batch: Number(batch.slice(1)),
          counters: {},
          current_item: null,
          error: null,
        });
        setAcceptedRunId(accepted.run_id);
      } catch (reason) {
        if (lifecycle.current === generation) setError(errorMessage(reason));
      } finally {
        actionPending.current = false;
        if (lifecycle.current === generation) {
          setBusy(false);
          setConfirming(false);
        }
      }
    },
    [batch, wid],
  );

  const stop = useCallback(async () => {
    if (actionPending.current) return;
    const generation = lifecycle.current;
    actionPending.current = true;
    setBusy(true);
    try {
      await api.stopRun(wid, batch);
    } catch (reason) {
      if (lifecycle.current === generation) setError(errorMessage(reason));
    } finally {
      actionPending.current = false;
      if (lifecycle.current === generation) setBusy(false);
    }
  }, [batch, wid]);

  async function prepareFullRun() {
    if (actionPending.current) return;
    const generation = lifecycle.current;
    actionPending.current = true;
    setBusy(true);
    setError("");
    try {
      const view = await api.listItems(wid, batch);
      if (lifecycle.current !== generation) return;
      const rows = (view.groups.unimported ?? []).map((row) => ({
        name: row.name,
        reason: row.reason ?? null,
      }));
      setUnimported(rows);
      if (rows.length) setConfirming(true);
      else {
        actionPending.current = false;
        await start("full");
      }
    } catch (reason) {
      if (lifecycle.current === generation) setError(errorMessage(reason));
    } finally {
      actionPending.current = false;
      if (lifecycle.current === generation) setBusy(false);
    }
  }

  return (
    <section className="flex items-center gap-3" aria-label="运行控制">
      {error && (
        <p role="alert" className="text-t-sm text-bad-ink">
          {error}
        </p>
      )}
      {reconnecting && (
        <span role="status" className="text-t-sm text-warn-ink">
          正在重新连接
        </span>
      )}
      {status ? (
        <>
          <span className="sr-only">{current ? `正在处理：${current}` : "运行中"}</span>
          <svg
            viewBox="0 0 20 20"
            className="size-4.5 shrink-0 -rotate-90"
            role="progressbar"
            aria-label="运行进度"
            aria-valuemin={0}
            aria-valuemax={status.counters.planned || 1}
            aria-valuenow={status.counters.attempted ?? 0}
          >
            <circle
              cx="10"
              cy="10"
              r="6"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              className="text-n-200"
            />
            <circle
              cx="10"
              cy="10"
              r="6"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              pathLength="100"
              strokeDasharray="100"
              strokeDashoffset={
                100 -
                Math.min(
                  100,
                  ((status.counters.attempted ?? 0) / (status.counters.planned || 1)) *
                    100,
                )
              }
              className="text-primary"
            />
          </svg>
          <span className="whitespace-nowrap text-t-sm tabular-nums text-muted-foreground">
            {`${status.counters.attempted ?? 0} / ${status.counters.planned ?? 0} · 失败 ${status.counters.failed ?? 0}`}
          </span>
          <Button
            variant="destructive-soft"
            size="sm"
            disabled={busy}
            data-testid="run-stop"
            onClick={() => void stop()}
          >
            <Square aria-hidden="true" />
            停止
          </Button>
        </>
      ) : (
        <>
          <Button
            size="sm"
            disabled={busy || reconnecting || !known}
            onClick={() => void prepareFullRun()}
          >
            开始打标
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy || reconnecting || !known}
            onClick={() => void start("retry")}
          >
            开始重试
          </Button>
        </>
      )}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>未导入素材确认</DialogTitle>
            <DialogDescription>
              有 {unimported.length} 个工作目录文件未登记，不会进入本次跑批。
            </DialogDescription>
          </DialogHeader>
          <ul
            className="max-h-48 overflow-auto rounded-md border border-border p-3 text-t-sm"
            aria-label="未导入素材"
          >
            {unimported.map((row) => (
              <li key={row.name}>
                {row.name}
                {row.reason && (
                  <span className="text-muted-foreground"> · {row.reason}</span>
                )}
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setConfirming(false);
                onImport?.();
              }}
            >
              先去导入
            </Button>
            <Button disabled={busy} onClick={() => void start("full")}>
              仍要开始
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
