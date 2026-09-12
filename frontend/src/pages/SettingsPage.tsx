/**
 * 设置容器（design「设置容器与技能预览」）：左二级导航（连接 / 能力）+ 右内容区。
 * 子页切换即导航切换；未来新设置项（生成参数等）加导航项即可，不再新增主导航。
 */
import {
  CopyIcon,
  FileTextIcon,
  ImageIcon,
  LockIcon,
  PlusIcon,
  SearchIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import type { EndpointConfigSummary, SkillFileInfo, SkillInfo } from "../api";
import { api, errorMessage } from "../api";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { Separator } from "../components/ui/separator";
import { Switch } from "../components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../components/ui/tooltip";
import { cn } from "../lib/utils";

type SettingsSection = "endpoints" | "skills";

interface Feedback {
  kind: "success" | "error";
  text: string;
}

/** 一期唯一支持的调用格式；其余选项灰显「暂未支持」，未来补适配器即启用。 */
const SUPPORTED_API_FORMAT = "openai-chat-completions";

const API_FORMATS: ReadonlyArray<{ value: string; label: string; enabled: boolean }> = [
  { value: SUPPORTED_API_FORMAT, label: "OpenAI Chat Completions", enabled: true },
  { value: "openai-responses", label: "OpenAI Responses（暂未支持）", enabled: false },
  {
    value: "anthropic-messages",
    label: "Anthropic Messages（暂未支持）",
    enabled: false,
  },
];

/* ================= 连接 · 端点配置（列表 + 详情双栏） ================= */

function EndpointConfigPanel(): ReactElement {
  const [endpoints, setEndpoints] = useState<EndpointConfigSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftBaseUrl, setDraftBaseUrl] = useState("");
  const [draftFormat, setDraftFormat] = useState<string>(SUPPORTED_API_FORMAT);
  const [draftModel, setDraftModel] = useState("");
  const [draftKey, setDraftKey] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const reload = useCallback(async (): Promise<EndpointConfigSummary[]> => {
    try {
      const list = await api.listEndpoints();
      setEndpoints(list);
      return list;
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
      return [];
    }
  }, []);

  useEffect(() => {
    void reload().then((list) => {
      const active = list.find((item) => item.is_active);
      if (active !== undefined) {
        setSelected(active.name);
      }
    });
  }, [reload]);

  const current = endpoints.find((item) => item.name === selected);

  // 选中变化（或保存后列表刷新）时把详情回填为落盘值；创建态保留空白草稿。
  useEffect(() => {
    if (creating || current === undefined) {
      return;
    }
    setDraftName(current.name);
    setDraftBaseUrl(current.base_url);
    setDraftFormat(current.api_format);
    setDraftModel(current.model);
    setDraftKey("");
  }, [
    creating,
    current?.name,
    current?.base_url,
    current?.model,
    current?.api_format,
    current,
  ]);

  const pick = (name: string): void => {
    setSelected(name);
    setCreating(false);
    setFeedback(null);
  };

  const startCreate = (): void => {
    setCreating(true);
    setSelected("");
    setDraftName("");
    setDraftBaseUrl("");
    setDraftFormat(SUPPORTED_API_FORMAT);
    setDraftModel("");
    setDraftKey("");
    setFeedback(null);
  };

  const save = async (): Promise<void> => {
    const key = draftKey.trim();
    try {
      if (creating) {
        const created = await api.createEndpoint({
          name: draftName,
          base_url: draftBaseUrl,
          model: draftModel,
          api_format: draftFormat,
          // 密钥可留空：之后可再编辑补配，或用环境变量 DSF_API_KEY 兜底。
          ...(key === "" ? {} : { api_key: key }),
        });
        setFeedback({ kind: "success", text: `已创建配置「${created.name}」` });
        setCreating(false);
        setSelected(created.name);
      } else {
        const updated = await api.updateEndpoint(selected, {
          base_url: draftBaseUrl,
          model: draftModel,
          api_format: draftFormat,
          // 没填新密钥就整个不传：后端沿用该配置已存密钥，不必重输。
          ...(key === "" ? {} : { api_key: key }),
        });
        setFeedback({ kind: "success", text: `已保存「${updated.name}」的更改` });
      }
      setDraftKey("");
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const activate = async (): Promise<void> => {
    try {
      await api.activateEndpoint(selected);
      await reload();
      setFeedback({
        kind: "success",
        text: `已切换当前使用的配置为「${selected}」，对新请求立即生效`,
      });
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const remove = async (): Promise<void> => {
    try {
      await api.deleteEndpoint(selected);
      setDeleteDialogOpen(false);
      setFeedback({ kind: "success", text: `已删除配置「${selected}」` });
      setSelected("");
      await reload();
    } catch (err) {
      setDeleteDialogOpen(false);
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const canSave =
    draftName.trim() !== "" && draftBaseUrl.trim() !== "" && draftModel.trim() !== "";

  return (
    <div className="flex h-full min-h-0">
      {/* 左：配置列表 */}
      <div className="flex w-64 shrink-0 flex-col p-4">
        <div className="mb-3 flex items-baseline gap-2">
          <h3 className="text-[15px] font-semibold">端点配置</h3>
          <span className="text-[12px] text-muted-foreground">
            {endpoints.length} 套
          </span>
        </div>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          {endpoints.map((item) => {
            const active = item.name === selected;
            return (
              <button
                key={item.name}
                type="button"
                onClick={() => pick(item.name)}
                aria-current={active ? "true" : undefined}
                className={
                  "relative block w-full rounded-lg border p-3 text-left transition-colors hover:border-primary/40 " +
                  (active ? "border-primary bg-primary/10" : "border-border bg-card")
                }
              >
                <span className="flex items-center gap-2">
                  <span
                    className={
                      "size-2 rounded-full " +
                      (item.is_active ? "bg-success" : "bg-muted-foreground/30")
                    }
                    aria-label={item.is_active ? "当前使用" : ""}
                  />
                  <span
                    className={
                      "truncate text-[13px] font-medium" +
                      (active ? " text-primary" : "")
                    }
                  >
                    {item.name}
                  </span>
                </span>
                <span className="mt-0.5 block truncate pl-4 text-[12px] text-muted-foreground">
                  {item.model}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={startCreate}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border p-3 text-[13px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
          >
            <PlusIcon className="size-4" /> 添加配置
          </button>
        </div>
      </div>

      <Separator className="my-4" />

      {/* 右：详情 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto p-4">
        {creating || current !== undefined ? (
          <div className="mx-auto w-full max-w-xl space-y-4">
            <div className="flex items-center gap-2">
              <h3 className="text-[15px] font-semibold">
                {creating ? "添加配置" : current?.name}
              </h3>
              {!creating && current?.is_active && (
                <Badge variant="success">当前使用</Badge>
              )}
            </div>

            <div className="space-y-1">
              <Label htmlFor="endpoint-name">名称</Label>
              <Input
                id="endpoint-name"
                value={draftName}
                disabled={!creating}
                placeholder="如 siliconflow"
                onInput={(event) => setDraftName(event.currentTarget.value)}
              />
              <p className="text-[12px] text-muted-foreground">
                {creating
                  ? "即数据目录名，创建后不可改。"
                  : "名称即目录名，创建后不可改。"}
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-base-url">Base URL</Label>
              <Input
                id="endpoint-base-url"
                value={draftBaseUrl}
                placeholder="如 https://api.siliconflow.cn/v1"
                onInput={(event) => setDraftBaseUrl(event.currentTarget.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-format">API 格式</Label>
              <Select
                value={draftFormat}
                onValueChange={setDraftFormat}
                disabled={!creating && current === undefined}
              >
                <SelectTrigger id="endpoint-format">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {API_FORMATS.map((format) => (
                    <SelectItem
                      key={format.value}
                      value={format.value}
                      disabled={!format.enabled}
                    >
                      {format.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-model">模型名</Label>
              <Input
                id="endpoint-model"
                value={draftModel}
                placeholder="如 Qwen/Qwen3.5-4B"
                onInput={(event) => setDraftModel(event.currentTarget.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-api-key">API 密钥</Label>
              <Input
                id="endpoint-api-key"
                type="password"
                value={draftKey}
                placeholder={
                  !creating && (current?.has_api_key ?? false)
                    ? "留空 = 沿用已存密钥"
                    : "请输入密钥"
                }
                onInput={(event) => setDraftKey(event.currentTarget.value)}
              />
              <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                {(current?.has_api_key ?? draftKey.trim() !== "") && (
                  <span className="size-2 rounded-full bg-success" aria-hidden />
                )}
                {(current?.has_api_key ?? false)
                  ? "已配置" + (current?.is_active ? " · 来源：credentials 文件" : "")
                  : "未配置——可之后补配，或用环境变量 DSF_API_KEY 兜底"}
              </p>
              <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <LockIcon className="size-3" /> 密钥写入该配置的 credentials
                文件，界面不回显、接口不返回内容。
              </p>
            </div>

            {feedback !== null && (
              <Alert variant={feedback.kind === "error" ? "destructive" : "success"}>
                <AlertDescription>{feedback.text}</AlertDescription>
              </Alert>
            )}

            <div className="flex items-center justify-end gap-2 border-t border-border pt-4">
              {!creating && current !== undefined && !current.is_active && (
                <Button type="button" variant="outline" onClick={() => void activate()}>
                  设为当前使用
                </Button>
              )}
              {!creating && (
                <Button
                  type="button"
                  variant="destructive"
                  onClick={() => setDeleteDialogOpen(true)}
                >
                  删除
                </Button>
              )}
              <Button type="button" disabled={!canSave} onClick={() => void save()}>
                {creating ? "创建配置" : "保存更改"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
            左侧选择一套配置，或「添加配置」新建。
          </div>
        )}
      </div>

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除端点配置「{selected}」？</DialogTitle>
            <DialogDescription>
              将连同该配置的密钥文件一起移除；当前使用中的配置需先切换才能删。此操作不可撤销。
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

/* ================= 能力 · 技能（列表 + 详情双栏，含包内容预览） ================= */

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
      <TooltipContent>不参与注入（注入范围 = 仅 SKILL.md）</TooltipContent>
    </Tooltip>
  );
}

function SkillsPanel(): ReactElement {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const [files, setFiles] = useState<SkillFileInfo[]>([]);
  const [previewPath, setPreviewPath] = useState("");
  const [previewContent, setPreviewContent] = useState("");
  const [importPath, setImportPath] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

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

  const doImport = async (): Promise<void> => {
    try {
      const result = await api.importSkill(importPath);
      const sizeKb = (result.total_bytes / 1024).toFixed(1);
      setFeedback({
        kind: "success",
        text: `已导入「${result.name}」（${sizeKb} KiB——skill 全文将注入打标请求，体积偏大时留意 token 消耗）`,
      });
      setImportPath("");
      setSelected(result.name);
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
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
    <div className="flex h-full min-h-0">
      {/* 左：列表 + 导入卡 */}
      <div className="flex w-72 shrink-0 flex-col p-4">
        <div className="mb-2 flex items-baseline gap-2">
          <h3 className="text-[15px] font-semibold">技能</h3>
          <span className="text-[12px] text-muted-foreground">{skills.length} 个</span>
        </div>
        <div className="relative mb-2">
          <SearchIcon className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索技能"
            placeholder="搜索名称或描述…"
            className="pl-8"
            value={search}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
        </div>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
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
                    <span className="block truncate text-[13px] font-medium">
                      {skill.name}
                    </span>
                    <span className="line-clamp-2 block text-[12px] leading-relaxed text-muted-foreground">
                      {skill.description === "" ? "（无描述）" : skill.description}
                    </span>
                  </button>
                  <Badge variant={skill.enabled ? "success" : "muted"}>
                    {skill.enabled ? "已启用" : "已停用"}
                  </Badge>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-3 rounded-lg border border-border bg-card p-3">
          <p className="mb-2 text-[12px] font-medium">
            导入 skill 包（agentskills.io 标准）
          </p>
          <div className="flex items-center gap-2">
            <Input
              aria-label="Skill 包路径"
              placeholder="如 D:/skills/h3-prompt-writing"
              value={importPath}
              onInput={(event) => setImportPath(event.currentTarget.value)}
            />
            <Button
              type="button"
              size="sm"
              disabled={importPath.trim() === ""}
              onClick={() => void doImport()}
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

      <Separator className="my-4" />

      {/* 右：详情 + 包内容预览 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto p-4">
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
                包文件——SKILL.md 注入请求；references 供查阅；assets / scripts
                不参与注入
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

/* ================= 设置容器 ================= */

const SECTIONS: ReadonlyArray<{ key: SettingsSection; group: string; label: string }> =
  [
    { key: "endpoints", group: "连接", label: "端点配置" },
    { key: "skills", group: "能力", label: "技能" },
  ];

export function SettingsPage(): ReactElement {
  const [section, setSection] = useState<SettingsSection>("endpoints");

  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0">
        <nav
          className="w-52 shrink-0 space-y-5 py-4 pr-4 pl-5"
          aria-label="设置二级导航"
        >
          {(["连接", "能力"] as const).map((group) => (
            <div key={group}>
              <p className="mb-1 px-3 text-[12px] text-muted-foreground">{group}</p>
              <ul className="space-y-0.5">
                {SECTIONS.filter((item) => item.group === group).map((item) => {
                  const active = section === item.key;
                  return (
                    <li key={item.key}>
                      <button
                        type="button"
                        aria-current={active ? "true" : undefined}
                        onClick={() => setSection(item.key)}
                        className={
                          "relative flex w-full items-center rounded-md px-3 py-2 text-[13px] transition-colors hover:bg-accent " +
                          (active ? "bg-primary/10 font-medium text-primary" : "")
                        }
                      >
                        {active && (
                          <span
                            className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-primary"
                            aria-hidden
                          />
                        )}
                        {item.label}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <Separator className="my-4" />

        <section className="min-w-0 flex-1" aria-label="设置内容区">
          {section === "endpoints" && <EndpointConfigPanel />}
          {section === "skills" && <SkillsPanel />}
        </section>
      </div>
    </TooltipProvider>
  );
}
