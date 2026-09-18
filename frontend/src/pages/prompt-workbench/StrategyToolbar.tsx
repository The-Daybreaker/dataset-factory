import { ChevronDownIcon, CopyIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { type ReactElement, useEffect, useRef, useState } from "react";
import {
  api,
  type EndpointConfigSummary,
  errorMessage,
  type PromptInfo,
  type SkillInfo,
} from "../../api";
import type { components } from "../../api-types.gen";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";

type Strategy = components["schemas"]["StrategyView"];
type References = Pick<Strategy, "endpoint" | "prompt" | "skills">;

export function StrategyToolbar({
  references,
  prompts,
  skills,
  endpoints,
  locked,
  onOpenSettings,
  onSelect,
}: {
  references: References;
  prompts: PromptInfo[];
  skills: SkillInfo[];
  endpoints: EndpointConfigSummary[];
  locked: boolean;
  onOpenSettings: () => void;
  onSelect: (strategy: Strategy) => Promise<void>;
}): ReactElement {
  const [entries, setEntries] = useState<Strategy[]>([]);
  const [selected, setSelected] = useState<Strategy | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [remove, setRemove] = useState<Strategy | null>(null);
  const [repair, setRepair] = useState<Strategy | null>(null);
  /** 当前库策略被多少批次应用过（copy-on-apply 出身记录，仅提示用）。 */
  const [appliedCount, setAppliedCount] = useState(0);
  const [bindings, setBindings] = useState<References>({
    endpoint: "",
    prompt: "",
    skills: [],
  });
  const mounted = useRef(true);
  const pending = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!selected) {
      setAppliedCount(0);
      return;
    }
    let cancelled = false;
    void api
      .listStrategyReferences(selected.id)
      .then((list) => {
        if (!cancelled) setAppliedCount(list.length);
      })
      .catch(() => {
        if (!cancelled) setAppliedCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void api
      .listStrategies()
      .then((list) => {
        if (!cancelled) {
          setEntries(list);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setEntries([]);
          setError(errorMessage(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const remember = (entry: Strategy): void => {
    setEntries((current) =>
      [...current.filter((item) => item.id !== entry.id), entry].sort((a, b) =>
        a.name.localeCompare(b.name),
      ),
    );
  };
  const operate = async (operation: () => Promise<void>): Promise<void> => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (err) {
      if (mounted.current) setError(errorMessage(err));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const choose = async (entry: Strategy): Promise<void> => {
    if (pending.current || locked || loading) return;
    if (!entry.available) {
      setBindings({
        endpoint: entry.endpoint,
        prompt: entry.prompt,
        skills: entry.skills,
      });
      setRepair(entry);
      setOpen(false);
      return;
    }
    await operate(async () => {
      await onSelect(entry);
      if (!mounted.current) return;
      setSelected(entry);
      setName(entry.name);
      setDescription(entry.description);
      setOpen(false);
    });
  };
  const dirty =
    selected !== null &&
    (name !== selected.name ||
      description !== selected.description ||
      references.endpoint !== selected.endpoint ||
      references.prompt !== selected.prompt ||
      JSON.stringify(references.skills) !== JSON.stringify(selected.skills));
  const switchLocked =
    locked ||
    busy ||
    dirty ||
    (selected === null && (name !== "" || description !== ""));
  /** 当前端点已设置的生成 / 传输参数摘要（原型口径：打标前扫一眼这次会带什么）。 */
  const paramsSummary = (() => {
    const params = endpoints.find(
      (entry) => entry.name === references.endpoint,
    )?.request_params;
    if (!params) return "默认";
    const parts = [
      params.temperature != null ? `temperature ${params.temperature}` : null,
      params.top_p != null ? `top_p ${params.top_p}` : null,
      params.max_tokens != null ? `max_tokens ${params.max_tokens}` : null,
      params.timeout_seconds != null ? `timeout ${params.timeout_seconds}s` : null,
      params.max_retries != null ? `max_retries ${params.max_retries}` : null,
      params.extra_body && Object.keys(params.extra_body).length > 0
        ? `extra_body ${JSON.stringify(params.extra_body)}`
        : null,
    ].filter((part): part is string => part !== null);
    return parts.length > 0 ? parts.join(" · ") : "默认";
  })();
  const save = (): void => {
    void operate(async () => {
      const body = { name: name.trim(), description, ...references };
      const entry = selected
        ? await api.updateStrategy(selected.id, body)
        : await api.createStrategy(body);
      if (!mounted.current) return;
      remember(entry);
      setSelected(entry);
      setName(entry.name);
      setDescription(entry.description);
    });
  };

  return (
    <header className="col-span-full border-b border-border px-6 pt-6 pb-4">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div className="relative flex min-w-0 max-w-full items-center">
          <input
            aria-label="策略名称"
            placeholder="新建策略"
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.currentTarget.value)}
            className="min-w-24 max-w-full field-sizing-content rounded-md border border-transparent bg-transparent py-1 pr-7 pl-2 text-t-2xl font-semibold hover:border-border hover:bg-card focus:border-input"
          />
          <DropdownMenu open={open} onOpenChange={setOpen}>
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute right-0 size-6"
                    aria-label="切换策略"
                    disabled={switchLocked}
                  >
                    <ChevronDownIcon />
                  </Button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent>
                {switchLocked ? "请先保存当前修改" : "切换策略"}
              </TooltipContent>
            </Tooltip>
            <DropdownMenuContent
              align="start"
              className="w-96 max-w-[calc(100vw-32px)] p-1"
            >
              <div className="flex items-center justify-between px-2 py-1 text-t-xs text-text-4">
                <span>策略库 · 共 {entries.length} 条</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="新建策略"
                      disabled={busy || loading || locked}
                      onClick={() => {
                        setSelected(null);
                        setName("");
                        setDescription("");
                        setOpen(false);
                      }}
                    >
                      <PlusIcon />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>新建策略</TooltipContent>
                </Tooltip>
              </div>
              <div className="max-h-80 overflow-y-auto">
                {entries.map((entry) => (
                  <div
                    key={entry.id}
                    className={`group flex items-center gap-1 rounded-md p-2 ${selected?.id === entry.id ? "bg-primary/10" : "hover:bg-accent"}`}
                  >
                    <button
                      type="button"
                      disabled={busy || loading || locked}
                      className={`min-w-0 flex-1 text-left ${entry.available ? "text-text-2" : "text-text-4"}`}
                      onClick={() => void choose(entry)}
                    >
                      <span className="block truncate text-t-md font-medium">
                        {entry.name}
                      </span>
                      <span className="block truncate text-t-xs">
                        {entry.description}
                      </span>
                      {!entry.available && <span className="text-t-xs">引用缺失</span>}
                    </button>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`复制策略 ${entry.name}`}
                          disabled={busy || loading}
                          onClick={() =>
                            void operate(async () => {
                              const copy = await api.copyStrategy(entry.id);
                              if (mounted.current) remember(copy);
                            })
                          }
                        >
                          <CopyIcon />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>复制</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-bad-ink"
                          aria-label={`删除策略 ${entry.name}`}
                          disabled={busy || loading}
                          onClick={() => {
                            setRemove(entry);
                            setOpen(false);
                          }}
                        >
                          <Trash2Icon />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>删除</TooltipContent>
                    </Tooltip>
                  </div>
                ))}
              </div>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <input
          aria-label="策略描述"
          placeholder="描述"
          value={description}
          disabled={busy}
          onChange={(event) => setDescription(event.currentTarget.value)}
          className="min-w-0 basis-full rounded-md border border-transparent bg-transparent px-2 py-1 text-t-md text-text-3 hover:border-border hover:bg-card focus:border-input sm:flex-1 sm:basis-auto"
        />
        {dirty && <span className="text-t-xs text-warn-ink">未保存</span>}
        <Button
          size="sm"
          aria-label="保存策略"
          disabled={
            busy || locked || !name.trim() || !references.prompt || !references.endpoint
          }
          onClick={save}
        >
          保存
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-t-sm">
        <span className="text-text-3">
          端点 <span className="text-text-2">{references.endpoint || "未指定"}</span>
        </span>
        <span aria-hidden="true" className="text-text-4">
          ·
        </span>
        <span className="text-text-3">
          提示词 <span className="text-text-2">{references.prompt || "未指定"}</span>
        </span>
        <span aria-hidden="true" className="text-text-4">
          ·
        </span>
        <span className="min-w-0 text-text-3">
          Skill{" "}
          <span className="text-text-2">{references.skills.join(" · ") || "无"}</span>
        </span>
        <span aria-hidden="true" className="text-text-4">
          ·
        </span>
        <span className="min-w-0 text-text-3">
          高级参数 <span className="text-text-2">{paramsSummary}</span>
        </span>
        <Button variant="accent" size="xs" onClick={onOpenSettings}>
          前往设置
        </Button>
      </div>
      {selected && appliedCount > 0 && (
        <p className="mt-1 text-t-xs text-text-3">
          已被 {appliedCount} 个批次应用（批次持有创建时的副本，改库不影响它们）
        </p>
      )}
      {error && !remove && !repair && (
        <Alert variant="destructive" className="mt-2">
          <AlertDescription>{error}</AlertDescription>
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              void operate(async () => {
                const list = await api.listStrategies();
                if (mounted.current) setEntries(list);
              })
            }
          >
            刷新策略库
          </Button>
        </Alert>
      )}
      <Dialog
        open={remove !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setRemove(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除策略「{remove?.name}」？</DialogTitle>
            <DialogDescription>
              删除库中的组合清单，已应用到工作目录的批次与产物保持不变。
            </DialogDescription>
          </DialogHeader>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setRemove(null)}>
              取消
            </Button>
            <Button
              variant="destructive-fill"
              disabled={busy}
              onClick={() =>
                void operate(async () => {
                  if (!remove) return;
                  await api.deleteStrategy(remove.id);
                  if (!mounted.current) return;
                  setEntries((current) =>
                    current.filter((entry) => entry.id !== remove.id),
                  );
                  if (selected?.id === remove.id) {
                    setSelected(null);
                    setName("");
                    setDescription("");
                  }
                  setRemove(null);
                })
              }
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={repair !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setRepair(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重新指定「{repair?.name}」</DialogTitle>
            <DialogDescription>{repair?.missing_refs.join("；")}</DialogDescription>
          </DialogHeader>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <label className="grid gap-2 text-t-sm">
            端点
            <select
              aria-label="重新指定端点"
              value={bindings.endpoint}
              disabled={busy}
              onChange={(event) =>
                setBindings({ ...bindings, endpoint: event.currentTarget.value })
              }
              className="h-(--h-lg) rounded-md border border-input bg-card px-3"
            >
              <option value="">选择端点</option>
              {!endpoints.some((entry) => entry.name === bindings.endpoint) &&
                bindings.endpoint && (
                  <option value={bindings.endpoint} disabled>
                    {bindings.endpoint}（缺失）
                  </option>
                )}
              {endpoints.map((entry) => (
                <option key={entry.name}>{entry.name}</option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-t-sm">
            提示词
            <select
              aria-label="重新指定提示词"
              value={bindings.prompt}
              disabled={busy}
              onChange={(event) =>
                setBindings({ ...bindings, prompt: event.currentTarget.value })
              }
              className="h-(--h-lg) rounded-md border border-input bg-card px-3"
            >
              <option value="">选择提示词</option>
              {!prompts.some((entry) => entry.name === bindings.prompt) &&
                bindings.prompt && (
                  <option value={bindings.prompt} disabled>
                    {bindings.prompt}（缺失）
                  </option>
                )}
              {prompts.map((entry) => (
                <option key={entry.name}>{entry.name}</option>
              ))}
            </select>
          </label>
          <fieldset disabled={busy} className="grid gap-2">
            <legend className="mb-2 text-t-sm">Skill</legend>
            {Array.from(
              new Set([
                ...bindings.skills,
                ...skills.filter((entry) => entry.enabled).map((entry) => entry.name),
              ]),
            ).map((skill) => (
              <label key={skill} className="flex items-center gap-2 text-t-sm">
                <input
                  type="checkbox"
                  checked={bindings.skills.includes(skill)}
                  onChange={(event) =>
                    setBindings({
                      ...bindings,
                      skills: event.currentTarget.checked
                        ? [...bindings.skills, skill]
                        : bindings.skills.filter((name) => name !== skill),
                    })
                  }
                />
                {skill}
                {skills.some((entry) => entry.name === skill && entry.enabled)
                  ? ""
                  : "（缺失或停用）"}
              </label>
            ))}
          </fieldset>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setRepair(null)}>
              取消
            </Button>
            <Button
              disabled={busy || !bindings.endpoint || !bindings.prompt}
              onClick={() =>
                void operate(async () => {
                  if (!repair) return;
                  const entry = await api.rebindStrategy(repair.id, bindings);
                  if (!mounted.current) return;
                  remember(entry);
                  setRepair(null);
                })
              }
            >
              重新指定
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </header>
  );
}
