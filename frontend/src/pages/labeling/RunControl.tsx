import { Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
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
import { Tip } from "../../components/ui/tooltip";
import {
  type ItemDelta,
  type ItemUpdate,
  parseItemDelta,
  parseItemUpdate,
} from "./items-state";

type RunStatus = components["schemas"]["RunStatusView"];

interface Props {
  wid: string;
  batch: string;
  onFinish: () => void | Promise<void>;
  onItemUpdate?: (event: ItemUpdate) => void;
  /** 流式增量转发（A2：跑批逐字呈现）；不转发时增量事件只被忽略。 */
  onItemDelta?: (event: ItemDelta) => void;
  onCurrentItem?: (item: string | null) => void;
  onImport?: () => void;
  externalRunId?: string;
  /**
   * 左列「重试列表」组头发起的开始重试请求（令牌式触发：值变化即发车）。
   *
   * 为什么用令牌而不是把 `start` 提上去：`start` 要用本组件的生命周期代次
   * （`lifecycle`）做迟到响应护栏，提到父层就得把整套护栏也搬上去。令牌只
   * 传「点了」这一个事实，动作仍由持有护栏的这里执行——与页面别处
   * 「revision 计数器驱动重取」是同一个范式。
   */
  retryRequest?: number;
  /**
   * 报告本批次运行状态的每一次变化（进行中与终态都报），供顶栏状态章显示。
   *
   * 报的是「服务端说的事实」：受理成功报 running、SSE 终态报 run-finished 里的
   * status、轮询到已在进行中的运行也报它自己的 status——父层不必自己猜状态。
   */
  onRunStatus?: (status: string) => void;
}

/** 断流不代表运行结束，重连前用 current 确认运行并刷新条目。 */
export function RunControl({
  wid,
  batch,
  onFinish,
  onImport,
  onItemUpdate,
  onItemDelta,
  onCurrentItem,
  externalRunId,
  retryRequest,
  onRunStatus,
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
  const [retryConfirming, setRetryConfirming] = useState(false);
  const lifecycle = useRef(0);
  const actionPending = useRef(false);
  const finishRef = useRef(onFinish);
  finishRef.current = onFinish;
  const itemRef = useRef(onItemUpdate);
  itemRef.current = onItemUpdate;
  const itemDeltaRef = useRef(onItemDelta);
  itemDeltaRef.current = onItemDelta;
  const currentItemRef = useRef(onCurrentItem);
  currentItemRef.current = onCurrentItem;
  const runStatusRef = useRef(onRunStatus);
  runStatusRef.current = onRunStatus;

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let delay = 1000;
    let observed = acceptedRunId !== null || !!externalRunId;
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
      // L3（2026-09-21 审计定案）：空闲轮询从 5 秒自续降到 15 秒慢轮——它的存在
      // 理由只剩「发现 CLI 等外部进程启动的运行」（跨进程没有推送通道）；后端已把
      // 「没在跑」改成 200 + null，慢轮不再产生 404 错误噪音。后台标签页暂停。
      timer = setTimeout(() => {
        if (disposed) return;
        if (document.hidden) {
          timer = setTimeout(() => void connect(), 15000);
          return;
        }
        void connect();
      }, 15000);
    }

    function retry() {
      if (disposed) return;
      connection += 1;
      clearTimeout(timer);
      source?.close();
      source = null;
      setReconnecting(true);
      timer = setTimeout(() => void connect(), delay);
      delay = Math.min(delay * 2, 10000);
    }

    async function connect() {
      try {
        const view = await api.currentRun(wid, batch);
        if (disposed) return;
        setKnown(true);
        // 空闲 = 200 + null（L3）：不是错误、也不再轮询；404 只剩 wid / 批次不存在。
        // 空闲必须报给顶栏状态章：运行可能在页面未观察的窗口期结束（如设置抽屉开着
        // 时重打完成），不报会让 batchRunState 卡在 running、把导出入口永久藏住（V15）。
        if (view === null) {
          runStatusRef.current?.("idle");
          finish();
          return;
        }
        if (view.status !== "running" && view.status !== "pending") {
          if (view.error) setError(view.error);
          runStatusRef.current?.(view.status);
          finish();
          return;
        }
        observed = true;
        // 闭包里用的收窄副本（TS 不为嵌套函数保留 const 的 null 收窄）。
        const activeRun = view;
        setStatus(view);
        runStatusRef.current?.(view.status);
        setCurrent(view.current_item);
        if (view.current_item) currentItemRef.current?.(view.current_item);
        const token = ++connection;
        const stream = new EventSource(
          `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs/stream`,
        );
        source = stream;
        const isCurrent = () => !disposed && token === connection;
        let syncing = false;
        let syncAgain = false;
        let refreshedRunId: string | null = null;
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
              if (latest === null || latest.run_id !== activeRun.run_id) {
                // 空闲 / 换了运行：按收尾处理（B9 连带的 null 语义适配）。
                finish();
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
            // B9（2026-09-21 审计定案）：全量条目刷新**每个运行只做一次**——
            // 此前每次 SSE 建连 / 重连都触发一遍 listItems + export/plan。
            // 断线缺口由 syncProgress 的快照校准 + 终态刷新兜底。
            if (refreshedRunId !== activeRun.run_id) {
              refreshedRunId = activeRun.run_id;
              await finishRef.current();
              if (!isCurrent()) return;
            }
            for (const update of pending) itemRef.current?.(update);
            pending.length = 0;
            ready = true;
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
        stream.addEventListener("item-delta", (event) => {
          if (!isCurrent()) return;
          try {
            const data = parseItemDelta(
              JSON.parse((event as MessageEvent<string>).data),
            );
            if (data.batch === Number(batch.slice(1))) {
              itemDeltaRef.current?.(data);
            }
          } catch {
            // 增量帧解析失败不打断跑批观察（增量只是呈现层），忽略这一帧。
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
              data.run_id === activeRun.run_id &&
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
              runStatusRef.current?.(String(data.status));
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
        runStatusRef.current?.("running");
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

  // 左列组头的「开始重试」：令牌从 0 起，父层点一次加一；切换批次时父层清零，
  // 新挂载的组件因此不会被上一批次的令牌误触发。
  useEffect(() => {
    if (!retryRequest) return;
    void start("retry");
  }, [retryRequest, start]);

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
      {error && <FormError className="text-t-sm text-bad-ink">{error}</FormError>}
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
          {/* L6（2026-09-21 审计 / ui-spec §4.3）：禁用必须说明原因。 */}
          <Tip
            label={
              busy
                ? "上一个操作还在处理中"
                : reconnecting
                  ? "正在重新连接运行状态"
                  : !known
                    ? "正在确认运行状态…"
                    : ""
            }
          >
            <Button
              size="sm"
              disabled={busy || reconnecting || !known}
              onClick={() => void prepareFullRun()}
            >
              开始打标
            </Button>
          </Tip>
          <Tip
            label={
              busy
                ? "上一个操作还在处理中"
                : reconnecting
                  ? "正在重新连接运行状态"
                  : !known
                    ? "正在确认运行状态…"
                    : "将按重试列表的当前名单重新打标"
            }
          >
            <Button
              variant="outline"
              size="sm"
              disabled={busy || reconnecting || !known}
              onClick={() => setRetryConfirming(true)}
            >
              开始重试
            </Button>
          </Tip>
        </>
      )}
      {/* Q1（2026-09-21 复核定案）：名单发车不可撤销，顶栏「开始重试」必须过确认。 */}
      <Dialog open={retryConfirming} onOpenChange={setRetryConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>开始重试？</DialogTitle>
            <DialogDescription>
              将按重试列表的当前名单逐条重新打标（名单在发车瞬间拍快照）。发车后不可撤销，
              等它跑完或点「停止」前不能再发车。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRetryConfirming(false)}>
              取消
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                setRetryConfirming(false);
                void start("retry");
              }}
            >
              开始重试
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
