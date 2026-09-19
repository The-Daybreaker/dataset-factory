import { useEffect, useRef, useState } from "react";
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
import { formatBytes } from "../../lib/format";

type Entry = { name: string; size: number; batch?: number; modified_at?: number };

export function CleanupDialog({
  wid,
  kind,
  onClose,
  onCleaned,
}: {
  wid: string;
  kind: "products" | "runs";
  onClose: () => void;
  onCleaned: () => void;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<components["schemas"]["CleanupResult"] | null>(
    null,
  );
  const pending = useRef(false);
  const generation = useRef(0);
  const selectAll = useRef<HTMLInputElement>(null);
  const title = kind === "products" ? "清理无素材产物" : "清理运行记录";

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision retries the preview after a failed read.
  useEffect(() => {
    const token = ++generation.current;
    setLoading(true);
    setEntries([]);
    setSelected(new Set());
    setResult(null);
    setConfirming(false);
    setError("");
    const request =
      kind === "products"
        ? api.previewProductCleanup(wid).then((value) => value.products)
        : api.previewRunCleanup(wid);
    void request
      .then(
        (value) => {
          if (token === generation.current) setEntries(value);
        },
        (reason: unknown) => {
          if (token === generation.current) setError(errorMessage(reason));
        },
      )
      .finally(() => {
        if (token === generation.current) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [wid, kind, revision]);

  useEffect(() => {
    if (selectAll.current) {
      selectAll.current.indeterminate =
        selected.size > 0 && selected.size < entries.length;
    }
  }, [selected.size, entries.length]);

  async function clean() {
    if (pending.current || !confirming || selected.size === 0) return;
    pending.current = true;
    const token = generation.current;
    setBusy(true);
    setError("");
    try {
      const value = await api.cleanupWorkdir(wid, kind, [...selected]);
      if (generation.current !== token) return;
      setResult(value);
      onCleaned();
    } catch (reason) {
      if (generation.current === token) setError(errorMessage(reason));
    } finally {
      pending.current = false;
      if (generation.current === token) setBusy(false);
    }
  }

  const bytes = entries.reduce(
    (total, entry) => total + (selected.has(entry.name) ? entry.size : 0),
    0,
  );
  // 最近一次运行挂时效标记（原型口径）：它是当前产物时效判定与出身的凭据，误删会失去判定依据。
  const latestRunAt =
    kind === "runs" && entries.length > 0
      ? Math.max(...entries.map((entry) => entry.modified_at ?? 0))
      : Number.NaN;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending.current) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {kind === "products" ? "没有素材配对的 txt 产物" : "运行日志与条目记录"}
          </DialogDescription>
        </DialogHeader>
        {loading && <p role="status">正在读取清单</p>}
        {error && (
          <p role="alert" className="text-t-sm text-bad-ink">
            {error}
          </p>
        )}
        {result ? (
          <div role="status" className="space-y-2 text-t-sm">
            <p>已移出 {result.count} 项</p>
            {result.recovery_path && (
              <p className="break-all text-warn-ink">
                暂存目录未清空：{result.recovery_path}
              </p>
            )}
          </div>
        ) : (
          !loading && (
            <>
              <fieldset disabled={busy || confirming} className="min-w-0 space-y-2">
                <label className="flex items-center gap-2 text-t-sm">
                  <input
                    ref={selectAll}
                    type="checkbox"
                    className="cb"
                    checked={entries.length > 0 && selected.size === entries.length}
                    disabled={entries.length === 0}
                    onChange={(event) =>
                      setSelected(
                        event.currentTarget.checked
                          ? new Set(entries.map((entry) => entry.name))
                          : new Set(),
                      )
                    }
                  />
                  全选
                </label>
                <div className="max-h-64 overflow-y-auto border-y border-border">
                  {entries.length === 0 && (
                    <p className="py-3 text-t-sm text-text-4">没有可清理的项目</p>
                  )}
                  {entries.map((entry) => (
                    <label
                      key={entry.name}
                      className="flex items-start gap-3 border-b border-border px-2 py-2 text-t-sm last:border-0 hover:bg-accent"
                    >
                      <input
                        type="checkbox"
                        className="cb mt-1"
                        aria-label={entry.name}
                        checked={selected.has(entry.name)}
                        onChange={(event) => {
                          const checked = event.currentTarget.checked;
                          setSelected((previous) => {
                            const next = new Set(previous);
                            if (checked) next.add(entry.name);
                            else next.delete(entry.name);
                            return next;
                          });
                        }}
                      />
                      <span className="min-w-0 flex-1 break-all">{entry.name}</span>
                      {kind === "runs" && entry.modified_at === latestRunAt && (
                        <span className="inline-flex h-[18px] shrink-0 items-center rounded-sm bg-muted px-2 text-t-xs text-text-3">
                          最近一次 · 当前产物时效判定用
                        </span>
                      )}
                      <span className="shrink-0 text-text-4 tabular-nums">
                        {formatBytes(entry.size, "KiB", 1)}
                        {entry.batch !== undefined ? ` · s${entry.batch}` : ""}
                      </span>
                      {entry.modified_at !== undefined && (
                        <span className="text-text-4">
                          {new Date(entry.modified_at * 1000).toLocaleString()}
                        </span>
                      )}
                    </label>
                  ))}
                </div>
              </fieldset>
              <p className="text-t-sm text-text-3">
                已选 {selected.size} 项 · {formatBytes(bytes, "KiB", 1)}
              </p>
              {confirming && (
                <p className="text-t-sm text-warn-ink">
                  确认删除选中的 {selected.size} 项？
                  {kind === "runs"
                    ? "对应运行日志将无法再查看。素材与打标产物保留。"
                    : "素材和其余产物保留。"}
                </p>
              )}
            </>
          )
        )}
        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            关闭
          </Button>
          {!result && !loading && entries.length === 0 && error && (
            <Button variant="outline" onClick={() => setRevision((value) => value + 1)}>
              重新读取
            </Button>
          )}
          {!result && confirming && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                setError("");
              }}
            >
              返回清单
            </Button>
          )}
          {!result && entries.length > 0 && (
            <Button
              variant="destructive-fill"
              disabled={busy || selected.size === 0}
              onClick={() => (confirming ? void clean() : setConfirming(true))}
            >
              {busy ? "正在清理" : confirming ? "确认清理" : `清理（${selected.size}）`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
