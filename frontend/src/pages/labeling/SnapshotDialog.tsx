import { CopyIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorMessage } from "../../api";
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
import { Tip } from "../../components/ui/tooltip";

type Snapshot = components["schemas"]["BatchSnapshotView"];

export function SnapshotDialog({
  wid,
  batch,
  onClose,
}: {
  wid: string;
  batch: string;
  onClose: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [copyError, setCopyError] = useState("");
  const [copied, setCopied] = useState(false);
  const [revision, setRevision] = useState(0);
  // biome-ignore lint/correctness/useExhaustiveDependencies: revision retries the read without mutating stored snapshots.
  useEffect(() => {
    let current = true;
    setSnapshot(null);
    setError("");
    setCopied(false);
    setCopyError("");
    api.getBatchSnapshot(wid, batch).then(
      (value) => {
        if (current) setSnapshot(value);
      },
      (reason) => {
        if (current) setError(errorMessage(reason));
      },
    );
    return () => {
      current = false;
    };
  }, [wid, batch, revision]);

  async function copy() {
    if (!snapshot) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(snapshot, null, 2));
      setCopied(true);
      setCopyError("");
    } catch {
      setCopyError("复制失败，请检查浏览器剪贴板权限。");
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] w-[min(680px,100%)] max-w-none overflow-y-auto">
        <DialogHeader>
          <DialogTitle>策略快照</DialogTitle>
          <DialogDescription>{batch}</DialogDescription>
        </DialogHeader>
        {error ? (
          <div className="flex items-center gap-2">
            <p role="alert" className="text-bad-ink">
              {error}
            </p>
            <Button variant="ghost" size="sm" onClick={() => setRevision((n) => n + 1)}>
              重新读取
            </Button>
          </div>
        ) : !snapshot ? (
          <p role="status">正在读取快照</p>
        ) : (
          <>
            {snapshot.changed && (
              <p
                role="alert"
                className="rounded-md border border-warn-bd bg-warn-bg p-3 text-t-sm text-warn-ink"
              >
                快照哈希与最近一次运行记录不一致，快照文件可能被手动修改。后续跑批将使用当前快照。
              </p>
            )}
            <section className="min-w-0 border-b border-border pb-4">
              <h3 className="mb-2 text-t-md font-medium">端点与生成参数</h3>
              <dl className="flex flex-wrap gap-x-4 gap-y-2 text-t-sm">
                {Object.entries({
                  配置名: snapshot.endpoint.name,
                  模型: snapshot.endpoint.model,
                  api_format: snapshot.endpoint.api_format,
                  ...snapshot.endpoint.request_params,
                  base_url: snapshot.endpoint.base_url,
                }).map(([label, value]) => (
                  <div key={label} className="flex min-w-0 flex-wrap gap-2 break-all">
                    <dt className="text-text-4">{label}</dt>
                    <dd className="text-text-2">
                      {typeof value === "string" ? value : JSON.stringify(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
            {[
              { ...snapshot.prompt, kind: "基础提示词" },
              ...snapshot.skills.map((skill) => ({ ...skill, kind: "启用 Skill" })),
            ].map((entry) => (
              <section
                key={`${entry.kind}/${entry.name}`}
                className="min-w-0 border-b border-border pb-4"
              >
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-t-md font-medium">
                  <span className="break-all">
                    {entry.kind} · {entry.name}
                  </span>
                  <span className="ml-auto break-all text-t-xs font-normal text-text-4">
                    sha256 {entry.sha256}
                  </span>
                </h3>
                <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-sans text-t-sm leading-loose">
                  {entry.body}
                </pre>
              </section>
            ))}
            <p className="break-all text-t-xs text-text-4">
              快照时间 {new Date(snapshot.built_at).toLocaleString()} · 工具版本{" "}
              {snapshot.tool_version} · 快照哈希 {snapshot.sha256}
            </p>
          </>
        )}
        {copyError && (
          <p role="alert" className="text-bad-ink">
            {copyError}
          </p>
        )}
        {copied && (
          <p role="status" className="text-t-sm text-text-3">
            已复制
          </p>
        )}
        <DialogFooter>
          <Tip label="复制快照">
            <Button
              variant="ghost"
              size="icon"
              aria-label="复制快照"
              disabled={!snapshot}
              onClick={() => void copy()}
            >
              <CopyIcon />
            </Button>
          </Tip>
          <Button variant="outline" size="sm" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
