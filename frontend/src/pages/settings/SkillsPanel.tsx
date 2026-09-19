/** 能力 · 技能（列表 + 详情双栏，含包内容预览与三种导入方式）。 */
import {
  CopyIcon,
  FileTextIcon,
  FolderOpenIcon,
  ImageIcon,
  ImportIcon,
  SaveIcon,
  SearchIcon,
  Trash2Icon,
  Undo2Icon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { SkillFileInfo, SkillImportResponse, SkillInfo } from "../../api";
import { api, errorMessage } from "../../api";
import { DirectoryPicker } from "../../components/DirectoryPicker";
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
import {
  Tip,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../../components/ui/tooltip";
import type { Feedback } from "../../lib/feedback";
import { cn } from "../../lib/utils";
import { readSkillDrop } from "./skill-drop";

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
  disabled,
  onSelect,
}: {
  entry: SkillFileInfo;
  active: boolean;
  disabled: boolean;
  onSelect: (path: string) => void;
}): ReactElement {
  const chip = (
    <button
      type="button"
      disabled={disabled || !entry.previewable}
      onClick={() => onSelect(entry.path)}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-full border px-3 py-1 text-t-sm transition-colors [overflow-wrap:anywhere]",
        entry.previewable
          ? "border-border bg-card hover:bg-accent"
          : "cursor-not-allowed border-border bg-muted text-n-400 line-through",
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
  const [originalContent, setOriginalContent] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const previewGeneration = useRef(0);
  const savePending = useRef(false);
  const togglePending = useRef(false);
  const [toggling, setToggling] = useState(false);
  const dirty = previewContent !== originalContent || descriptionDraft !== null;
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [readingDrop, setReadingDrop] = useState(false);
  const dropPending = useRef(false);
  const [pathValue, setPathValue] = useState("");
  const [pickingPath, setPickingPath] = useState(false);
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
    const generation = ++previewGeneration.current;
    setPreviewPath("");
    setPreviewContent("");
    setOriginalContent("");
    setDescriptionDraft(null);
    if (selected === "") {
      setFiles([]);
      setPreviewPath("");
      setPreviewContent("");
      return;
    }
    let cancelled = false;
    setLoadingPreview(true);
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
          if (!cancelled && generation === previewGeneration.current) {
            setPreviewPath(first.path);
            setPreviewContent(content.content);
            setOriginalContent(content.content);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setFeedback({ kind: "error", text: errorMessage(err) });
        }
      } finally {
        if (!cancelled && generation === previewGeneration.current)
          setLoadingPreview(false);
      }
    })();
    return () => {
      cancelled = true;
      previewGeneration.current += 1;
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
    if (dirty || saving) return;
    setSelected(name);
    setFeedback(null);
  };

  const toggle = async (skill: SkillInfo): Promise<void> => {
    if (togglePending.current) return;
    togglePending.current = true;
    setToggling(true);
    try {
      await api.setSkillEnabled(skill.name, !skill.enabled);
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      togglePending.current = false;
      setToggling(false);
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

  const drop = async (transfer: DataTransfer): Promise<void> => {
    if (dropPending.current || importing) return;
    dropPending.current = true;
    setReadingDrop(true);
    try {
      const picked = await readSkillDrop(transfer);
      if (picked.length === 0) throw new Error("没有可导入的文件。");
      if (picked.length === 1 && !(picked[0]?.webkitRelativePath ?? "").includes("/")) {
        await doImportFile(picked[0]);
      } else {
        await doImport(picked);
      }
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      dropPending.current = false;
      setReadingDrop(false);
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
    if (selected === "" || dirty || savePending.current) {
      return;
    }
    const generation = ++previewGeneration.current;
    setLoadingPreview(true);
    try {
      const content = await api.readSkillFile(selected, path);
      if (generation !== previewGeneration.current) return;
      setPreviewPath(path);
      setPreviewContent(content.content);
      setOriginalContent(content.content);
      setDescriptionDraft(null);
    } catch (err) {
      if (generation === previewGeneration.current)
        setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      if (generation === previewGeneration.current) setLoadingPreview(false);
    }
  };

  const save = async (): Promise<void> => {
    if (savePending.current || loadingPreview || !dirty) return;
    savePending.current = true;
    setSaving(true);
    const generation = previewGeneration.current;
    try {
      const result = await api.saveSkillFile(selected, previewPath, {
        content: previewContent,
        original_content: originalContent,
        ...(descriptionDraft !== null ? { description: descriptionDraft } : {}),
      });
      if (generation !== previewGeneration.current) return;
      setPreviewContent(result.content);
      setOriginalContent(result.content);
      setDescriptionDraft(null);
      setFeedback({ kind: "success", text: "已保存" });
      await reload();
    } catch (err) {
      if (generation === previewGeneration.current)
        setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      savePending.current = false;
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-auto bg-card lg:grid lg:grid-cols-[340px_minmax(0,1fr)] lg:overflow-hidden">
      {/* 左：列表 + 导入 */}
      <fieldset
        disabled={dirty || saving}
        className="flex min-w-0 shrink-0 flex-col lg:min-h-0"
      >
        <div className="flex flex-wrap items-center gap-2 px-4 pt-6 pb-2 lg:px-6">
          <h3 className="text-t-sm font-medium">技能列表</h3>
          <span className="text-t-sm text-muted-foreground">{skills.length} 个</span>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => {
              setFeedback(null);
              setImportOpen(true);
            }}
          >
            <ImportIcon />
            导入 Skill
          </Button>
        </div>
        <div className="relative mx-4 my-2 lg:mx-6">
          <SearchIcon className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索技能"
            placeholder="搜索名称或描述…"
            className="pl-8"
            value={search}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
        </div>
        <div className="max-h-40 min-h-0 flex-1 space-y-1 overflow-y-auto px-4 pb-3 lg:max-h-none lg:px-6">
          {visible.length === 0 && (
            <p className="px-1 text-t-sm text-muted-foreground">
              {skills.length === 0 ? "Skill 库为空" : "没有匹配的技能"}
            </p>
          )}
          {visible.map((skill) => {
            const active = skill.name === selected;
            return (
              <div
                key={skill.name}
                className={
                  "rounded-md px-3 py-2 transition-colors " +
                  (active ? "bg-primary/10" : "bg-card hover:bg-accent")
                }
              >
                <div className="flex items-center gap-2">
                  <Switch
                    disabled={toggling}
                    checked={skill.enabled}
                    aria-label={`启用 ${skill.name}`}
                    onClick={() => void toggle(skill)}
                  />
                  <button
                    type="button"
                    onClick={() => pick(skill.name)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 truncate text-t-md font-medium">
                        {skill.name}
                      </span>
                      <Tip label="注入正文字符数（SKILL.md + references，即打标请求的注入量）">
                        <span className="shrink-0 text-t-sm text-muted-foreground">
                          {formatChars(skill.body_chars)}
                        </span>
                      </Tip>
                    </span>
                    {skill.description === "" ? (
                      <span className="line-clamp-2 text-t-sm text-muted-foreground">
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
                              "line-clamp-2 text-t-sm [overflow-wrap:anywhere] " +
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
                  <span className="sr-only">{skill.enabled ? "已启用" : "已停用"}</span>
                </div>
              </div>
            );
          })}
        </div>
      </fieldset>
      <Dialog
        open={importOpen}
        onOpenChange={(open) => {
          if (!importing && !readingDrop) setImportOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>导入 Skill 包</DialogTitle>
            <DialogDescription>agentskills.io 标准</DialogDescription>
          </DialogHeader>
          <fieldset disabled={importing || readingDrop} className="min-w-0 space-y-3">
            <button
              type="button"
              aria-label="拖入 Skill 包"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                void drop(event.dataTransfer);
              }}
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center justify-center gap-3 rounded-md border border-dashed border-input bg-card p-8 text-t-md text-muted-foreground"
            >
              <ImportIcon className="size-4" />
              拖文件夹 / SKILL.md 到这里导入
            </button>
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
                文件夹
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={importing}
                onClick={() => mdInputRef.current?.click()}
              >
                <FileTextIcon className="size-4" />
                SKILL.md 文件
              </Button>
            </div>
            <div className="mt-2 flex gap-2">
              <Input
                aria-label="skill 服务器路径"
                placeholder="服务器上的目录或文件"
                value={pathValue}
                onInput={(event) => setPathValue(event.currentTarget.value)}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-lg"
                    aria-label="选择技能目录或文件"
                    disabled={importing}
                    onClick={() => setPickingPath(true)}
                  >
                    <FolderOpenIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>选择目录或文件</TooltipContent>
              </Tooltip>
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
          </fieldset>
        </DialogContent>
      </Dialog>

      {/* 右：详情 + 包内容预览 */}
      <div className="flex min-w-0 shrink-0 flex-1 flex-col border-t border-border p-4 lg:min-h-0 lg:overflow-y-auto lg:border-t-0 lg:border-l lg:px-8 lg:py-6">
        {!importOpen && feedback !== null && (
          <Alert variant={feedback.kind === "error" ? "destructive" : "success"}>
            <AlertDescription>{feedback.text}</AlertDescription>
          </Alert>
        )}
        {current !== undefined ? (
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="flex items-center gap-2">
              <h3 className="min-w-0 flex-1 truncate text-t-xl font-medium">
                {current.name}
              </h3>
              <Badge variant={current.enabled ? "success" : "muted"}>
                {current.enabled ? "已启用" : "已停用"}
              </Badge>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="删除技能"
                    className="ml-auto"
                    disabled={dirty || saving || loadingPreview}
                    onClick={() => setDeleteDialogOpen(true)}
                  >
                    <Trash2Icon className="text-bad-ink" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>删除技能</TooltipContent>
              </Tooltip>
            </div>
            <label
              htmlFor="skill-description"
              className="flex items-center gap-3 text-t-sm"
            >
              <span className="shrink-0 text-muted-foreground">描述</span>
              <Input
                aria-label="技能描述"
                id="skill-description"
                value={descriptionDraft ?? current.description}
                disabled={saving || loadingPreview || previewPath !== "SKILL.md"}
                onChange={(event) =>
                  setDescriptionDraft(
                    event.currentTarget.value === current.description
                      ? null
                      : event.currentTarget.value,
                  )
                }
              />
            </label>

            <div>
              <div className="flex flex-wrap gap-1.5">
                {files.map((entry) => (
                  <SkillFileChip
                    key={entry.path}
                    entry={entry}
                    active={entry.path === previewPath}
                    disabled={dirty || saving}
                    onSelect={(path) => void openPreview(path)}
                  />
                ))}
              </div>
            </div>

            {previewPath !== "" && (
              <div className="flex min-h-40 flex-1 flex-col gap-2">
                <label
                  htmlFor="skill-content"
                  className="text-t-sm text-muted-foreground"
                >
                  内容预览
                </label>
                <textarea
                  aria-label="技能文件内容"
                  id="skill-content"
                  spellCheck={false}
                  value={previewContent}
                  disabled={saving || loadingPreview}
                  onChange={(event) => setPreviewContent(event.currentTarget.value)}
                  className="min-h-40 w-full flex-1 resize-y rounded-md border border-input bg-card px-4 py-3 font-sans text-t-sm leading-(--lh-loose) hover:border-n-400 focus:border-n-400"
                />
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                disabled={!dirty || saving}
                onClick={() => {
                  setPreviewContent(originalContent);
                  setDescriptionDraft(null);
                }}
              >
                <Undo2Icon />
                放弃更改
              </Button>
              <Button
                disabled={!dirty || saving || loadingPreview}
                onClick={() => void save()}
              >
                <SaveIcon />
                保存更改
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-t-md text-muted-foreground">
            左侧选择一个技能查看详情与包内容。
          </div>
        )}
      </div>

      {pickingPath && (
        <DirectoryPicker
          files
          suffixes={[".md", ".txt"]}
          onClose={() => setPickingPath(false)}
          onSelect={(path) => {
            setPathValue(path);
            setPickingPath(false);
          }}
        />
      )}
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
