/** 能力 · 技能（列表 + 详情双栏，含包内容预览与三种导入方式）。 */
import {
  CopyIcon,
  FileTextIcon,
  FolderOpenIcon,
  ImageIcon,
  SearchIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { SkillFileInfo, SkillImportResponse, SkillInfo } from "../../api";
import { api, errorMessage } from "../../api";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
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
import { Switch } from "../../components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";
import type { Feedback } from "./types";

/** 注入正文字符数（SKILL.md + references）→ 列表徽标文案（即打标请求的实际注入量）。 */
function formatChars(count: number): string {
  if (count >= 10_000) {
    return `${(count / 10_000).toFixed(1)} 万字`;
  }
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}k 字`;
  }
  return `${count} 字`;
}

function SkillFileChip({
  entry,
  active,
  onSelect,
}: {
  entry: SkillFileInfo;
  active: boolean;
  onSelect: (path: string) => void;
}): ReactElement {
  const chip = (
    <button
      type="button"
      disabled={!entry.previewable}
      onClick={() => onSelect(entry.path)}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[12px] transition-colors",
        entry.previewable
          ? "border-border bg-card hover:border-primary/50 hover:text-primary"
          : "cursor-not-allowed border-dashed border-border text-muted-foreground/60 opacity-70",
        active && entry.previewable && "border-primary bg-primary/10 text-primary",
      )}
    >
      {entry.path.startsWith("references/") ? (
        <FileTextIcon className="size-3" />
      ) : entry.path.startsWith("assets/") ? (
        <ImageIcon className="size-3" />
      ) : (
        <CopyIcon className="size-3" />
      )}
      {entry.path}
    </button>
  );
  if (entry.previewable) {
    return chip;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{chip}</span>
      </TooltipTrigger>
      <TooltipContent>不参与注入（注入范围 = SKILL.md 与 references/）</TooltipContent>
    </Tooltip>
  );
}

export function SkillsPanel(): ReactElement {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const [files, setFiles] = useState<SkillFileInfo[]>([]);
  const [previewPath, setPreviewPath] = useState("");
  const [previewContent, setPreviewContent] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pathValue, setPathValue] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mdInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.listSkills();
      setSkills(list);
      // 首次加载默认选中第一个技能（详情区直接有内容；用户可再点选其他）。
      setSelected((current) =>
        current === "" && list.length > 0 ? (list[0]?.name ?? "") : current,
      );
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 选中变化 → 拉包文件清单；默认预览 SKILL.md（注入源）。
  useEffect(() => {
    if (selected === "") {
      setFiles([]);
      setPreviewPath("");
      setPreviewContent("");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.listSkillFiles(selected);
        if (cancelled) {
          return;
        }
        setFiles(result.files);
        const first = result.files.find((entry) => entry.previewable);
        if (first !== undefined) {
          const content = await api.readSkillFile(selected, first.path);
          if (!cancelled) {
            setPreviewPath(first.path);
            setPreviewContent(content.content);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setFeedback({ kind: "error", text: errorMessage(err) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const current = skills.find((item) => item.name === selected);
  const keyword = search.trim().toLowerCase();
  const visible =
    keyword === ""
      ? skills
      : skills.filter(
          (item) =>
            item.name.toLowerCase().includes(keyword) ||
            item.description.toLowerCase().includes(keyword),
        );

  const pick = (name: string): void => {
    setSelected(name);
    setFeedback(null);
  };

  const toggle = async (skill: SkillInfo): Promise<void> => {
    try {
      await api.setSkillEnabled(skill.name, !skill.enabled);
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const applyImportResult = (result: SkillImportResponse): void => {
    const sizeKb = (result.total_bytes / 1024).toFixed(1);
    setFeedback({
      kind: "success",
      text: `已导入「${result.name}」（${sizeKb} KiB——skill 全文将注入打标请求，体积偏大时留意 token 消耗）`,
    });
    setSelected(result.name);
  };

  const doImport = async (picked: File[]): Promise<void> => {
    setImporting(true);
    try {
      applyImportResult(await api.importSkillFiles(picked));
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setImporting(false);
    }
  };

  const doImportFile = async (picked: File | undefined): Promise<void> => {
    if (picked === undefined) {
      return;
    }
    setImporting(true);
    try {
      applyImportResult(await api.importSkillFile(picked));
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setImporting(false);
    }
  };

  const doImportPath = async (): Promise<void> => {
    const path = pathValue.trim();
    if (path === "") {
      return;
    }
    setImporting(true);
    try {
      applyImportResult(await api.importSkill(path));
      setPathValue("");
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setImporting(false);
    }
  };

  const remove = async (): Promise<void> => {
    if (selected === "") {
      return;
    }
    try {
      await api.deleteSkill(selected);
      setDeleteDialogOpen(false);
      setFeedback({ kind: "success", text: `已删除「${selected}」` });
      setSelected("");
      await reload();
    } catch (err) {
      setDeleteDialogOpen(false);
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const openPreview = async (path: string): Promise<void> => {
    if (selected === "") {
      return;
    }
    try {
      const content = await api.readSkillFile(selected, path);
      setPreviewPath(path);
      setPreviewContent(content.content);
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[340px_1fr] overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      {/* 左：列表 + 导入 */}
      <div className="flex min-h-0 flex-col p-2">
        <div className="flex items-baseline gap-2 px-3 pt-2.5 pb-1.5">
          <h3 className="text-[13px] font-semibold">技能</h3>
          <span className="text-[11px] text-muted-foreground">{skills.length} 个</span>
        </div>
        <div className="relative mx-1 mb-2">
          <SearchIcon className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索技能"
            placeholder="搜索名称或描述…"
            className="pl-8"
            value={search}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-1">
          {visible.length === 0 && (
            <p className="px-1 text-[12px] text-muted-foreground">
              （{skills.length === 0 ? "Skill 库为空——下方导入" : "没有匹配的技能"}）
            </p>
          )}
          {visible.map((skill) => {
            const active = skill.name === selected;
            return (
              <div
                key={skill.name}
                className={
                  "rounded-lg border p-2.5 transition-colors " +
                  (active
                    ? "border-primary bg-primary/10"
                    : "border-border bg-card hover:border-primary/40")
                }
              >
                <div className="flex items-center gap-2">
                  <Switch
                    checked={skill.enabled}
                    aria-label={`启用 ${skill.name}`}
                    onClick={() => void toggle(skill)}
                  />
                  <button
                    type="button"
                    onClick={() => pick(skill.name)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className="min-w-0 truncate text-[13px] font-medium">
                        {skill.name}
                      </span>
                      <span
                        className="shrink-0 rounded-full bg-muted px-1.5 text-[10.5px] leading-[1.6] text-muted-foreground"
                        title="注入正文字符数（SKILL.md + references，即打标请求的注入量）"
                      >
                        {formatChars(skill.body_chars)}
                      </span>
                    </span>
                    {skill.description === "" ? (
                      <span className="line-clamp-2 text-[12px] leading-relaxed text-muted-foreground">
                        （无描述）
                      </span>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          {/* 去掉 block：line-clamp-2 自带 -webkit-box 显示模式与省略号，
                              block 会把它覆盖成普通块级导致截断失效（长下划线词把卡片撑破
                              左栏宽度，2026-09-13 用户反馈）；anywhere 断长词兜底 */}
                          <span
                            className={
                              "line-clamp-2 text-[12px] leading-relaxed [overflow-wrap:anywhere] " +
                              (skill.description.startsWith("文件损坏：")
                                ? "text-destructive"
                                : "text-muted-foreground")
                            }
                          >
                            {skill.description}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-80 whitespace-normal leading-relaxed">
                          {skill.description}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </button>
                  <Badge variant={skill.enabled ? "success" : "muted"}>
                    {skill.enabled ? "已启用" : "已停用"}
                  </Badge>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-2 border-t border-border px-3 pt-3 pb-1">
          <p className="mb-2 text-[12px] font-medium">
            导入 skill 包（agentskills.io 标准）
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            aria-label="选择 skill 文件夹"
            // @ts-expect-error -- webkitdirectory 为浏览器非标准属性，React DOM 类型未收录
            webkitdirectory=""
            onChange={(event) => {
              const picked = Array.from(event.currentTarget.files ?? []);
              if (picked.length > 0) {
                void doImport(picked);
              }
              event.currentTarget.value = "";
            }}
          />
          <input
            ref={mdInputRef}
            type="file"
            accept=".md"
            hidden
            aria-label="选择 SKILL.md 文件"
            onChange={(event) => {
              void doImportFile(event.currentTarget.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={importing}
              onClick={() => fileInputRef.current?.click()}
            >
              <FolderOpenIcon className="size-4" />
              文件夹…
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={importing}
              onClick={() => mdInputRef.current?.click()}
            >
              <FileTextIcon className="size-4" />
              SKILL.md 文件…
            </Button>
          </div>
          <div className="mt-2 flex gap-2">
            <Input
              aria-label="skill 本机路径"
              placeholder="粘贴本机路径后导入…"
              value={pathValue}
              onInput={(event) => setPathValue(event.currentTarget.value)}
            />
            <Button
              type="button"
              variant="outline"
              disabled={importing || pathValue.trim() === ""}
              onClick={() => void doImportPath()}
            >
              导入
            </Button>
          </div>
          {feedback !== null && (
            <Alert
              variant={feedback.kind === "error" ? "destructive" : "success"}
              className="mt-2"
            >
              <AlertDescription>{feedback.text}</AlertDescription>
            </Alert>
          )}
        </div>
      </div>

      {/* 右：详情 + 包内容预览 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto border-l border-border p-5">
        {current !== undefined ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <h3 className="text-[15px] font-semibold">{current.name}</h3>
              <Badge variant={current.enabled ? "success" : "muted"}>
                {current.enabled ? "已启用" : "已停用"}
              </Badge>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                className="ml-auto"
                onClick={() => setDeleteDialogOpen(true)}
              >
                删除
              </Button>
            </div>
            <p className="text-[13px] text-muted-foreground">
              {current.description === "" ? "（无描述）" : current.description}
            </p>

            <div>
              <p className="mb-1.5 text-[12px] text-muted-foreground">
                包文件——SKILL.md 与 references/ 注入请求；assets / scripts 不参与注入
              </p>
              <div className="flex flex-wrap gap-1.5">
                {files.map((entry) => (
                  <SkillFileChip
                    key={entry.path}
                    entry={entry}
                    active={entry.path === previewPath}
                    onSelect={(path) => void openPreview(path)}
                  />
                ))}
              </div>
            </div>

            {previewPath !== "" && (
              <div className="min-h-0 overflow-hidden rounded-md border border-border">
                <div className="border-b border-border bg-muted/40 px-3 py-1.5 text-[12px] text-muted-foreground">
                  {previewPath}
                </div>
                <pre className="max-h-96 overflow-auto p-3 font-mono text-[12.5px] leading-[1.7] whitespace-pre-wrap">
                  {previewContent}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
            左侧选择一个技能查看详情与包内容。
          </div>
        )}
      </div>

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除技能「{selected}」？</DialogTitle>
            <DialogDescription>
              将从 Skill 库整目录移除该包。此操作不可撤销；停用 ≠ 删除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive-fill"
              onClick={() => void remove()}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
