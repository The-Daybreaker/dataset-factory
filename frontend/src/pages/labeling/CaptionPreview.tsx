import { useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "../../api";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import { Button } from "../../components/ui/button";
import { Tip } from "../../components/ui/tooltip";

interface Props {
  wid: string;
  batch: string;
  row: components["schemas"]["ItemRowView"];
  onRetryChange: (items: string[]) => void;
  batches?: readonly components["schemas"]["BatchView"][];
}

function ComparisonCaption({
  wid,
  batch,
  item,
}: {
  wid: string;
  batch: string;
  item: string;
}) {
  const [text, setText] = useState("正在读取");
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    setText("正在读取");
    setError("");
    void api
      .readCaption(wid, batch, item)
      .then((value) => {
        if (current) setText(value || "暂无产物");
      })
      .catch((reason: unknown) => {
        if (!current) return;
        setText("暂无产物");
        if (!(reason instanceof ApiError && reason.status === 404))
          setError(errorMessage(reason));
      });
    return () => {
      current = false;
    };
  }, [wid, batch, item]);
  return (
    <div className="p-3 whitespace-pre-wrap break-words text-t-md leading-loose">
      {error ? <FormError className="text-bad-ink">{error}</FormError> : text}
    </div>
  );
}

/** 只读产物与名单编辑分离；重试成功后由父视图同步两处名单标记。 */
export function CaptionPreview({
  wid,
  batch,
  row,
  onRetryChange,
  batches = [],
}: Props) {
  const [caption, setCaption] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const generation = useRef(0);
  const comparisonIdentity = useRef("");
  const [comparison, setComparison] = useState<string[]>([]);
  const available = batches.filter((entry) => entry.active && entry.id !== batch);
  const compared = comparison.filter((id) =>
    available.some((entry) => entry.id === id),
  );

  useEffect(() => {
    generation.current += 1;
    let current = true;
    setCaption("");
    setError("");
    setLoading(true);
    setSaving(false);
    const identity = `${wid}/${batch}/${row.item}`;
    if (comparisonIdentity.current !== identity) {
      comparisonIdentity.current = identity;
      setComparison([]);
    }
    void api
      .readCaption(wid, batch, row.item)
      .then((text) => {
        if (current) setCaption(text);
      })
      .catch((reason: unknown) => {
        if (current && !(reason instanceof ApiError && reason.status === 404)) {
          setError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
      generation.current += 1;
    };
  }, [wid, batch, row]);

  async function toggleRetry() {
    if (saving) return;
    const requestGeneration = generation.current;
    setSaving(true);
    setError("");
    try {
      const result = row.in_retry
        ? await api.removeRetryItem(wid, batch, row.item)
        : await api.addRetryItems(wid, batch, [row.item]);
      if (generation.current === requestGeneration) onRetryChange(result.items);
    } catch (reason) {
      if (generation.current === requestGeneration) setError(errorMessage(reason));
    } finally {
      if (generation.current === requestGeneration) setSaving(false);
    }
  }

  return (
    <section className="mt-4 min-w-0" aria-label="产物预览">
      <div className="mb-3 flex items-center gap-3">
        <h3 className="text-t-md font-medium">已产出 txt · {batch}</h3>
        <Tip
          label={
            saving
              ? "正在保存"
              : !row.in_retry && !row.can_retry
                ? "不可加入重试——排队中无需重试、缺失要先补回素材、格式类失败要先解决格式问题"
                : ""
          }
        >
          <Button
            className="ml-auto"
            variant={row.in_retry ? "outline" : "default"}
            size="sm"
            disabled={saving || (!row.in_retry && !row.can_retry)}
            onClick={() => void toggleRetry()}
          >
            {saving ? "正在保存" : row.in_retry ? "移出重试" : "加入重试"}
          </Button>
        </Tip>
      </div>
      {!!available.length && (
        <section
          className="mb-2 ml-auto flex max-w-[304px] justify-end gap-2 overflow-x-auto pb-2 [scrollbar-width:thin]"
          aria-label="策略对比"
        >
          {[
            {
              id: batch,
              name: batches.find((entry) => entry.id === batch)?.name ?? batch,
            },
            ...available,
          ].map((entry) => {
            const primary = entry.id === batch;
            const selected = primary || compared.includes(entry.id);
            return (
              <Tip
                key={entry.id}
                label={
                  primary
                    ? "当前策略固定为第一栏"
                    : !selected && compared.length >= 2
                      ? "同时最多对比三套策略"
                      : entry.name
                }
              >
                <button
                  type="button"
                  aria-pressed={selected}
                  disabled={primary || (!selected && compared.length >= 2)}
                  onClick={() =>
                    setComparison((previous) =>
                      previous.includes(entry.id)
                        ? previous.filter((id) => id !== entry.id)
                        : [...previous, entry.id],
                    )
                  }
                  className={`h-(--h-xs) min-w-[88px] shrink-0 rounded-full border px-4 text-t-sm ${primary ? "border-primary/45 bg-primary/10 font-medium text-primary" : selected ? "border-border bg-card text-foreground" : "border-border bg-secondary text-text-3"}`}
                >
                  {entry.name} · {entry.id}
                </button>
              </Tip>
            );
          })}
        </section>
      )}
      {error && <FormError className="mb-3 text-t-sm text-bad-ink">{error}</FormError>}
      <div
        className="grid gap-3"
        style={{
          gridTemplateColumns: `repeat(${compared.length + 1}, minmax(0, 1fr))`,
        }}
      >
        <section
          className={
            compared.length
              ? "min-w-0 overflow-hidden rounded-lg border border-primary/50"
              : "min-w-0"
          }
          aria-label={`产物 ${batch}`}
        >
          {!!compared.length && (
            <h4 className="border-b border-border px-3 py-2 text-t-sm text-text-3">
              {batches.find((entry) => entry.id === batch)?.name ?? batch} · {batch}
            </h4>
          )}
          <div
            className="whitespace-pre-wrap break-words rounded-md bg-muted p-4 text-t-md leading-loose"
            aria-busy={loading}
          >
            {/* A3 显示层兜底：老产物可能带前导空行，展示前去掉（产物文件本身不动）。 */}
            {loading ? "正在读取" : caption.trim() || "暂无产物"}
          </div>
        </section>
        {compared.map((id) => (
          <section
            key={id}
            className="min-w-0 overflow-hidden rounded-lg border border-border"
            aria-label={`产物 ${id}`}
          >
            <h4 className="border-b border-border px-3 py-2 text-t-sm text-text-3">
              {batches.find((entry) => entry.id === id)?.name ?? id} · {id}
            </h4>
            <ComparisonCaption wid={wid} batch={id} item={row.item} />
          </section>
        ))}
      </div>
    </section>
  );
}
