import { RefreshCwIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useReducer, useState } from "react";
import { api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";
import { Tip } from "../../components/ui/tooltip";

/** 端点健康探测结果（§5.3：点的每个颜色都要有数据来源）。 */
type EndpointHealth = {
  status: "probing" | "ok" | "bad" | "unknown";
  message: string;
};

/** 缓存条目带时间戳：探测结果会过期，不许一路灰着（B2，2026-09-21 审计）。 */
type CachedHealth = EndpointHealth & { at: number };

/** 会话级探测缓存：按端点名记一次结果 + 时刻，切回视图不重复探测。 */
const healthCache = new Map<string, CachedHealth>();
const healthGen = new Map<string, number>();
const healthListeners = new Set<() => void>();

/** 探测结果的有效期：过期后视图挂载即自动重探（60 秒，B2 定案）。 */
const HEALTH_TTL_MS = 60_000;

function notifyAll(): void {
  for (const notify of healthListeners) notify();
}

function setHealth(name: string, health: EndpointHealth): void {
  healthCache.set(name, { ...health, at: Date.now() });
  notifyAll();
}

/** 「重新探测」入口（B2）：清缓存 + 唤醒全部订阅者重跑探测。 */
function reprobe(name: string): void {
  healthCache.delete(name);
  healthGen.set(name, (healthGen.get(name) ?? 0) + 1);
  notifyAll();
}

function useEndpointHealth(
  name: string,
  target:
    | {
        id?: string | null;
        base_url: string;
        model: string;
        api_format: string;
      }
    | undefined,
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
  const generation = healthGen.get(name) ?? 0;
  const cached = healthCache.get(name);
  // biome-ignore lint/correctness/useExhaustiveDependencies: cached 的读取是有意的快照判定——重探由 reprobe 的 generation 变化与 TTL 过期驱动，不随每次缓存写入重跑。
  useEffect(() => {
    if (!baseUrl || !model) return;
    // 有未过期缓存就不重探；过期（或刚被 reprobe 清掉）则自动重跑。
    if (cached !== undefined && Date.now() - cached.at < HEALTH_TTL_MS) return;
    setHealth(name, { status: "probing", message: "正在探测端点连通性…" });
    void api
      .testEndpoint({
        base_url: baseUrl,
        model,
        api_format: apiFormat ?? "",
        id: target?.id ?? null,
      })
      .then((result) =>
        setHealth(
          name,
          result.ok
            ? {
                status: "ok",
                message: `端点连通 · 首字 ${(result.latency_ms / 1000).toFixed(2)} 秒`,
              }
            : { status: "bad", message: result.message },
        ),
      )
      .catch((reason: unknown) =>
        setHealth(name, {
          // unknown ≠ bad：bad 是「后端判定端点不通」，unknown 是「前端自己没问到」
          // （超时 / 断网）——灰点说真话，不再让用户误会成端点坏了。
          status: "unknown",
          message: `探测请求未完成（${errorMessage(reason)}）——这是探测本身的问题，不代表端点不通；可点右侧按钮重试。`,
        }),
      );
    // generation 变化（reprobe）必须重跑；cached 变化由 notify 驱动重渲染后判定。
  }, [name, baseUrl, model, apiFormat, generation]);
  return healthCache.get(name) ?? { status: "probing", message: "正在探测端点连通性…" };
}

const HEALTH_DOT: Record<EndpointHealth["status"], string> = {
  // L14（2026-09-21 审计 / ui-spec :150）：.dot--run 的呼吸是全站唯一持续动画——
  // 探测中改静态蓝点；「正在探测」的信息由文案承载，不靠动画。
  probing: "bg-info-dot",
  ok: "bg-ok-dot",
  bad: "bg-bad-dot",
  unknown: "bg-n-400",
};

/** base_url → 可读服务商名（V9）：`api.siliconflow.cn` → SiliconFlow；推不出回退配置名。 */
function readableProviderName(baseUrl: string, fallback: string): string {
  const KNOWN: Record<string, string> = {
    "api.siliconflow.cn": "SiliconFlow",
    "api.openai.com": "OpenAI",
    "dashscope.aliyuncs.com": "DashScope",
    "api.deepseek.com": "DeepSeek",
    "open.bigmodel.cn": "智谱",
  };
  try {
    const host = new URL(baseUrl).hostname;
    if (KNOWN[host] !== undefined) return KNOWN[host];
    const brand = host.replace(/^api\./, "").split(".")[0] ?? "";
    return brand === "" ? fallback : brand.charAt(0).toUpperCase() + brand.slice(1);
  } catch {
    return fallback;
  }
}

/** 模型名去服务商命名空间（V9）：`Qwen/Qwen3.5-4B` → `Qwen3.5-4B`。 */
function shortModelName(model: string): string {
  const index = model.indexOf("/");
  return index === -1 ? model : model.slice(index + 1);
}

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
          <Tip key={key} label={value}>
            <span className="inline-flex h-(--h-sm) max-w-full cursor-help items-center rounded-lg bg-muted px-3 text-t-sm">
              <span className="truncate">{value}</span>
            </span>
          </Tip>
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

/** 顶栏端点章（C1）：带健康状态点 + 重新探测入口；title 留全量信息（V9）。 */
function EndpointChip({
  endpoint,
}: {
  endpoint: components["schemas"]["SnapshotEndpointView"];
}): ReactElement {
  const health = useEndpointHealth(endpoint.name, endpoint);
  const label = `${readableProviderName(endpoint.base_url, endpoint.name)} · ${shortModelName(endpoint.model)}`;
  const fullTitle = `${endpoint.name} · ${endpoint.model} · ${health.message}`;
  return (
    <Tip label={fullTitle}>
      <span className="inline-flex h-(--h-sm) max-w-full cursor-help items-center gap-2 rounded-lg bg-muted px-3 text-t-sm">
        <span
          aria-hidden
          className={`size-1.5 shrink-0 rounded-full ${HEALTH_DOT[health.status]}`}
        />
        <span className="truncate">{label}</span>
        {(health.status === "bad" || health.status === "unknown") && (
          <button
            type="button"
            aria-label="重新探测"
            onClick={(event) => {
              event.stopPropagation();
              reprobe(endpoint.name);
            }}
          >
            <RefreshCwIcon className="size-3 text-text-4 hover:text-text-2" />
          </button>
        )}
      </span>
    </Tip>
  );
}
