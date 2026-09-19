import type { ReactElement } from "react";
import { useEffect, useReducer, useState } from "react";
import { api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";

/** 端点健康探测结果（§5.3：点的每个颜色都要有数据来源）。 */
type EndpointHealth = {
  status: "probing" | "ok" | "bad" | "unknown";
  message: string;
};

/** 会话级探测缓存：按端点名记一次结果，切回视图不重复探测。 */
const healthCache = new Map<string, EndpointHealth>();
const healthListeners = new Set<() => void>();

function setHealth(name: string, health: EndpointHealth): void {
  healthCache.set(name, health);
  for (const notify of healthListeners) notify();
}

function useEndpointHealth(
  name: string,
  target: { base_url: string; model: string; api_format: string } | undefined,
): EndpointHealth {
  const [, force] = useReducer((count: number) => count + 1, 0);
  useEffect(() => {
    healthListeners.add(force);
    return () => {
      healthListeners.delete(force);
    };
  }, []);
  const baseUrl = target?.base_url;
  const model = target?.model;
  const apiFormat = target?.api_format;
  useEffect(() => {
    if (!baseUrl || !model || healthCache.has(name)) return;
    setHealth(name, { status: "probing", message: "正在探测端点连通性…" });
    void api
      .testEndpoint({ base_url: baseUrl, model, api_format: apiFormat ?? "", name })
      .then((result) =>
        setHealth(
          name,
          result.ok
            ? {
                status: "ok",
                message: `端点连通 · 探测耗时 ${Math.round(result.latency_ms)}ms`,
              }
            : { status: "bad", message: result.message },
        ),
      )
      .catch((reason: unknown) =>
        setHealth(name, {
          status: "unknown",
          message: `探测请求失败：${errorMessage(reason)}`,
        }),
      );
  }, [name, baseUrl, model, apiFormat]);
  return healthCache.get(name) ?? { status: "probing", message: "正在探测端点连通性…" };
}

const HEALTH_DOT: Record<EndpointHealth["status"], string> = {
  probing: "bg-info-dot animate-pulse",
  ok: "bg-ok-dot",
  bad: "bg-bad-dot",
  unknown: "bg-n-400",
};

export function BatchConfiguration({
  wid,
  batch,
  compact = false,
}: {
  wid: string;
  batch: string;
  compact?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<
    components["schemas"]["BatchSnapshotView"] | null
  >(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision explicitly retries the snapshot read.
  useEffect(() => {
    let current = true;
    setSnapshot(null);
    setError("");
    void api.getBatchSnapshot(wid, batch).then(
      (value) => {
        if (current) setSnapshot(value);
      },
      (reason: unknown) => {
        if (current) setError(errorMessage(reason));
      },
    );
    return () => {
      current = false;
    };
  }, [wid, batch, revision]);

  if (error)
    return (
      <div className="flex items-center gap-2 py-2">
        <FormError className="text-t-sm text-bad-ink">{error}</FormError>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setRevision((value) => value + 1)}
        >
          重新读取
        </Button>
      </div>
    );
  if (!snapshot)
    return (
      <p role="status" className="py-2 text-t-sm text-text-4">
        正在读取策略配置
      </p>
    );
  if (compact)
    return (
      <section
        aria-label="策略配置"
        className="flex min-w-0 flex-1 flex-wrap items-center gap-3"
      >
        <EndpointChip endpoint={snapshot.endpoint} />
        {[
          { key: "prompt", value: snapshot.prompt.name },
          ...snapshot.skills
            .slice(0, 5)
            .map((entry) => ({ key: `skill/${entry.name}`, value: entry.name })),
          ...(snapshot.skills.length > 5
            ? [{ key: "more", value: `+${snapshot.skills.length - 5}` }]
            : []),
        ].map(({ key, value }) => (
          <span
            key={key}
            title={value}
            className="inline-flex h-(--h-sm) max-w-full items-center rounded-lg bg-muted px-3 text-t-sm"
          >
            <span className="truncate">{value}</span>
          </span>
        ))}
      </section>
    );
  return (
    <dl className="px-6 pb-3 text-t-sm">
      {Object.entries({
        端点配置: `${snapshot.endpoint.name} · ${snapshot.endpoint.model}`,
        基础提示词: snapshot.prompt.name,
        Skill: snapshot.skills.map((entry) => entry.name).join("、") || "无",
        创建时间: new Date(snapshot.built_at).toLocaleString(),
      }).map(([label, value]) => (
        <div key={label} className="flex gap-3 py-1">
          <dt className="w-[84px] shrink-0 text-text-4">{label}</dt>
          <dd className="min-w-0 break-all text-text-2">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** 顶栏端点章（C1）：带健康状态点——探测中蓝脉冲 / 通绿 / 不通红 / 探测失败灰，title 写明来源。 */
function EndpointChip({
  endpoint,
}: {
  endpoint: components["schemas"]["SnapshotEndpointView"];
}): ReactElement {
  const health = useEndpointHealth(endpoint.name, endpoint);
  const label = `${endpoint.name} · ${endpoint.model}`;
  return (
    <span
      title={`${label} · ${health.message}`}
      className="inline-flex h-(--h-sm) max-w-full items-center gap-2 rounded-lg bg-muted px-3 text-t-sm"
    >
      <span
        aria-hidden
        className={`size-1.5 shrink-0 rounded-full ${HEALTH_DOT[health.status]}`}
      />
      <span className="truncate">{label}</span>
    </span>
  );
}
