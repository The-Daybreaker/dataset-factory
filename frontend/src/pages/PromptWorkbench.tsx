/**
 * 提示词工作台（无全局页头、三列满高、列间竖装饰线——原型稿 ui-draft-05 为视觉事实源）：
 * ① 列表列（270px）：提示词卡片 + 计数 + 新建；选中的卡片即本轮打标的基础提示词。
 * ② 编辑器列（430px）：名称 / 描述 / Markdown 正文（等宽 + 行号槽）+ 字节计量 + 保存；
 *    改名保存 = 重命名文件（历史备份随迁）。
 * ③ 对话列（自适应）：端点配置切换器 + 主题切换、会话行、「本轮携带」请求条、消息流、输入区。
 */
import {
  ChevronDownIcon,
  FileTextIcon,
  ImageIcon,
  PaperclipIcon,
  PlusIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import {
  type ChangeEvent,
  type KeyboardEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type {
  EndpointConfigSummary,
  HistoryMessageView,
  PromptInfo,
  SkillInfo,
} from "../api";
import { ApiError, api, errorMessage } from "../api";
import { ShutdownButton } from "../components/shutdown-button";
import { ThemeToggle } from "../components/theme-toggle";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../components/ui/tooltip";

/** 基础提示词的字节护栏（对齐 Codex project_doc_max_bytes，后端同值校验）。 */
const PROMPT_BYTE_BUDGET = 32 * 1024;

/** 待发送的图片：原始文件名 + data URL（后端接受 data URL 或纯 base64）。 */
interface PendingImage {
  name: string;
  dataUrl: string;
}

/** 界面里的消息 = 后端历史消息 + 渲染用稳定 id + 新增消息才有的 meta。 */
interface ChatMessage extends HistoryMessageView {
  id: number;
  model?: string;
  durationSeconds?: number;
  createdAt?: Date;
}

/** 一次性反馈（编辑器列的操作结果）；id 让同文案重复出现也能触发重渲染。 */
interface Feedback {
  kind: "success" | "error";
  text: string;
}

/** 端点配置切换器（chip = 「名称 · 模型名」；切换调 activate，对新请求立即生效）。 */
function EndpointSwitcher({
  endpoints,
  onActivate,
  onManage,
}: {
  endpoints: EndpointConfigSummary[];
  onActivate: (name: string) => void;
  onManage: () => void;
}): ReactElement {
  const active = endpoints.find((item) => item.is_active);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-[30px] max-w-60 rounded-full border-border bg-card px-3 text-[12px] text-muted-foreground"
          aria-label="端点配置切换器"
        >
          <span className="size-[7px] shrink-0 rounded-full bg-success" aria-hidden />
          <span className="truncate">
            {active ? `${active.name} · ${active.model}` : "未配置端点"}
          </span>
          <ChevronDownIcon className="size-3 shrink-0" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>端点配置（当前使用）</DropdownMenuLabel>
        {endpoints.length === 0 && (
          <DropdownMenuLabel>（还没有配置——去「管理配置」新增）</DropdownMenuLabel>
        )}
        {endpoints.map((item) => (
          <DropdownMenuItem key={item.name} onSelect={() => onActivate(item.name)}>
            <span className="flex-1 truncate">
              {item.name} · {item.model}
            </span>
            {item.is_active && <span className="size-2 rounded-full bg-success" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onManage}>管理配置…</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** 正文编辑区：等宽字体 + 行号槽（行号随滚动同步平移）。 */
function BodyEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}): ReactElement {
  const [scrollTop, setScrollTop] = useState(0);
  const lineCount = value === "" ? 1 : value.split("\n").length;

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden rounded-md border border-input bg-background focus-within:ring-2 focus-within:ring-ring/55">
      <div
        aria-hidden
        className="min-w-[30px] shrink-0 overflow-hidden border-r border-border bg-muted/50 px-2 py-2.5 text-right font-mono text-[12px] leading-[1.7] text-muted-foreground select-none"
      >
        <div style={{ transform: `translateY(-${scrollTop}px)` }}>
          {Array.from({ length: lineCount }, (_, index) => index + 1).map(
            (lineNumber) => (
              <div key={lineNumber}>{lineNumber}</div>
            ),
          )}
        </div>
      </div>
      <textarea
        data-slot="prompt-body"
        aria-label="正文（Markdown）"
        className="min-h-0 w-full resize-none bg-transparent px-3 py-2.5 font-mono text-[12.5px] leading-[1.7] focus-visible:outline-none"
        value={value}
        onInput={(event) => onChange(event.currentTarget.value)}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        placeholder={"你是……\n（Markdown 正文，即基础提示词本体）"}
      />
    </div>
  );
}

export function PromptWorkbench({
  onNavigateToSettings,
}: {
  onNavigateToSettings: () => void;
}): ReactElement {
  // ---------- 列表与编辑器 ----------
  const [prompts, setPrompts] = useState<PromptInfo[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [isNewDraft, setIsNewDraft] = useState(false);
  const [editorFeedback, setEditorFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  // ---------- 对话列 ----------
  const [endpoints, setEndpoints] = useState<EndpointConfigSummary[]>([]);
  const [activeModel, setActiveModel] = useState("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [instruction, setInstruction] = useState("");
  const [image, setImage] = useState<PendingImage | null>(null);
  const [sending, setSending] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [chatError, setChatError] = useState("");
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const bodyInputRef = useRef<HTMLTextAreaElement>(null);
  // 会话恢复是否带回了基础提示词：带回了就不做「自动选中首条」（恢复优先于默认）。
  const restoredPromptRef = useRef(false);

  const selectPrompt = useCallback(async (name: string): Promise<void> => {
    try {
      const full = await api.getPrompt(name);
      setSelectedName(full.name);
      setDraftName(full.name);
      setDraftDescription(full.description);
      setDraftBody(full.body);
      setIsNewDraft(false);
      setEditorFeedback(null);
    } catch (err) {
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    }
  }, []);

  // 进页拉提示词 / skill / 端点配置三份列表；没有会话恢复时默认选中首条（原型稿激活态）。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [promptList, skillList, endpointList] = await Promise.all([
          api.listPrompts(),
          api.listSkills(),
          api.listEndpoints(),
        ]);
        if (cancelled) {
          return;
        }
        setPrompts(promptList);
        setSkills(skillList);
        setEndpoints(endpointList);
        setActiveModel(endpointList.find((item) => item.is_active)?.model ?? "");
        const first = promptList[0];
        if (!restoredPromptRef.current && first !== undefined) {
          await selectPrompt(first.name);
        }
      } catch (err) {
        if (!cancelled) {
          setEditorFeedback({ kind: "error", text: errorMessage(err) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectPrompt]);

  // 恢复最近一次会话（「还没有会话」404 是首次使用的正常情况，不当错误展示）。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const snapshot = await api.latestSession();
        if (cancelled) {
          return;
        }
        restoredPromptRef.current = true;
        setSessionId(snapshot.session_id);
        setSkillNames(snapshot.settings.skill_names);
        setMessages(snapshot.messages.map((item, index) => ({ ...item, id: index })));
        if (snapshot.settings.prompt_name !== null) {
          await selectPrompt(snapshot.settings.prompt_name);
        }
      } catch (err) {
        const noSessionYet = err instanceof ApiError && err.status === 404;
        if (!cancelled && !noSessionYet) {
          setChatError(errorMessage(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectPrompt]);

  // 发送期间每秒累加等待时间；结束（成功 / 失败）时归零。
  useEffect(() => {
    if (!sending) {
      return;
    }
    setWaitSeconds(0);
    const timer = setInterval(() => {
      setWaitSeconds((current) => current + 1);
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [sending]);

  const reloadPrompts = useCallback(async (): Promise<void> => {
    try {
      setPrompts(await api.listPrompts());
    } catch (err) {
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    }
  }, []);

  const startNewDraft = (): void => {
    setSelectedName("");
    setDraftName("");
    setDraftDescription("");
    setDraftBody("");
    setIsNewDraft(true);
    setEditorFeedback(null);
    bodyInputRef.current?.focus();
  };

  /** 保存草稿：名称改动过 = 先重命名（改文件名）再写内容，旧名不再保留。 */
  const saveDraft = async (): Promise<void> => {
    const name = draftName.trim();
    if (name === "") {
      setEditorFeedback({ kind: "error", text: "名称不能为空；请填写提示词名称。" });
      return;
    }
    try {
      if (!isNewDraft && selectedName !== "" && name !== selectedName) {
        await api.renamePrompt(selectedName, { new_name: name });
      }
      await api.savePrompt(name, {
        description: draftDescription,
        body: draftBody,
      });
      setIsNewDraft(false);
      setSelectedName(name);
      setEditorFeedback({ kind: "success", text: `已保存提示词「${name}」` });
      await reloadPrompts();
    } catch (err) {
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const deleteSelected = async (): Promise<void> => {
    if (selectedName === "") {
      return;
    }
    try {
      await api.deletePrompt(selectedName);
      setEditorFeedback({ kind: "success", text: `已删除「${selectedName}」` });
      setSelectedName("");
      setDraftName("");
      setDraftDescription("");
      setDraftBody("");
      setIsNewDraft(false);
      await reloadPrompts();
    } catch (err) {
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setDeleteDialogOpen(false);
    }
  };

  const activateEndpoint = async (name: string): Promise<void> => {
    try {
      await api.activateEndpoint(name);
      setEndpoints((current) =>
        current.map((item) => ({ ...item, is_active: item.name === name })),
      );
      setActiveModel(endpoints.find((item) => item.name === name)?.model ?? "");
    } catch (err) {
      setChatError(errorMessage(err));
    }
  };

  const toggleSkill = (name: string): void => {
    setSkillNames((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    );
  };

  const pickImage = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file === undefined) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setImage({ name: file.name, dataUrl: String(reader.result) });
    };
    reader.readAsDataURL(file);
  };

  const send = async (): Promise<void> => {
    if (sending || (instruction.trim() === "" && image === null)) {
      return;
    }
    setSending(true);
    setChatError("");
    const startedAt = Date.now();
    try {
      const result = await api.label({
        session_id: sessionId,
        prompt_name: selectedName === "" ? null : selectedName,
        skill_names: skillNames,
        instruction,
        image_base64: image?.dataUrl ?? null,
        image_name: image?.name ?? "image.png",
      });
      setMessages((current) => [
        ...current,
        {
          id: current.length,
          role: "user",
          text: instruction,
          attachment: image?.name ?? null,
        },
        {
          id: current.length + 1,
          role: "assistant",
          text: result.caption,
          attachment: null,
          model: activeModel || undefined,
          durationSeconds: Math.round((Date.now() - startedAt) / 1000),
          createdAt: new Date(),
        },
      ]);
      setSessionId(result.session_id);
      setInstruction("");
      setImage(null);
    } catch (err) {
      setChatError(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const newSession = (): void => {
    setSessionId(null);
    setMessages([]);
    setChatError("");
  };

  const copyCaption = (message: ChatMessage): void => {
    void navigator.clipboard.writeText(message.text).then(() => {
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId(null), 1500);
    });
  };

  // 中文输入法的回车上屏不属于「发送」（isComposing 判定），Shift+Enter 换行。
  const onInstructionKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  };

  const bodyBytes = new TextEncoder().encode(draftBody).length;
  const byteOver = bodyBytes > PROMPT_BYTE_BUDGET;
  const canSend = !sending && (instruction.trim() !== "" || image !== null);
  const bodyKiB = (bodyBytes / 1024).toFixed(1);

  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0">
        {/* ① 列表列（270px） */}
        <section
          className="flex w-[270px] shrink-0 flex-col py-4 pr-[18px] pl-5"
          aria-label="提示词列表列"
        >
          <div className="flex items-center gap-2 pt-1 pb-4">
            <h2 className="text-[16.5px] font-semibold">提示词</h2>
            <span className="rounded-full bg-secondary px-2 text-[11px] font-medium text-muted-foreground">
              {prompts.length}
            </span>
            <span className="flex-1" />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="size-7 border-border bg-card text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  aria-label="新建提示词"
                  onClick={startNewDraft}
                >
                  <PlusIcon className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>新建提示词</TooltipContent>
            </Tooltip>
          </div>
          <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-1">
            {prompts.length === 0 && (
              <p className="px-1 text-[12px] text-muted-foreground">
                （提示词库为空——点「新建」写一条）
              </p>
            )}
            {prompts.map((prompt) => {
              const active = prompt.name === selectedName;
              return (
                <button
                  key={prompt.name}
                  type="button"
                  onClick={() => void selectPrompt(prompt.name)}
                  aria-current={active ? "true" : undefined}
                  className={
                    "block w-full rounded-lg border bg-card px-3.5 py-3 text-left transition-colors " +
                    (active
                      ? "border-primary/50 bg-primary/5"
                      : "border-border hover:border-foreground/22")
                  }
                >
                  <span
                    className={
                      "block truncate text-[13.5px] leading-[1.45] font-medium " +
                      (active ? "text-primary" : "")
                    }
                  >
                    {prompt.name}
                  </span>
                  <span className="mt-[3px] line-clamp-2 block text-[12.5px] leading-[1.55] text-muted-foreground">
                    {prompt.description === "" ? "（无描述）" : prompt.description}
                  </span>
                </button>
              );
            })}
          </div>
          <p className="pt-3.5 text-[11.5px] text-muted-foreground">
            每条提示词 = 数据目录下的一个 .md 文件
          </p>
        </section>

        {/* ② 编辑器列（430px） */}
        <section
          className="flex w-[430px] shrink-0 flex-col border-l border-border p-5"
          aria-label="提示词编辑列"
        >
          <div className="pt-1 pb-4">
            <h2 className="truncate text-[16.5px] font-semibold">
              {isNewDraft ? "新建提示词" : draftName === "" ? "编辑器" : draftName}
            </h2>
          </div>
          <p className="-mt-2 mb-4 text-[12.5px] text-muted-foreground">
            文件名即名称，改名会同步重命名文件。
          </p>

          <div className="flex min-h-0 flex-1 flex-col">
            <div>
              <Label htmlFor="prompt-name" className="mb-[5px] block">
                名称
              </Label>
              <Input
                id="prompt-name"
                value={draftName}
                placeholder="如 h3-video"
                onInput={(event) => setDraftName(event.currentTarget.value)}
              />
            </div>
            <div className="mt-3.5">
              <Label htmlFor="prompt-desc" className="mb-[5px] block">
                描述
              </Label>
              <Input
                id="prompt-desc"
                value={draftDescription}
                placeholder="这条提示词产出什么、适合什么"
                onInput={(event) => setDraftDescription(event.currentTarget.value)}
              />
            </div>
            <div className="mt-3.5 flex min-h-0 flex-1 flex-col">
              <Label htmlFor="prompt-body" className="mb-[5px] block">
                正文（Markdown）
              </Label>
              <BodyEditor value={draftBody} onChange={setDraftBody} />
            </div>
          </div>

          {editorFeedback !== null && (
            <Alert
              variant={editorFeedback.kind === "error" ? "destructive" : "success"}
              className="mt-3"
            >
              <AlertDescription>{editorFeedback.text}</AlertDescription>
            </Alert>
          )}

          <div className="mt-auto flex items-center gap-2.5 pt-3">
            <span className="text-[11.5px] text-muted-foreground">
              <b
                className={`font-semibold ${byteOver ? "text-destructive" : "text-foreground"}`}
              >
                {bodyKiB} KiB
              </b>{" "}
              / 32 KiB
            </span>
            <span className="flex-1" />
            {!isNewDraft && selectedName !== "" && (
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => setDeleteDialogOpen(true)}
              >
                删除
              </Button>
            )}
            <Button type="button" size="sm" onClick={() => void saveDraft()}>
              保存
            </Button>
          </div>

          <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>删除提示词「{selectedName}」？</DialogTitle>
                <DialogDescription>
                  将连同其历史备份一起移除。此操作不可撤销。
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
                  onClick={() => void deleteSelected()}
                >
                  删除
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </section>

        {/* ③ 对话列（自适应） */}
        <section
          className="flex min-w-0 flex-1 flex-col border-l border-border p-5"
          aria-label="调试对话列"
        >
          <div className="flex items-center justify-end gap-3 pt-0.5 pb-3.5">
            <EndpointSwitcher
              endpoints={endpoints}
              onActivate={(name) => void activateEndpoint(name)}
              onManage={onNavigateToSettings}
            />
            <ShutdownButton />
            <ThemeToggle />
          </div>

          <div className="flex min-w-0 items-center gap-2.5 pb-3">
            <span className="inline-flex max-w-[45%] items-center gap-[7px] rounded-full bg-secondary px-2.5 py-0.5 text-[12px] whitespace-nowrap">
              <span className="truncate">
                {sessionId === null ? "新会话" : `会话 ${sessionId}`}
              </span>
            </span>
            <span className="truncate text-[12px] text-muted-foreground">
              历史自动保存，重启程序后可恢复
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={newSession}
            >
              新会话
            </Button>
          </div>

          {/* 「本轮携带」请求条 */}
          <div className="flex flex-wrap items-center gap-2 pb-3.5">
            <span className="flex-none text-[11.5px] text-muted-foreground">
              本轮携带
            </span>
            <span className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-primary/35 bg-primary/10 px-2.5 text-[12px] font-medium text-primary">
              <FileTextIcon className="size-3" aria-hidden />
              {selectedName === ""
                ? "基础提示词：未选择"
                : `基础提示词：${selectedName}`}
            </span>
            {skillNames.map((name) => (
              <span
                key={name}
                className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-border bg-card px-2.5 text-[12px]"
              >
                {name}
                <button
                  type="button"
                  aria-label={`移除 Skill ${name}`}
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() => toggleSkill(name)}
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            ))}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="inline-flex h-[26px] items-center gap-1 rounded-full border border-dashed border-border px-2.5 text-[12px] text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                >
                  <PlusIcon className="size-3" /> 添加 Skill
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-60">
                <DropdownMenuLabel>勾选注入的 Skill（停用的不可选）</DropdownMenuLabel>
                {skills.length === 0 && (
                  <DropdownMenuLabel>
                    （Skill 库为空——到 设置 → 能力·技能 导入）
                  </DropdownMenuLabel>
                )}
                {skills.map((skill) => (
                  <DropdownMenuCheckboxItem
                    key={skill.name}
                    checked={skillNames.includes(skill.name) && skill.enabled}
                    disabled={!skill.enabled}
                    onSelect={(event) => {
                      event.preventDefault();
                      toggleSkill(skill.name);
                    }}
                  >
                    <span className="truncate">
                      {skill.name}
                      {skill.enabled ? "" : "（已停用）"}
                    </span>
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {/* 消息流 */}
          <div
            role="log"
            aria-label="消息流"
            className="mt-1 min-h-0 flex-1 space-y-[18px] overflow-y-auto pr-1"
          >
            {messages.length === 0 && (
              <p className="mt-8 text-center text-[12px] text-muted-foreground">
                （还没有消息——发一张图 + 指令开始打标）
              </p>
            )}
            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[94%] rounded-xl rounded-br-[4px] bg-primary/10 px-3.5 py-[11px] text-[13.5px] whitespace-pre-wrap">
                    {message.text}
                    {message.attachment !== null && (
                      <span className="mt-2.5 flex items-center gap-2.5 rounded-lg bg-card py-2 pr-3.5 pl-2 text-[12px] text-muted-foreground shadow-sm">
                        <ImageIcon className="size-7 shrink-0 rounded-md bg-muted p-1.5" />
                        <span className="truncate">{message.attachment}</span>
                      </span>
                    )}
                  </div>
                </div>
              ) : (
                <div key={message.id} className="flex items-start">
                  <span
                    className="mt-0.5 mr-2.5 flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground"
                    aria-hidden
                  >
                    <SparklesIcon className="size-3.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="inline-block max-w-full rounded-xl rounded-bl-[4px] bg-muted/55 px-3.5 py-[11px] text-[13.5px] whitespace-pre-wrap">
                      {message.text}
                    </div>
                    <div className="mt-2 flex items-center gap-2.5 text-[11.5px] text-muted-foreground">
                      {(message.model !== undefined ||
                        message.durationSeconds !== undefined) && (
                        <span>
                          {[
                            message.model,
                            message.durationSeconds !== undefined
                              ? `${message.durationSeconds}s`
                              : null,
                          ]
                            .filter((part) => part !== null && part !== undefined)
                            .join(" · ")}
                        </span>
                      )}
                      <button
                        type="button"
                        className="text-primary underline underline-offset-[3px] hover:opacity-80"
                        aria-label="复制 caption"
                        onClick={() => copyCaption(message)}
                      >
                        {copiedId === message.id ? "已复制" : "复制"}
                      </button>
                      {message.createdAt !== undefined && (
                        <span className="ml-auto">
                          {message.createdAt.toLocaleTimeString("zh-CN", {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ),
            )}
          </div>

          {chatError !== "" && (
            <Alert variant="destructive" className="mt-3">
              <AlertDescription>{chatError}</AlertDescription>
            </Alert>
          )}

          {/* 输入区 */}
          <div className="mt-3.5 pt-0">
            <Textarea
              aria-label="打标指令"
              placeholder="输入指令，继续交互……"
              className="min-h-[62px] rounded-lg border-input bg-card px-3.5 py-[11px] text-[13.5px] leading-[1.6]"
              value={instruction}
              onInput={(event) => setInstruction(event.currentTarget.value)}
              onKeyDown={onInstructionKeyDown}
            />
            <div className="mt-2.5 flex items-center gap-2.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <label className="inline-flex h-[30px] cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-[12px] hover:bg-accent">
                    <PaperclipIcon className="size-3.5" />
                    附件
                    <span className="sr-only">附图（一期单张 / 次）</span>
                    <input
                      type="file"
                      accept="image/*"
                      className="sr-only"
                      aria-label="附图"
                      onChange={pickImage}
                    />
                  </label>
                </TooltipTrigger>
                <TooltipContent>附图（一期单张 / 次）</TooltipContent>
              </Tooltip>
              {image !== null && (
                <img
                  className="size-9 rounded-md border border-border object-cover"
                  src={image.dataUrl}
                  alt={`待打标图片 ${image.name}`}
                />
              )}
              <span className="ml-auto text-[11.5px] text-muted-foreground">
                Enter 发送 · Shift+Enter 换行
              </span>
              <Button
                type="button"
                size="sm"
                disabled={!canSend}
                onClick={() => void send()}
              >
                {sending
                  ? waitSeconds < 3
                    ? "已发送，等待模型…"
                    : `等待模型响应…（已 ${waitSeconds}s）`
                  : "发送"}
              </Button>
            </div>
          </div>
        </section>
      </div>
    </TooltipProvider>
  );
}
