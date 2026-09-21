import {
  CheckIcon,
  ChevronDownIcon,
  FolderIcon,
  PlusIcon,
  SettingsIcon,
} from "lucide-react";
import { useState } from "react";
import type { components } from "../../api-types.gen";
import { FormError } from "../../components/form-error";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import {
  Tip,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../../components/ui/tooltip";

export interface WorkdirBatches {
  id: string;
  title: string;
  batches: components["schemas"]["BatchView"][];
  /** 工作目录的规范绝对路径——胶囊被截断时靠它做悬停兜底（原型明写的用意）。 */
  path?: string;
  error?: string;
}

export interface BatchSelection {
  workdirId: string;
  batchId: string;
}

interface Props {
  workdirs: readonly WorkdirBatches[];
  value: BatchSelection | null;
  onChange: (value: BatchSelection) => void;
  onSettings?: (wid: string) => void;
  onNewStrategy?: (wid: string) => void;
  /** 当前批次的运行状态（跑批中 / 已完成 / 已中断 / 失败），无运行记录时为 null。 */
  runState?: string | null;
}

/**
 * 运行状态章的配方（顶栏胶囊第三段）。
 *
 * 取值沿用原型实测：已完成 = ink 实底 + `--on-ink` 字（暗色随 n-0 翻深，与
 * ui-spec 5.2「状态章实底彩字」一致）；已中断 = 中性浅底 + 次级文字（原型实测
 * `rgb(242,244,247)` 即本仓的 `--muted`）；跑批中 = 信息蓝实底。
 */
const RUN_STATE_BADGES: Record<string, { text: string; tone: string }> = {
  running: { text: "跑批中", tone: "bg-info-ink text-on-ink" },
  pending: { text: "跑批中", tone: "bg-info-ink text-on-ink" },
  completed: { text: "已完成", tone: "bg-ok-ink text-on-ink" },
  interrupted: { text: "已中断", tone: "bg-muted text-text-3" },
  failed: { text: "失败", tone: "bg-bad-ink text-on-ink" },
};

/** 目录是分组，只有启用的批次可被选中；切换时一次传递完整身份。 */
export function BatchSelector({
  workdirs,
  value,
  onChange,
  onSettings,
  onNewStrategy,
  runState,
}: Props) {
  const [open, setOpen] = useState(false);
  const directory = workdirs.find((entry) => entry.id === value?.workdirId);
  const batch = directory?.batches.find(
    (entry) => entry.id === value?.batchId && entry.active,
  );
  const badge = batch && runState ? RUN_STATE_BADGES[runState] : undefined;
  // V14：全站提示走自研气泡（ui-spec :343）——完整路径悬停可查，不再用原生 title。
  const fullLocation = directory?.path
    ? `${directory.path}${batch ? ` / ${batch.name} · ${batch.id}` : ""}`
    : "";

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      {/* V14：完整路径悬停可查（自研气泡）。Tooltip 在外、DropdownMenuTrigger 在内——
          两个 asChild 触发器链式克隆同一个按钮（PromptWorkbench 同款已验证模式），
          不能用 Tip 包住触发器：Tip 不透传 ref 会把 asChild 链打断、菜单打不开。 */}
      <TooltipProvider delayDuration={320}>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label="选择工作目录与批次"
                className="flex h-8.5 w-full min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-3 text-t-md text-foreground hover:bg-accent"
              >
                <span className="min-w-0 flex-1 truncate text-left font-medium">
                  {directory?.title ?? "选择工作目录"}
                </span>
                {batch && (
                  <span className="min-w-0 flex-1 truncate text-left text-t-sm text-muted-foreground">
                    {batch.name} · {batch.id}
                  </span>
                )}
                {badge && (
                  <span
                    className={`inline-flex h-4.5 shrink-0 items-center rounded-full px-2 text-t-xs font-medium ${badge.tone}`}
                  >
                    {badge.text}
                  </span>
                )}
                <ChevronDownIcon className="size-4 shrink-0" />
              </button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent>{fullLocation}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DropdownMenuContent align="start" className="max-h-80 w-80 overflow-y-auto">
        {workdirs.length === 0 && (
          <p className="px-3 py-2 text-t-sm text-muted-foreground">还没有工作目录</p>
        )}
        {workdirs.map((entry) => {
          const active = entry.batches.filter((candidate) => candidate.active);
          return (
            <fieldset key={entry.id} aria-label={entry.title}>
              <div className="flex items-center gap-2 px-3 py-2 text-t-md font-medium">
                <FolderIcon className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{entry.title}</span>
                {onNewStrategy && (
                  <Tip label="新增策略">
                    <button
                      type="button"
                      disabled={!!entry.error}
                      className="flex size-(--h-sm) shrink-0 items-center justify-center rounded-md hover:bg-accent"
                      aria-label={`新增策略 ${entry.title}`}
                      onClick={() => {
                        setOpen(false);
                        onNewStrategy(entry.id);
                      }}
                    >
                      <PlusIcon className="size-4" />
                    </button>
                  </Tip>
                )}
                {onSettings && (
                  <Tip label="工作目录设置">
                    <button
                      type="button"
                      className="ml-auto flex size-(--h-sm) shrink-0 items-center justify-center rounded-md hover:bg-accent"
                      aria-label={`工作目录设置 ${entry.title}`}
                      onClick={() => {
                        setOpen(false);
                        onSettings(entry.id);
                      }}
                    >
                      <SettingsIcon className="size-4" />
                    </button>
                  </Tip>
                )}
              </div>
              {entry.error && (
                <FormError className="px-6 py-2 text-t-sm text-bad-ink">
                  {entry.error}
                </FormError>
              )}
              {!entry.error && active.length === 0 && (
                <p className="px-6 py-2 text-t-sm text-muted-foreground">
                  没有启用的批次
                </p>
              )}
              {active.map((candidate) => {
                const isCurrent =
                  entry.id === value?.workdirId && candidate.id === value.batchId;
                const badge =
                  candidate.run_status != null
                    ? RUN_STATE_BADGES[candidate.run_status]
                    : undefined;
                return (
                  <DropdownMenuItem
                    key={candidate.id}
                    className="ml-3 border-l-2 border-border pl-4 text-t-md"
                    aria-current={isCurrent ? "true" : undefined}
                    onSelect={() => {
                      onChange({ workdirId: entry.id, batchId: candidate.id });
                      setOpen(false);
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">{candidate.name}</span>
                    {badge && (
                      <span
                        className={`inline-flex h-4.5 shrink-0 items-center rounded-full px-2 text-t-xs font-medium ${badge.tone}`}
                      >
                        {badge.text}
                      </span>
                    )}
                    {candidate.run_done !== null && candidate.run_total !== null && (
                      <span className="shrink-0 text-t-xs text-text-4 tabular-nums">
                        {candidate.run_done} / {candidate.run_total}
                      </span>
                    )}
                    <span className="shrink-0 text-t-sm text-muted-foreground">
                      {candidate.id}
                    </span>
                    {isCurrent && (
                      <CheckIcon
                        data-testid="batch-is-current"
                        aria-hidden="true"
                        className="size-4 shrink-0 text-ok-ink"
                      />
                    )}
                  </DropdownMenuItem>
                );
              })}
            </fieldset>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
