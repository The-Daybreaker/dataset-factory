/**
 * 提示词工作台（无全局页头、三列满高、列间竖装饰线——原型稿 ui-draft-05 为视觉事实源）：
 * ① 列表列（270px）：提示词卡片 + 计数 + 新建；选中的卡片即本轮打标的基础提示词。
 * ② 编辑器列（430px）：名称 / 描述 / Markdown 正文（等宽 + 行号槽）+ 字节计量 + 保存；
 *    改名保存 = 重命名文件（历史备份随迁）。
 * ③ 对话列（自适应）：端点配置切换器 + 主题切换、会话行、「本轮携带」请求条、消息流、输入区。
 * 子组件按 feature 目录拆分：EndpointSwitcher / BodyEditor / MessageList / InputArea。
 */
import { FileTextIcon, PlusIcon, XIcon } from "lucide-react";
import {
  type ChangeEvent,
  type KeyboardEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { EndpointConfigSummary, PromptInfo, SkillInfo } from "../../api";
import { ApiError, api, errorMessage } from "../../api";
import { ShutdownButton } from "../../components/shutdown-button";
import { ThemeToggle } from "../../components/theme-toggle";
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
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../../components/ui/tooltip";
import { BodyEditor } from "./BodyEditor";
import { EndpointSwitcher } from "./EndpointSwitcher";
import { InputArea } from "./InputArea";
import { MessageList } from "./MessageList";
import type { ChatMessage, PendingMedia } from "./types";

/** 基础提示词的字节护栏（对齐 Codex project_doc_max_bytes，后端同值校验）。 */
const PROMPT_BYTE_BUDGET = 32 * 1024;

/** 一次性反馈（编辑器列的操作结果）；id 让同文案重复出现也能触发重渲染。 */
interface Feedback {
  kind: "success" | "error";
  text: string;
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
  const [media, setMedia] = useState<PendingMedia | null>(null);
  const [sending, setSending] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [chatError, setChatError] = useState("");
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [streaming, setStreaming] = useState<{
    reasoning: string;
    content: string;
  } | null>(null);
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

  const pickMedia = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file === undefined) {
      return;
    }
    const isVideo = file.type.startsWith("video/");
    const reader = new FileReader();
    reader.onload = () => {
      setMedia({
        name: file.name,
        dataUrl: String(reader.result),
        kind: isVideo ? "video" : "image",
        fps: 2,
        maxFrames: 16,
      });
    };
    reader.readAsDataURL(file);
  };

  const send = async (): Promise<void> => {
    if (sending || (instruction.trim() === "" && media === null)) {
      return;
    }
    setSending(true);
    setChatError("");
    const startedAt = Date.now();
    const sentAttachment = media?.name ?? null;
    // 用户消息先上屏（乐观更新）；回复走流式增量，done 后再落终稿消息。
    setMessages((current) => [
      ...current,
      {
        id: current.length,
        role: "user",
        text: instruction,
        attachment: sentAttachment,
      },
    ]);
    setStreaming({ reasoning: "", content: "" });
    let reasoningText = "";
    try {
      await api.labelStream(
        {
          session_id: sessionId,
          prompt_name: selectedName === "" ? null : selectedName,
          skill_names: skillNames,
          instruction,
          image_base64: media?.kind === "image" ? media.dataUrl : null,
          image_name: media?.kind === "image" ? media.name : "image.png",
          video_base64: media?.kind === "video" ? media.dataUrl : null,
          video_name: media?.kind === "video" ? media.name : "video.mp4",
          video_fps: media?.kind === "video" ? media.fps : 2,
          video_max_frames: media?.kind === "video" ? media.maxFrames : 16,
        },
        {
          onStart: (id) => setSessionId(id),
          onDelta: (kind, text) => {
            // 思考增量另存一份到局部变量：done 时挂到消息上（结束后保留可回看）。
            if (kind === "reasoning") {
              reasoningText += text;
            }
            setStreaming((current) =>
              current === null
                ? current
                : kind === "reasoning"
                  ? { ...current, reasoning: current.reasoning + text }
                  : { ...current, content: current.content + text },
            );
          },
          onDone: (id, caption) => {
            setMessages((current) => [
              ...current,
              {
                id: current.length,
                role: "assistant",
                text: caption,
                attachment: null,
                model: activeModel || undefined,
                durationSeconds: Math.round((Date.now() - startedAt) / 1000),
                createdAt: new Date(),
                ...(reasoningText === "" ? {} : { reasoning: reasoningText }),
              },
            ]);
            setSessionId(id);
            setInstruction("");
            setMedia(null);
            // 与追加消息同一同步块里清流式面板：合并成一次提交，避免「终稿 + 流式面板」
            // 短暂同屏一帧（e2e 严格模式抓到过）。finally 的清算是错误路径兜底。
            setStreaming(null);
          },
          onError: (message) => setChatError(message),
        },
      );
    } catch (err) {
      setChatError(errorMessage(err));
    } finally {
      setSending(false);
      setStreaming(null);
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

  // 抽帧参数改动走函数式更新：值已在 InputArea 的事件处理里同步取出，这里只合成新 media。
  const setMediaFps = (fps: number): void => {
    setMedia((current) => (current === null ? current : { ...current, fps }));
  };
  const setMediaMaxFrames = (maxFrames: number): void => {
    setMedia((current) => (current === null ? current : { ...current, maxFrames }));
  };

  const bodyBytes = new TextEncoder().encode(draftBody).length;
  const byteOver = bodyBytes > PROMPT_BYTE_BUDGET;
  const canSend = !sending && (instruction.trim() !== "" || media !== null);
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
                  <span
                    className={
                      "mt-[3px] line-clamp-2 block text-[12.5px] leading-[1.55] " +
                      (prompt.description.startsWith("文件损坏：")
                        ? "text-destructive"
                        : "text-muted-foreground")
                    }
                  >
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
                    <span
                      className={
                        "truncate" +
                        (skill.description.startsWith("文件损坏：")
                          ? " text-destructive"
                          : "")
                      }
                    >
                      {skill.name}
                      {skill.enabled ? "" : "（已停用）"}
                    </span>
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {/* 消息流 */}
          <MessageList
            messages={messages}
            streaming={streaming}
            copiedId={copiedId}
            onCopy={copyCaption}
          />

          {chatError !== "" && (
            <Alert variant="destructive" className="mt-3">
              <AlertDescription>{chatError}</AlertDescription>
            </Alert>
          )}

          {/* 输入区 */}
          <InputArea
            instruction={instruction}
            onInstructionChange={setInstruction}
            onInstructionKeyDown={onInstructionKeyDown}
            media={media}
            onPickMedia={pickMedia}
            onMediaFpsChange={setMediaFps}
            onMediaMaxFramesChange={setMediaMaxFrames}
            onClearMedia={() => setMedia(null)}
            canSend={canSend}
            sending={sending}
            waitSeconds={waitSeconds}
            onSend={() => void send()}
          />
        </section>
      </div>
    </TooltipProvider>
  );
}
