import { ChevronDownIcon, CopyIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { type ReactElement, useEffect, useRef, useState } from "react";
import {
  api,
  type EndpointConfigSummary,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  Tip,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../../components/ui/tooltip";
import { reportError } from "../../lib/feedback";
import { formatChars } from "../../lib/format";
import {
  isStrategySelection,
  readStoredJson,
  WORKBENCH_STRATEGY_KEY,
  writeStoredJson,
} from "../../lib/ui-storage";

type Strategy = components["schemas"]["StrategyView"];
type References = Pick<Strategy, "endpoint" | "prompt" | "skills">;

/** 悬停气泡里的出身三参数（§5.7 键值行：键用弱字、值用次字，键列全站统一 84px）。 */
function StrategyRefs({ endpoint, prompt, skills }: References): ReactElement {
  return (
    <span className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2 gap-y-1 text-left">
      <span className="text-text-4">端点</span>
      <span className="min-w-0 wrap-anywhere text-text-2">{endpoint}</span>
      <span className="text-text-4">提示词</span>
      <span className="min-w-0 wrap-anywhere text-text-2">{prompt}</span>
      <span className="text-text-4">Skill</span>
      <span className="min-w-0 wrap-anywhere text-text-2">
        {skills.length === 0 ? "无" : skills.join("、")}
      </span>
    </span>
  );
}

export function StrategyToolbar({
  references,
  prompts,
  skills,
  endpoints,
  locked,
  onSelect,
  onNewStrategy,
  onStrategySaved,
}: {
  references: References;
  prompts: PromptInfo[];
  skills: SkillInfo[];
  endpoints: EndpointConfigSummary[];
  locked: boolean;
  onSelect: (strategy: Strategy) => Promise<void>;
  /** 点「新建策略」时回调：换桶语义，工作域据此进 __new__ 桶（三期 v3）。 */
  onNewStrategy: () => void;
  /** 新策略落库成功后回调：工作域把当前草稿会话改挂到新策略 id（三期 v3）。 */
  onStrategySaved: (strategy: Strategy) => void;
}): ReactElement {
  const [entries, setEntries] = useState<Strategy[]>([]);
  const [selected, setSelected] = useState<Strategy | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [open, setOpen] = useState(false);
  // L9：锁定状态下点了非当前项 → 在列表内给出原因（不弹全局 toast，就地可见）。
  const [switchNotice, setSwitchNotice] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [remove, setRemove] = useState<Strategy | null>(null);
  const [repair, setRepair] = useState<Strategy | null>(null);
  const [bindings, setBindings] = useState<References>({
    endpoint: "",
    prompt: "",
    skills: [],
  });
  const mounted = useRef(true);
  const pending = useRef(false);
  // 策略选中镜像的写入门闩：挂载首帧不写（防止空白态冲掉既有镜像），任何真实
  // 交互（新建 / 改名 / 选中等）后才落盘。见下方持久化与恢复两个 effect。
  const selectionTouched = useRef(false);
  // 启动恢复的一次性门闩：镜像认领 + 签名认领只试一轮，之后交给用户。
  const selectionResolved = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // 策略库列表：挂载即拉（三期 v2——boot 要恢复「我在哪个策略里」，不能等首次
  // 打开下拉），此后每次打开下拉重拉一份；关闭后跳过（保留当前清单），取消链随
  // 依赖变化级联（旧响应迟到不覆盖新清单，见 StrictMode 用例）。
  const firstLoad = useRef(true);
  useEffect(() => {
    if (!open && !firstLoad.current) return;
    firstLoad.current = false;
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
          setError(reportError(err) ?? "");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // 策略选中镜像落盘：id + 名称/描述缓冲 + 签名四元组（会话域对账与重启恢复都认它）。
  // 新建态（无选中）落 null——新建草稿缓冲不跨重启；门闩未开不写，防止挂载首帧
  // 用空白冲掉既有镜像。
  useEffect(() => {
    if (!selectionTouched.current) return;
    writeStoredJson(
      WORKBENCH_STRATEGY_KEY,
      selected === null
        ? null
        : {
            id: selected.id,
            name,
            description,
            endpoint: selected.endpoint,
            prompt: selected.prompt,
            skills: selected.skills,
          },
    );
  }, [selected, name, description]);

  // 策略选中的启动恢复（三期 v3，归属即身份）：镜像键三分支——
  // ① 键存在且指向策略 → 按 id 对号入座（名称/描述用镜像缓冲；认领写回的镜像
  //    名称为空，从库里补全并落回完整镜像）；指向已删除的策略则不认领。
  // ② 键存在且为 null → 用户停在新建策略态（或会话域认领过并定案），不认领。
  // ③ 键不存在 → 会话域（ChatSessionProvider）是认领的唯一发起方，这里只轮询
  //    等它落盘（最多约 3s）；超时仍无键就停在新建态。本组件 boot 不发请求。
  useEffect(() => {
    if (selectionResolved.current || selectionTouched.current) return;
    if (loading || entries.length === 0) return;
    if (selected !== null || name !== "" || description !== "") {
      selectionResolved.current = true;
      return;
    }
    const restoreFrom = (mirrorId: string | null): void => {
      if (mirrorId === null) {
        selectionResolved.current = true;
        return;
      }
      const byId = entries.find((entry) => entry.id === mirrorId);
      if (byId !== undefined) {
        const mirror = readStoredJson(WORKBENCH_STRATEGY_KEY, isStrategySelection);
        setSelected(byId);
        setName(mirror?.name || byId.name);
        setDescription(mirror?.description || byId.description);
        // 补全认领写回的骨架镜像（空名称）——落回完整版，下次重启直接用。
        if (mirror !== null && mirror.name === "") selectionTouched.current = true;
      }
      // 镜像指向已删除的策略：不认领（用户明确选过它，它没了就空着）。
      selectionResolved.current = true;
    };
    let attempts = 0;
    let timer = 0;
    const tick = (): void => {
      const raw = localStorage.getItem(WORKBENCH_STRATEGY_KEY);
      if (raw !== null) {
        const mirror = readStoredJson(WORKBENCH_STRATEGY_KEY, isStrategySelection);
        restoreFrom(mirror?.id ?? null);
        return;
      }
      attempts += 1;
      if (attempts >= 10) {
        selectionResolved.current = true;
        return;
      }
      timer = window.setTimeout(tick, 300);
    };
    tick();
    return () => {
      window.clearTimeout(timer);
    };
  }, [entries, loading, selected, name, description]);

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
    selectionTouched.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (err) {
      if (mounted.current) setError(reportError(err) ?? "");
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
  // 有东西可保存 = 已选策略有改动，或正在编辑一份尚未落库的新策略（新建流程不能被「无改动」禁用）。
  const actionable =
    dirty || (selected === null && (name !== "" || description !== ""));
  const switchLocked =
    locked ||
    busy ||
    dirty ||
    (selected === null && (name !== "" || description !== ""));
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
      // 新落库的策略：把当前草稿桶的会话改挂到它名下（三期 v3——保存前聊的
      // 就是「这个策略」的对话，落库即认领；改存量策略不动归属）。
      if (selected === null) {
        onStrategySaved(entry);
      }
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
            onChange={(event) => {
              selectionTouched.current = true;
              setName(event.currentTarget.value);
            }}
            className="min-w-24 max-w-full field-sizing-content rounded-md border border-transparent bg-transparent py-1 pr-7 pl-2 text-t-2xl font-semibold hover:border-border hover:bg-card focus:border-input"
          />
          <DropdownMenu open={open} onOpenChange={setOpen}>
            <Tooltip>
              <TooltipTrigger asChild>
                {/* L9（2026-09-21 审计 / 原型 :1084-1093）：未保存时列表照常打开、
                    选中非当前项时才提示——「点不动但看不见有什么」改成「点得动但会告诉你」。 */}
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute right-0 size-6"
                    aria-label="切换策略"
                  >
                    <ChevronDownIcon />
                  </Button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent>切换策略</TooltipContent>
            </Tooltip>
            <DropdownMenuContent
              align="start"
              className="w-96 max-w-[calc(100vw-32px)] p-1"
            >
              {switchNotice !== null && (
                <p
                  role="status"
                  className="rounded-md bg-warn-bg px-2 py-1.5 text-t-xs text-warn-ink"
                >
                  {switchNotice}
                </p>
              )}
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
                        selectionTouched.current = true;
                        selectionResolved.current = true;
                        setSelected(null);
                        setName("");
                        setDescription("");
                        setOpen(false);
                        // 新建 = 离开当前配置（换底座）：会话清空，与切策略同语义。
                        onNewStrategy();
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
                    <Tip
                      label={
                        <StrategyRefs
                          endpoint={entry.endpoint}
                          prompt={entry.prompt}
                          skills={entry.skills}
                        />
                      }
                    >
                      <span className="min-w-0 flex-1">
                        <button
                          type="button"
                          disabled={busy || loading}
                          className={`w-full min-w-0 text-left ${entry.available ? "text-text-2" : "text-text-4"}`}
                          onClick={() => {
                            if (switchLocked && selected?.id !== entry.id) {
                              // L9：锁着的时候点了要说清为什么（原型 :1093 文案）。
                              setSwitchNotice(
                                "当前有未保存的改动——先点「保存」，才能切换策略。",
                              );
                              return;
                            }
                            setSwitchNotice(null);
                            void choose(entry);
                          }}
                        >
                          <span className="flex items-baseline gap-2">
                            <span className="block min-w-0 truncate text-t-md font-medium">
                              {entry.name}
                            </span>
                            <span className="shrink-0 text-t-xs text-n-500">
                              {formatChars(entry.body_chars)}
                            </span>
                          </span>
                          <span className="block truncate text-t-xs text-n-500">
                            {entry.description}
                          </span>
                          {!entry.available && (
                            <span className="text-t-xs text-bad-ink">引用缺失</span>
                          )}
                        </button>
                      </span>
                    </Tip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-sm"
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
                          size="icon-sm"
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
          onChange={(event) => {
            selectionTouched.current = true;
            setDescription(event.currentTarget.value);
          }}
          className="min-w-24 max-w-full field-sizing-content rounded-md border border-transparent bg-transparent px-2 py-1 text-t-md text-text-3 hover:border-border hover:bg-card focus:border-input"
        />
        <span className="hidden flex-1 sm:block" />
        <Tip label={actionable ? "" : "没有未保存的修改"}>
          <Button
            size="sm"
            variant={actionable ? "default" : "ghost"}
            aria-label="保存策略"
            disabled={
              busy ||
              locked ||
              !actionable ||
              !name.trim() ||
              !references.prompt ||
              !references.endpoint
            }
            onClick={save}
          >
            保存
          </Button>
        </Tip>
      </div>
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
          <div className="grid gap-2 text-t-sm">
            端点
            {/* L12（2026-09-21 审计）：全站唯一的原生 select 破口 → Radix Select。 */}
            <Select
              value={bindings.endpoint}
              disabled={busy}
              onValueChange={(value) => setBindings({ ...bindings, endpoint: value })}
            >
              <SelectTrigger aria-label="重新指定端点" className="h-(--h-lg)">
                <SelectValue placeholder="选择端点" />
              </SelectTrigger>
              <SelectContent>
                {!endpoints.some((entry) => entry.name === bindings.endpoint) &&
                  bindings.endpoint !== "" && (
                    <SelectItem value={bindings.endpoint} disabled>
                      {bindings.endpoint}（缺失）
                    </SelectItem>
                  )}
                {endpoints.map((entry) => (
                  <SelectItem key={entry.name} value={entry.name}>
                    {entry.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2 text-t-sm">
            提示词
            <Select
              value={bindings.prompt}
              disabled={busy}
              onValueChange={(value) => setBindings({ ...bindings, prompt: value })}
            >
              <SelectTrigger aria-label="重新指定提示词" className="h-(--h-lg)">
                <SelectValue placeholder="选择提示词" />
              </SelectTrigger>
              <SelectContent>
                {!prompts.some((entry) => entry.name === bindings.prompt) &&
                  bindings.prompt !== "" && (
                    <SelectItem value={bindings.prompt} disabled>
                      {bindings.prompt}（缺失）
                    </SelectItem>
                  )}
                {prompts.map((entry) => (
                  <SelectItem key={entry.name} value={entry.name}>
                    {entry.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
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
                  className="cb"
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
