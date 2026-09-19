import {
  ArrowUpIcon,
  FileIcon,
  FolderIcon,
  FolderPlusIcon,
  PencilIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "../api";
import type { components } from "../api-types.gen";
import { FormError } from "../components/form-error";
import { formatBytes } from "../lib/format";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Tip } from "./ui/tooltip";

type Listing = components["schemas"]["DirectoryListing"];
type Entry = components["schemas"]["DirectoryEntry"];
interface Props {
  initialPath?: string;
  files?: boolean;
  suffixes?: string[];
  browseOnly?: boolean;
  allowRename?: boolean;
  allowCreate?: boolean;
  onClose: () => void;
  onSelect: (path: string, listing: Listing) => void;
}

export function DirectoryPicker({
  initialPath = "",
  files = false,
  suffixes = [],
  browseOnly = false,
  allowRename = false,
  allowCreate = false,
  onClose,
  onSelect,
}: Props) {
  const [location, setLocation] = useState(initialPath);
  const [input, setInput] = useState(initialPath);
  const [listing, setListing] = useState<Listing | null>(null);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [renaming, setRenaming] = useState<Entry | null>(null);
  const [creating, setCreating] = useState(false);
  const [canOpen, setCanOpen] = useState(false);
  const [opening, setOpening] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState("");
  const [renamingBusy, setRenamingBusy] = useState(false);
  const [renameTask, setRenameTask] = useState<string | null>(null);
  const [pollRevision, setPollRevision] = useState(0);
  const [pollFailed, setPollFailed] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const cancelPending = useRef(false);
  const renameInputId = useId();
  const mutationPending = useRef(false);
  const mounted = useRef(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const inputEdits = useRef(0);
  const suffixKey = JSON.stringify(suffixes);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!browseOnly) return;
    let current = true;
    void api
      .filesystemCapabilities()
      .then((value) => {
        if (current) setCanOpen(value.open_in_file_manager === true);
      })
      .catch(() => {
        if (current) setCanOpen(false);
      });
    return () => {
      current = false;
    };
  }, [browseOnly]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: pollRevision retries the existing task after a failed query.
  useEffect(() => {
    if (!renameTask) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setPollFailed(false);
    setRenameError("");
    async function poll() {
      try {
        const task = await api.getTask(renameTask as string);
        if (!current) return;
        if (task.id !== renameTask) throw new Error("任务响应与当前重命名不一致");
        if (task.status === "running") {
          timer = setTimeout(() => void poll(), 1000);
          return;
        }
        if (task.status === "succeeded") {
          const result = task.result;
          if (
            !result ||
            typeof result !== "object" ||
            !("path" in result) ||
            typeof result.path !== "string" ||
            !result.path.trim()
          )
            throw new Error("重命名结果格式无效");
          setRenaming(null);
          setLocation(result.path);
          setRevision((value) => value + 1);
          if ("cleanup_pending" in result && result.cleanup_pending === true)
            setRenameError("重命名已完成，旧位置尚未清理，请在工作目录设置中处理");
        } else setRenameError(task.error || "重命名未完成");
        setRenameTask(null);
        mutationPending.current = false;
        setRenamingBusy(false);
      } catch (reason) {
        if (current) {
          if (reason instanceof ApiError && reason.status === 404) {
            setRenameTask(null);
            setRenaming(null);
            setRenamingBusy(false);
            mutationPending.current = false;
            setRevision((value) => value + 1);
            setRenameError("重命名任务已丢失，请刷新目录确认实际位置后重试");
          } else {
            setRenameError(errorMessage(reason));
            setPollFailed(true);
          }
        }
      }
    }
    void poll();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [renameTask, pollRevision]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision permits retrying the same path after a failed read.
  useEffect(() => {
    let current = true;
    const editsAtRequest = inputEdits.current;
    setLoading(true);
    setSelected(null);
    setListing(null);
    setError("");
    const filter: string[] = JSON.parse(suffixKey);
    void api
      .listDirectory(location, files || browseOnly, hidden, browseOnly ? [] : filter)
      .then((value) => {
        if (!current) return;
        setListing(value);
        if (inputEdits.current === editsAtRequest) setInput(value.path);
      })
      .catch((reason: unknown) => {
        if (current) setError(errorMessage(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [location, files, hidden, browseOnly, suffixKey, revision]);

  function navigate(path: string) {
    if (mutationPending.current) return;
    setRenaming(null);
    setCreating(false);
    setLocation(path);
    setRevision((value) => value + 1);
  }

  const breadcrumbs = breadcrumbsFor(listing);
  const chosen = selected?.path ?? listing?.path;
  const title = browseOnly ? "浏览目录" : files ? "选择目录或文件" : "选择目录";

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !mutationPending.current && onClose()}
    >
      <DialogContent className="w-[calc(100%-2rem)] max-w-[620px] gap-0 p-4">
        <div className="mb-2 flex flex-wrap items-center gap-3 pr-8">
          <DialogTitle className="text-t-xl font-medium">{title}</DialogTitle>
          <DialogDescription className="min-w-0 break-all text-t-sm">
            {listing ? `当前后端：${listing.hostname} · ${listing.system}` : "当前后端"}
          </DialogDescription>
        </div>
        <div className="mb-2 flex min-w-0 items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={loading || renamingBusy || !listing?.parent}
            onClick={() => listing?.parent && navigate(listing.parent)}
          >
            <ArrowUpIcon />
            上一级
          </Button>
          <nav
            aria-label="目录路径"
            className="flex min-w-0 overflow-x-auto text-t-sm text-text-3"
          >
            {breadcrumbs.map((crumb, index) => (
              <button
                key={crumb.path}
                type="button"
                className="shrink-0 rounded-sm px-1 py-0.5 hover:bg-n-100"
                disabled={loading || renamingBusy || index === breadcrumbs.length - 1}
                onClick={() => navigate(crumb.path)}
              >
                {crumb.label}
              </button>
            ))}
          </nav>
        </div>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (mutationPending.current) return;
            setRenaming(null);
            navigate(input.trim());
          }}
        >
          <Input
            aria-label="服务器路径"
            className="min-w-0 flex-1"
            value={input}
            onChange={(event) => {
              inputEdits.current += 1;
              setInput(event.currentTarget.value);
            }}
          />
          <Button
            variant="outline"
            size="lg"
            type="submit"
            disabled={loading || renamingBusy}
          >
            跳转
          </Button>
        </form>
        <label className="mt-2 flex items-center gap-2 text-t-sm text-text-3">
          <input
            type="checkbox"
            className="cb"
            checked={hidden}
            disabled={renamingBusy}
            onChange={(event) => setHidden(event.currentTarget.checked)}
          />
          显示隐藏项
        </label>
        {allowCreate && !browseOnly && (
          <Tip label="新建目录">
            <Button
              variant="ghost"
              size="icon"
              aria-label="新建目录"
              disabled={loading || renamingBusy || !listing}
              onClick={() => {
                setRenaming(null);
                setCreating(true);
                setRenameValue("");
                setRenameError("");
              }}
            >
              <FolderPlusIcon />
            </Button>
          </Tip>
        )}
        {loading && (
          <p role="status" className="py-3 text-t-sm text-text-4">
            正在读取目录
          </p>
        )}
        {error && (
          <FormError className="py-3 text-t-sm text-bad-ink">{error}</FormError>
        )}
        {listing && (
          <section
            className="mt-3 max-h-[296px] overflow-y-auto rounded-lg border border-border bg-card"
            aria-label="目录内容"
          >
            {listing.entries.length === 0 && (
              <p className="px-3 py-2 text-t-sm text-text-4">没有匹配的项目</p>
            )}
            {listing.entries.map((entry) => (
              <div
                key={entry.path}
                className={`flex min-w-0 flex-wrap items-center gap-3 border-b border-border/60 px-3 py-2 last:border-0 ${selected?.path === entry.path ? "bg-primary/8 text-primary" : "hover:bg-accent"}`}
              >
                {browseOnly ? (
                  <span className="flex min-w-0 flex-1 items-center gap-3 text-t-md">
                    <FolderOrFile entry={entry} />
                    <span className="break-all">{entry.name}</span>
                  </span>
                ) : (
                  <>
                    <button
                      type="button"
                      disabled={renamingBusy}
                      className="flex min-w-0 flex-1 items-center gap-3 text-left text-t-md"
                      onClick={() =>
                        entry.kind === "directory"
                          ? navigate(entry.path)
                          : setSelected(entry)
                      }
                    >
                      <FolderOrFile entry={entry} />
                      <span className="break-all">{entry.name}</span>
                    </button>
                    {allowRename && entry.kind === "directory" && (
                      <Tip label={`重命名 ${entry.name}`}>
                        <Button
                          variant="ghost"
                          size="icon"
                          disabled={renamingBusy}
                          aria-label={`重命名 ${entry.name}`}
                          onClick={() => {
                            setCreating(false);
                            setRenaming(entry);
                            setRenameValue(entry.name);
                            setRenameError("");
                          }}
                        >
                          <PencilIcon />
                        </Button>
                      </Tip>
                    )}
                  </>
                )}
                <span className="ml-auto text-t-sm text-text-3 tabular-nums">
                  {entry.size === null ? "" : `${formatBytes(entry.size, "KiB", 1)} · `}
                  {new Date(entry.modified_at).toLocaleString()}
                </span>
              </div>
            ))}
          </section>
        )}
        {!!listing?.unavailable_count && (
          <p role="status" className="mt-2 text-t-sm text-warn-ink">
            {listing.unavailable_count} 项无法读取
          </p>
        )}
        {(renaming || creating) && (
          <form
            className="mt-3 border-t border-border pt-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (mutationPending.current) return;
              const newName = renameValue.trim();
              if (!newName) return;
              if (creating && listing) {
                mutationPending.current = true;
                setRenamingBusy(true);
                setRenameError("");
                void api
                  .createDirectory(listing.path, newName)
                  .then((value) => {
                    if (!mounted.current) return;
                    if (!value.path?.trim()) throw new Error("创建目录结果无效");
                    setCreating(false);
                    setLocation(value.path);
                    setRevision((value) => value + 1);
                  })
                  .catch((reason: unknown) => {
                    if (mounted.current) setRenameError(errorMessage(reason));
                  })
                  .finally(() => {
                    mutationPending.current = false;
                    if (mounted.current) setRenamingBusy(false);
                  });
                return;
              }
              if (!renaming || !listing || newName === renaming.name) {
                setRenaming(null);
                return;
              }
              setRenamingBusy(true);
              mutationPending.current = true;
              setRenameError("");
              api
                .renameDirectory(renaming.path, newName)
                .then((value) => {
                  if (!mounted.current) return;
                  if (!value.task_id?.trim()) throw new Error("重命名任务编号无效");
                  setRenameTask(value.task_id);
                })
                .catch((reason: unknown) => {
                  mutationPending.current = false;
                  if (!mounted.current) return;
                  setRenameError(errorMessage(reason));
                  setRenamingBusy(false);
                });
            }}
          >
            <label
              htmlFor={renameInputId}
              className="flex items-center gap-2 text-t-sm"
            >
              <span className="shrink-0">{creating ? "目录名称" : "重命名"}</span>
              <Input
                id={renameInputId}
                disabled={renamingBusy}
                className="min-w-0 flex-1"
                value={renameValue}
                onChange={(event) => setRenameValue(event.currentTarget.value)}
              />
            </label>
            <div className="mt-3 flex items-center justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={renamingBusy}
                onClick={() => {
                  setRenaming(null);
                  setCreating(false);
                }}
              >
                取消
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={renamingBusy || !renameValue.trim()}
              >
                {creating ? "创建" : "保存"}
              </Button>
            </div>
          </form>
        )}
        {renameError && (
          <FormError className="mt-2 break-all text-t-sm text-bad-ink">
            {renameError}
          </FormError>
        )}
        {renamingBusy && (
          <p role="status" className="mt-2 text-t-sm">
            {creating ? "正在创建目录" : "正在重命名"}
          </p>
        )}
        {pollFailed && (
          <Button
            variant="outline"
            onClick={() => setPollRevision((value) => value + 1)}
          >
            重新查询
          </Button>
        )}
        {renameTask && (
          <Button
            variant="outline"
            size="sm"
            disabled={cancelling}
            onClick={() => {
              if (cancelPending.current) return;
              cancelPending.current = true;
              setCancelling(true);
              void api
                .cancelTask(renameTask)
                .then(() => {
                  if (mounted.current) setPollRevision((value) => value + 1);
                })
                .catch((reason: unknown) => {
                  if (mounted.current) {
                    setRenameError(errorMessage(reason));
                    setPollFailed(true);
                  }
                })
                .finally(() => {
                  cancelPending.current = false;
                  if (mounted.current) setCancelling(false);
                });
            }}
          >
            取消重命名
          </Button>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-3">
          <span className="min-w-0 flex-1 break-all text-t-sm text-text-3">
            {browseOnly
              ? `共 ${listing?.entries.length ?? 0} 项 · 只读`
              : `将采用 ${chosen ?? ""}`}
          </span>
          {browseOnly && canOpen && (
            <Tip label="在系统文件资源管理器中打开">
              <Button
                variant="ghost"
                size="icon"
                aria-label="在系统文件资源管理器中打开"
                disabled={opening || loading || !listing}
                onClick={() => {
                  if (!listing || opening) return;
                  setOpening(true);
                  setRenameError("");
                  void api
                    .openDirectory(listing.path)
                    .catch((reason: unknown) => {
                      if (mounted.current) setRenameError(errorMessage(reason));
                    })
                    .finally(() => {
                      if (mounted.current) setOpening(false);
                    });
                }}
              >
                <FolderIcon />
              </Button>
            </Tip>
          )}
          <Button
            variant={browseOnly ? "outline" : "ghost"}
            disabled={renamingBusy}
            onClick={onClose}
          >
            {browseOnly ? "关闭" : "取消"}
          </Button>
          {!browseOnly && (
            <Button
              disabled={
                loading || renamingBusy || !!renaming || creating || !chosen || !!error
              }
              onClick={() => chosen && listing && onSelect(chosen, listing)}
            >
              {selected ? "选择此文件" : "选择此目录"}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function FolderOrFile({ entry }: { entry: Entry }) {
  const Icon = entry.kind === "directory" ? FolderIcon : FileIcon;
  return <Icon aria-hidden="true" className="size-4 shrink-0" />;
}

function breadcrumbsFor(listing: Listing | null) {
  if (!listing) return [];
  const windows = listing.system === "Windows";
  const separator = windows ? "\\" : "/";
  const path = windows ? listing.path.replaceAll("/", "\\") : listing.path;
  const root = windows
    ? (path.match(/^(?:\\\\[^\\]+\\[^\\]+\\?|[A-Za-z]:\\)/)?.[0] ?? "")
    : "/";
  if (!root) return [{ label: path, path }];
  const parts = path.slice(root.length).split(separator).filter(Boolean);
  const crumbs = [{ label: root, path: root }];
  let current = root;
  for (const part of parts) {
    current = `${current.endsWith(separator) ? current : current + separator}${part}`;
    crumbs.push({ label: part, path: current });
  }
  return crumbs;
}
