import { ChevronDownIcon, RefreshCwIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
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
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Input } from "../../components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

type Catalog = {
  strategies: Awaited<ReturnType<typeof api.listStrategies>>;
  endpoints: Awaited<ReturnType<typeof api.listEndpoints>>;
  prompts: Awaited<ReturnType<typeof api.listPrompts>>;
  skills: Awaited<ReturnType<typeof api.listSkills>>;
};

export function NewStrategyDialog({
  wid,
  title,
  onClose,
  onCreated,
}: {
  wid: string;
  title: string;
  onClose: () => void;
  onCreated: (batch: components["schemas"]["BatchView"]) => void;
}) {
  const id = useId();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [revision, setRevision] = useState(0);
  const [library, setLibrary] = useState("scratch");
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [prompt, setPrompt] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const completed = useRef(false);
  const lifetime = useRef<object | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision retries catalog loading without losing form inputs.
  useEffect(() => {
    const current = {};
    lifetime.current = current;
    setError("");
    void Promise.all([
      api.listStrategies(),
      api.listEndpoints(),
      api.listPrompts(),
      api.listSkills(),
    ]).then(
      ([strategies, endpoints, prompts, availableSkills]) => {
        if (lifetime.current !== current) return;
        setCatalog({
          strategies,
          endpoints,
          prompts,
          skills: availableSkills.filter((entry) => entry.enabled),
        });
        setEndpoint(
          (value) =>
            value ||
            endpoints.find((entry) => entry.is_active)?.name ||
            endpoints[0]?.name ||
            "",
        );
        setPrompt((value) => value || prompts[0]?.name || "");
      },
      (reason: unknown) => {
        if (lifetime.current === current) setError(errorMessage(reason));
      },
    );
    return () => {
      lifetime.current = null;
    };
  }, [revision]);

  const source = catalog?.strategies.find((entry) => entry.id === library);
  const valid =
    !!catalog &&
    (library === "scratch"
      ? !!name.trim() && !!endpoint && !!prompt
      : !!source?.available);

  async function create() {
    if (pending.current || completed.current || !valid) return;
    const current = lifetime.current;
    pending.current = true;
    setBusy(true);
    setError("");
    let batch: components["schemas"]["BatchView"];
    try {
      batch = await api.createBatch(
        wid,
        library === "scratch"
          ? { type: "scratch", name: name.trim(), endpoint, prompt, skills }
          : { type: "library", id: library, name: name.trim() || null },
      );
    } catch (reason) {
      if (lifetime.current === current) {
        setError(errorMessage(reason));
        setBusy(false);
      }
      pending.current = false;
      return;
    }
    completed.current = true;
    if (lifetime.current === current) onCreated(batch);
  }

  function picker(
    label: string,
    value: string,
    change: (value: string) => void,
    options: { value: string; label: string; disabled?: boolean }[],
    disabled = false,
  ) {
    return (
      <div className="flex min-w-0 items-center gap-3">
        <span className="w-20 shrink-0 text-t-sm text-text-4">{label}</span>
        <Select
          value={value}
          onValueChange={change}
          disabled={busy || !catalog || disabled}
        >
          <SelectTrigger aria-label={label} className="min-w-0 flex-1">
            <SelectValue placeholder="请选择" />
          </SelectTrigger>
          <SelectContent>
            {options.map((entry) => (
              <SelectItem
                key={entry.value}
                value={entry.value}
                disabled={entry.disabled}
              >
                {entry.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending.current) onClose();
      }}
    >
      <DialogContent className="max-h-[90vh] max-w-[600px] overflow-y-auto p-4">
        <DialogHeader className="flex-row flex-wrap items-baseline gap-3 pr-8">
          <DialogTitle>新增策略</DialogTitle>
          <DialogDescription className="min-w-0 break-all">
            工作目录：{title}
          </DialogDescription>
        </DialogHeader>
        {picker(
          "策略来源",
          library,
          (value) => {
            setLibrary(value);
            setName(
              catalog?.strategies.find((entry) => entry.id === value)?.name ?? "",
            );
          },
          [
            { value: "scratch", label: "从零配置" },
            ...(catalog?.strategies ?? []).map((entry) => ({
              value: entry.id,
              label: `${entry.name}${entry.available ? "" : " · 引用缺失"}`,
              disabled: !entry.available,
            })),
          ],
        )}
        <div className="space-y-2">
          <label htmlFor={`${id}-name`} className="text-t-sm text-text-4">
            策略名
          </label>
          <Input
            id={`${id}-name`}
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.currentTarget.value)}
          />
        </div>
        <fieldset className="min-w-0 space-y-2" disabled={busy}>
          <legend className="mb-2 text-t-sm text-text-4">策略内容</legend>
          {picker(
            "端点配置",
            source?.endpoint ?? endpoint,
            setEndpoint,
            (catalog?.endpoints ?? []).map((entry) => ({
              value: entry.name,
              label: `${entry.name} · ${entry.model}`,
            })),
            library !== "scratch",
          )}
          {picker(
            "基础提示词",
            source?.prompt ?? prompt,
            setPrompt,
            (catalog?.prompts ?? []).map((entry) => ({
              value: entry.name,
              label: entry.name,
            })),
            library !== "scratch",
          )}
          <div className="flex min-w-0 items-center gap-3">
            <span className="w-20 shrink-0 text-t-sm text-text-4">Skill</span>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  className="h-(--h-lg) min-w-0 flex-1 justify-between"
                  aria-label="选择 Skill"
                  disabled={busy || !catalog || library !== "scratch"}
                >
                  <span className="truncate">
                    {(source?.skills ?? skills).join(" · ") || "无"}
                  </span>
                  <ChevronDownIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="max-h-64 max-w-80 overflow-auto">
                {catalog?.skills.map((entry) => (
                  <DropdownMenuCheckboxItem
                    key={entry.name}
                    checked={skills.includes(entry.name)}
                    onSelect={(event) => event.preventDefault()}
                    onCheckedChange={(checked) =>
                      setSkills((previous) =>
                        checked
                          ? [...previous, entry.name]
                          : previous.filter((name) => name !== entry.name),
                      )
                    }
                  >
                    {entry.name}
                  </DropdownMenuCheckboxItem>
                ))}
                {catalog?.skills.length === 0 && (
                  <p className="p-2 text-t-sm text-text-3">没有可用 Skill</p>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </fieldset>
        {error && (
          <p role="alert" className="text-t-sm text-bad-ink">
            {error}
          </p>
        )}
        {!catalog && !error && (
          <p role="status" className="text-t-sm text-text-3">
            正在读取策略配置
          </p>
        )}
        <DialogFooter>
          {!catalog && error && (
            <Button
              variant="ghost"
              size="sm"
              aria-label="重试读取配置"
              onClick={() => setRevision((value) => value + 1)}
            >
              <RefreshCwIcon />
            </Button>
          )}
          <Button variant="ghost" size="sm" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button size="sm" disabled={!valid || busy} onClick={() => void create()}>
            {busy ? "正在创建" : "创建策略"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
