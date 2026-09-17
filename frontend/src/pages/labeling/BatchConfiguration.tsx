import { useEffect, useState } from "react";
import { api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { Button } from "../../components/ui/button";

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
        <p role="alert" className="text-t-sm text-bad-ink">
          {error}
        </p>
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
        {[
          {
            key: "endpoint",
            value: `${snapshot.endpoint.name} · ${snapshot.endpoint.model}`,
          },
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
