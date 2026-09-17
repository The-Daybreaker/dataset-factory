import { ArrowLeftIcon, FolderIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import {
  ApiError,
  api,
  type EndpointConfigSummary,
  errorMessage,
  type PromptInfo,
  type SkillInfo,
  type TaskView,
} from "../../api";
import type { components } from "../../api-types.gen";
import { DirectoryPicker } from "../../components/DirectoryPicker";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import type { BatchSelection } from "./BatchSelector";
import { ImportMaterialsDialog } from "./ImportMaterialsDialog";
import { parseImportReport } from "./import-report";

type Workdir = components["schemas"]["WorkdirInfo"];
type Batch = components["schemas"]["BatchView"];
interface Props {
  onBack: () => void;
  onCreated: (selection: BatchSelection) => void;
}

export function NewBatchForm({ onBack, onCreated }: Props) {
  const id = useId();
  const [mode, setMode] = useState<"copy" | "inplace">("copy");
  const [path, setPath] = useState("");
  const [source, setSource] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [library, setLibrary] = useState("scratch");
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [prompt, setPrompt] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [strategies, setStrategies] = useState<components["schemas"]["StrategyView"][]>(
    [],
  );
  const [endpoints, setEndpoints] = useState<EndpointConfigSummary[]>([]);
  const [prompts, setPrompts] = useState<PromptInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [workdir, setWorkdir] = useState<Workdir | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [importDone, setImportDone] = useState(false);
  const rejectedImports = useRef<{ name: string; reason: string }[]>([]);
  const [unimported, setUnimported] = useState<
    { name: string; reason: string | null | undefined }[] | null
  >(null);
  const [importOpen, setImportOpen] = useState(false);
  const [directoryField, setDirectoryField] = useState<"source" | "path" | null>(null);
  const mounted = useRef(false);
  const pending = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const wake = useRef<(() => void) | undefined>(undefined);

  useEffect(() => {
    let current = true;
    mounted.current = true;
    void Promise.all([
      api.listStrategies(),
      api.listEndpoints(),
      api.listPrompts(),
      api.listSkills(),
    ])
      .then(([libraryItems, endpointItems, promptItems, skillItems]) => {
        if (!current) return;
        setStrategies(libraryItems);
        setEndpoints(endpointItems);
        setPrompts(promptItems);
        setSkills(skillItems.filter((skill) => skill.enabled));
        setEndpoint(
          endpointItems.find((entry) => entry.is_active)?.name ??
            endpointItems[0]?.name ??
            "",
        );
        setPrompt(promptItems[0]?.name ?? "");
        setLoaded(true);
      })
      .catch((reason: unknown) => {
        if (current) setError(errorMessage(reason));
      });
    return () => {
      current = false;
      mounted.current = false;
      clearTimeout(timer.current);
      wake.current?.();
    };
  }, []);

  async function run(skipConfirmation = false) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      let directory = workdir;
      let importTask = taskId;
      if (!directory) {
        setProgress("正在登记工作目录");
        const accepted = await api.createWorkdir({
          path: path.trim(),
          source: mode === "copy" ? source.trim() : null,
          title: "",
        });
        if (!mounted.current) return;
        directory = accepted.workdir;
        importTask = accepted.task_id;
        setWorkdir(directory);
        setTaskId(importTask);
      }
      if (!importDone && !importTask) {
        const accepted = await api.importMaterials(directory.id, {
          source: mode === "copy" ? source.trim() : null,
        });
        if (!mounted.current) return;
        importTask = accepted.task_id;
        setTaskId(importTask);
      }
      if (!importDone && importTask) {
        while (mounted.current) {
          const task: TaskView = await api
            .getTask(importTask)
            .catch((reason: unknown) => {
              if (
                mounted.current &&
                reason instanceof ApiError &&
                reason.status === 404
              ) {
                setTaskId(null);
                throw new Error("上次任务已丢失（服务重启过），可重新执行");
              }
              throw reason;
            });
          if (!mounted.current) return;
          if (task.id !== importTask) throw new Error("导入任务响应不一致");
          setProgress(`正在导入 · ${Math.round(task.progress * 100)}%`);
          if (task.status === "succeeded") {
            const report = parseImportReport(task.result);
            rejectedImports.current = report.rejected;
            setProgress(`导入完成 · 新增 ${report.imported.length} 项`);
            setImportDone(true);
            break;
          }
          if (task.status !== "running") {
            setTaskId(null);
            throw new Error(task.error || "导入未完成，可重新执行");
          }
          await new Promise<void>((resolve) => {
            wake.current = resolve;
            timer.current = setTimeout(resolve, 2000);
          });
        }
      }
      if (!mounted.current) return;
      let createdBatch = batch;
      if (!createdBatch) {
        setProgress("正在创建批次");
        createdBatch = await api.createBatch(
          directory.id,
          library === "scratch"
            ? {
                type: "scratch",
                name: name.trim(),
                endpoint,
                prompt,
                skills: selectedSkills,
              }
            : { type: "library", id: library, name: name.trim() || null },
        );
        if (!mounted.current) return;
        setBatch(createdBatch);
      }
      if (!skipConfirmation) {
        const view = await api.listItems(directory.id, createdBatch.id);
        if (!mounted.current) return;
        const registered = new Set(
          Object.entries(view.groups)
            .filter(([group]) => group !== "unimported")
            .flatMap(([, rows]) => rows.map((row) => row.name)),
        );
        const omitted = new Map(
          rejectedImports.current
            .filter((row) => !registered.has(row.name))
            .map((row) => [row.name, row]),
        );
        for (const row of view.groups.unimported ?? [])
          omitted.set(row.name, { name: row.name, reason: row.reason ?? "未登记" });
        if (omitted.size) {
          setUnimported([...omitted.values()]);
          return;
        }
      }
      setProgress("正在启动");
      await api.startRun(directory.id, createdBatch.id, "full");
      if (mounted.current)
        onCreated({ workdirId: directory.id, batchId: createdBatch.id });
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const valid =
    loaded &&
    !!path.trim() &&
    (mode === "copy" ? !!source.trim() : acknowledged) &&
    (library !== "scratch" || (!!name.trim() && !!endpoint && !!prompt));
  const picker = (
    label: string,
    value: string,
    change: (value: string) => void,
    options: { value: string; label: string; disabled?: boolean }[],
  ) => (
    <div className="space-y-2">
      <span className="text-t-sm text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={change} disabled={busy || !!batch}>
        <SelectTrigger aria-label={label}>
          <SelectValue placeholder="请选择" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              disabled={option.disabled}
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <section aria-label="新建跑批" className="h-full overflow-auto px-6 py-4">
      {directoryField && (
        <DirectoryPicker
          initialPath={directoryField === "source" ? source : path}
          onClose={() => setDirectoryField(null)}
          onSelect={(value) => {
            if (directoryField === "source") setSource(value);
            else setPath(value);
            setDirectoryField(null);
          }}
        />
      )}
      {workdir && importOpen && (
        <ImportMaterialsDialog
          wid={workdir.id}
          initialMode={mode}
          initialSource={source.trim()}
          onClose={() => setImportOpen(false)}
          onImported={() => {
            setUnimported(null);
          }}
        />
      )}
      <div className="mx-auto max-w-2xl space-y-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            aria-label="返回打标页"
            disabled={busy}
            onClick={onBack}
          >
            <ArrowLeftIcon />
          </Button>
          <h1 className="text-t-2xl font-semibold">新建跑批</h1>
        </div>
        <fieldset disabled={busy || !!workdir} className="space-y-4">
          <legend className="mb-3 text-t-sm text-muted-foreground">素材来源</legend>
          <div className="flex gap-4">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`${id}-mode`}
                checked={mode === "copy"}
                onChange={() => setMode("copy")}
              />
              复制导入
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`${id}-mode`}
                checked={mode === "inplace"}
                onChange={() => setMode("inplace")}
              />
              就地采用
            </label>
          </div>
          {mode === "copy" && (
            <div className="space-y-2">
              <label htmlFor={`${id}-source`}>来源目录</label>
              <div className="flex items-center gap-2">
                <Input
                  id={`${id}-source`}
                  value={source}
                  onChange={(event) => setSource(event.currentTarget.value)}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon-lg"
                  aria-label="选择来源目录"
                  title="选择来源目录"
                  onClick={() => setDirectoryField("source")}
                >
                  <FolderIcon />
                </Button>
              </div>
            </div>
          )}
          <div className="space-y-2">
            <label htmlFor={`${id}-path`}>工作目录</label>
            <div className="flex items-center gap-2">
              <Input
                id={`${id}-path`}
                value={path}
                onChange={(event) => setPath(event.currentTarget.value)}
              />
              <Button
                type="button"
                variant="outline"
                size="icon-lg"
                aria-label="选择工作目录"
                title="选择工作目录"
                onClick={() => setDirectoryField("path")}
              >
                <FolderIcon />
              </Button>
            </div>
          </div>
          {mode === "inplace" && (
            <label className="flex items-start gap-2 rounded-md border border-warn-bd bg-warn-bg p-3 text-t-sm text-warn-ink">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.currentTarget.checked)}
              />
              确认就地采用：工具将写入 .dsf 与产物 txt，删除工作目录会连素材一起删除。
            </label>
          )}
        </fieldset>
        {picker(
          "策略来源",
          library,
          (value) => {
            setLibrary(value);
            setName(strategies.find((entry) => entry.id === value)?.name ?? "");
          },
          [
            { value: "scratch", label: "从零配置" },
            ...strategies.map((entry) => ({
              value: entry.id,
              label: `${entry.name}${entry.available ? "" : " · 引用缺失"}`,
              disabled: !entry.available,
            })),
          ],
        )}
        <div className="space-y-2">
          <label htmlFor={`${id}-name`}>策略名</label>
          <Input
            id={`${id}-name`}
            value={name}
            disabled={busy || !!batch}
            onChange={(event) => setName(event.currentTarget.value)}
          />
        </div>
        {library === "scratch" && (
          <>
            {picker(
              "端点配置",
              endpoint,
              setEndpoint,
              endpoints.map((entry) => ({
                value: entry.name,
                label: `${entry.name} · ${entry.model}`,
              })),
            )}
            {picker(
              "基础提示词",
              prompt,
              setPrompt,
              prompts.map((entry) => ({ value: entry.name, label: entry.name })),
            )}
            <fieldset disabled={busy || !!batch} className="flex flex-wrap gap-3">
              <legend className="mb-2 text-t-sm text-muted-foreground">Skill</legend>
              {skills.map((skill) => (
                <label key={skill.name} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selectedSkills.includes(skill.name)}
                    onChange={(event) => {
                      const checked = event.currentTarget.checked;
                      setSelectedSkills((previous) =>
                        checked
                          ? [...previous, skill.name]
                          : previous.filter((name) => name !== skill.name),
                      );
                    }}
                  />
                  {skill.name}
                </label>
              ))}
            </fieldset>
          </>
        )}
        {error && !unimported && (
          <p role="alert" className="text-bad-ink">
            {error}
          </p>
        )}
        {progress && (
          <p role="status" className="text-t-sm text-muted-foreground">
            {progress}
          </p>
        )}
        <Dialog
          open={!!unimported && !importOpen}
          onOpenChange={(open) => !open && !busy && setUnimported(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>开始打标前确认</DialogTitle>
              <DialogDescription>
                有 {unimported?.length ?? 0} 个素材未登记，本次不会打标。
              </DialogDescription>
            </DialogHeader>
            <ul className="max-h-48 overflow-auto text-t-sm" aria-label="未导入素材">
              {unimported?.map((row) => (
                <li
                  key={row.name}
                  className="flex flex-wrap gap-2 border-b border-border/60 py-2 last:border-0"
                >
                  <span className="break-all font-medium">{row.name}</span>
                  <span className="break-words text-muted-foreground">
                    {row.reason}
                  </span>
                </li>
              ))}
            </ul>
            {error && (
              <p role="alert" className="text-t-sm text-bad-ink">
                {error}
              </p>
            )}
            <DialogFooter>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => setImportOpen(true)}
              >
                先去导入
              </Button>
              <Button size="sm" disabled={busy} onClick={() => void run(true)}>
                仍要开始
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {!unimported && (
          <div className="flex justify-end border-t border-border pt-4">
            <Button disabled={!valid || busy} onClick={() => void run()}>
              {busy ? "正在处理" : "开始打标"}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}
