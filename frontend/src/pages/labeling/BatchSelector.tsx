import { ChevronDownIcon, FolderIcon, PlusIcon, SettingsIcon } from "lucide-react";
import { useState } from "react";
import type { components } from "../../api-types.gen";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";

export interface WorkdirBatches {
  id: string;
  title: string;
  batches: components["schemas"]["BatchView"][];
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
}

/** 目录是分组，只有启用的批次可被选中；切换时一次传递完整身份。 */
export function BatchSelector({
  workdirs,
  value,
  onChange,
  onSettings,
  onNewStrategy,
}: Props) {
  const [open, setOpen] = useState(false);
  const directory = workdirs.find((entry) => entry.id === value?.workdirId);
  const batch = directory?.batches.find(
    (entry) => entry.id === value?.batchId && entry.active,
  );

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="选择工作目录与批次"
          className="flex h-8.5 w-full min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-3 text-t-md text-foreground hover:bg-accent"
        >
          <FolderIcon className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate text-left font-medium">
            {directory?.title ?? "选择工作目录"}
          </span>
          {batch && (
            <span className="min-w-0 flex-1 truncate text-left text-t-sm text-muted-foreground">
              {batch.name} · {batch.id}
            </span>
          )}
          <ChevronDownIcon className="size-4 shrink-0" />
        </button>
      </DropdownMenuTrigger>
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
                  <button
                    type="button"
                    disabled={!!entry.error}
                    className="flex size-(--h-sm) shrink-0 items-center justify-center rounded-md hover:bg-accent"
                    aria-label={`新增策略 ${entry.title}`}
                    title="新增策略"
                    onClick={() => {
                      setOpen(false);
                      onNewStrategy(entry.id);
                    }}
                  >
                    <PlusIcon className="size-4" />
                  </button>
                )}
                {onSettings && (
                  <button
                    type="button"
                    className="ml-auto flex size-(--h-sm) shrink-0 items-center justify-center rounded-md hover:bg-accent"
                    aria-label={`工作目录设置 ${entry.title}`}
                    title="工作目录设置"
                    onClick={() => {
                      setOpen(false);
                      onSettings(entry.id);
                    }}
                  >
                    <SettingsIcon className="size-4" />
                  </button>
                )}
              </div>
              {entry.error && (
                <p role="alert" className="px-6 py-2 text-t-sm text-bad-ink">
                  {entry.error}
                </p>
              )}
              {!entry.error && active.length === 0 && (
                <p className="px-6 py-2 text-t-sm text-muted-foreground">
                  没有启用的批次
                </p>
              )}
              {active.map((candidate) => (
                <DropdownMenuItem
                  key={candidate.id}
                  className="ml-3 border-l-2 border-border pl-4 text-t-md"
                  aria-current={
                    entry.id === value?.workdirId && candidate.id === value.batchId
                      ? "true"
                      : undefined
                  }
                  onSelect={() => {
                    onChange({ workdirId: entry.id, batchId: candidate.id });
                    setOpen(false);
                  }}
                >
                  <span className="min-w-0 flex-1 truncate">{candidate.name}</span>
                  <span className="text-t-sm text-muted-foreground">
                    {candidate.id}
                  </span>
                </DropdownMenuItem>
              ))}
            </fieldset>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
