/** 服务 · 服务运行（状态 + 日志）。 */
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import type { ServiceLogs, ServiceStatus } from "../../api";
import { api } from "../../api";
import { ShutdownButton } from "../../components/shutdown-button";
import { Button } from "../../components/ui/button";
import { reportError } from "../../lib/feedback";

export function ServicePanel(): ReactElement {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [logs, setLogs] = useState<ServiceLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [nextStatus, nextLogs] = await Promise.all([
        api.getService(),
        api.getServiceLogs(),
      ]);
      setStatus(nextStatus);
      setLogs(nextLogs);
      setError(null);
    } catch (err) {
      // 连不上后端时这里要报的是**状态**（服务不可用），不是把那句长提示焊在卡上——
      // 长提示已经由浮层给过一次性提醒了。
      setError(reportError(err) ?? "服务不可用");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
        {status !== null ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-2 text-t-md font-medium">
              <span className="size-2 rounded-full bg-success" aria-hidden />
              服务运行中 · {status.version}
            </span>
            <span className="text-t-sm text-muted-foreground">
              监听 {status.host}:{status.port}
            </span>
            <span className="flex-1" />
            <ShutdownButton expanded />
          </div>
        ) : (
          <p className="text-t-md text-muted-foreground">{error ?? "读取中…"}</p>
        )}
      </div>
      <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-t-md font-medium">运行日志</h3>
          <span className="text-t-xs font-medium text-muted-foreground">
            最近 200 行
          </span>
          <span className="flex-1" />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => void reload()}
          >
            刷新
          </Button>
        </div>
        {logs?.exists ? (
          <pre className="max-h-80 overflow-auto rounded-md bg-muted/40 p-3 text-t-xs leading-[1.75]">
            {logs.content}
          </pre>
        ) : (
          <p className="text-t-sm text-muted-foreground">{error ?? "（暂无日志）"}</p>
        )}
      </div>
    </div>
  );
}
