/** 策略工作台：组合库、提示词编辑与对话调试。 */
import { ChevronDownIcon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";
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
import { StrategyToolbar } from "./StrategyToolbar";
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
  const [savedPrompt, setSavedPrompt] = useState({
    name: "",
    description: "",
    body: "",
  });
  const [isNewDraft, setIsNewDraft] = useState(false);
  const [editorFeedback, setEditorFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [promptMenuOpen, setPromptMenuOpen] = useState(false);
  const [promptBusy, setPromptBusy] = useState(false);
  const [strategyBusy, setStrategyBusy] = useState(false);

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
  const promptRequestRef = useRef(0);
  const savingPromptRef = useRef(false);
  const interactionRef = useRef(0);

  const selectPrompt = useCallback(async (name: string): Promise<void> => {
    const request = ++promptRequestRef.current;
    const interaction = interactionRef.current;
    try {
      const full = await api.getPrompt(name);
      if (
        request !== promptRequestRef.current ||
        interaction !== interactionRef.current
      )
        return;
      setSelectedName(full.name);
      setDraftName(full.name);
      setDraftDescription(full.description);
      setDraftBody(full.body);
      setSavedPrompt(full);
      setIsNewDraft(false);
      setEditorFeedback(null);
    } catch (err) {
      if (
        request !== promptRequestRef.current ||
        interaction !== interactionRef.current
      )
        return;
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    }
  }, []);

  useEffect(
    () => () => {
      promptRequestRef.current += 1;
    },
    [],
  );

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
        if (
          !restoredPromptRef.current &&
          interactionRef.current === 0 &&
          first !== undefined
        ) {
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
    const interaction = interactionRef.current;
    void (async () => {
      try {
        const snapshot = await api.latestSession();
        if (cancelled || interaction !== interactionRef.current) {
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
    interactionRef.current += 1;
    promptRequestRef.current += 1;
    setSelectedName("");
    setDraftName("");
    setDraftDescription("");
    setDraftBody("");
    setSavedPrompt({ name: "", description: "", body: "" });
    setIsNewDraft(true);
    setPromptMenuOpen(false);
    setEditorFeedback(null);
    bodyInputRef.current?.focus();
  };

  /** 保存草稿：名称改动过 = 先重命名（改文件名）再写内容，旧名不再保留。 */
  const saveDraft = async (): Promise<void> => {
    if (savingPromptRef.current) return;
    const name = draftName.trim();
    if (name === "") {
      setEditorFeedback({ kind: "error", text: "名称不能为空；请填写提示词名称。" });
      return;
    }
    savingPromptRef.current = true;
    setPromptBusy(true);
    try {
      if (!isNewDraft && selectedName !== "" && name !== selectedName) {
        await api.renamePrompt(selectedName, { new_name: name });
        setSelectedName(name);
      }
      await api.savePrompt(name, {
        description: draftDescription,
        body: draftBody,
      });
      setIsNewDraft(false);
      setSelectedName(name);
      setDraftName(name);
      setSavedPrompt({ name, description: draftDescription, body: draftBody });
      setEditorFeedback({ kind: "success", text: `已保存提示词「${name}」` });
      await reloadPrompts();
    } catch (err) {
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      savingPromptRef.current = false;
      setPromptBusy(false);
    }
  };

  const deleteSelected = async (): Promise<void> => {
    if (deleteName === "" || savingPromptRef.current) {
      return;
    }
    savingPromptRef.current = true;
    setPromptBusy(true);
    try {
      await api.deletePrompt(deleteName);
      if (deleteName === selectedName) startNewDraft();
      setEditorFeedback({ kind: "success", text: `已删除「${deleteName}」` });
      setDeleteDialogOpen(false);
      await reloadPrompts();
    } catch (err) {
      setEditorFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      savingPromptRef.current = false;
      setPromptBusy(false);
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
    interactionRef.current += 1;
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
  const promptDirty =
    draftName !== savedPrompt.name ||
    draftDescription !== savedPrompt.description ||
    draftBody !== savedPrompt.body;

  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col">
        <StrategyToolbar
          references={{
            endpoint: endpoints.find((entry) => entry.is_active)?.name ?? "",
            prompt: selectedName,
            skills: skillNames,
          }}
          prompts={prompts}
          skills={skills}
          endpoints={endpoints}
          locked={promptDirty || sending || promptBusy || strategyBusy}
          onSelect={async (strategy) => {
            interactionRef.current += 1;
            const request = ++promptRequestRef.current;
            setStrategyBusy(true);
            try {
              const full = await api.getPrompt(strategy.prompt);
              if (request !== promptRequestRef.current)
                throw new Error("当前编辑状态已改变，请重新选择策略");
              await api.activateEndpoint(strategy.endpoint);
              if (request !== promptRequestRef.current)
                throw new Error("当前编辑状态已改变，请重新选择策略");
              restoredPromptRef.current = true;
              setSelectedName(full.name);
              setDraftName(full.name);
              setDraftDescription(full.description);
              setDraftBody(full.body);
              setSavedPrompt(full);
              setIsNewDraft(false);
              setSkillNames(strategy.skills);
              setEndpoints((current) =>
                current.map((entry) => ({
                  ...entry,
                  is_active: entry.name === strategy.endpoint,
                })),
              );
              setActiveModel(
                endpoints.find((entry) => entry.name === strategy.endpoint)?.model ??
                  "",
              );
            } finally {
              setStrategyBusy(false);
            }
          }}
        />
        <fieldset
          disabled={strategyBusy || promptBusy || sending}
          className="grid min-h-0 min-w-0 flex-1 grid-cols-1 overflow-auto lg:grid-cols-2 lg:overflow-hidden"
        >
          <section
            className="flex min-h-80 min-w-0 flex-col px-6 py-4 lg:min-h-0"
            aria-label="提示词编辑列"
          >
            <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card px-6 py-4">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="text-t-sm text-text-3">提示词</span>
                <div className="relative flex min-w-0 max-w-full flex-1 items-center">
                  <input
                    id="prompt-name"
                    aria-label="名称"
                    value={draftName}
                    placeholder="新建提示词"
                    className="min-w-0 w-full rounded-md border border-transparent bg-transparent py-1 pr-7 pl-1 text-t-xl font-medium hover:border-input focus:border-n-400"
                    onInput={(event) => {
                      interactionRef.current += 1;
                      setDraftName(event.currentTarget.value);
                    }}
                  />
                  <DropdownMenu open={promptMenuOpen} onOpenChange={setPromptMenuOpen}>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="absolute right-0 size-6"
                        aria-label="切换提示词"
                        disabled={promptDirty}
                      >
                        <ChevronDownIcon />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      align="start"
                      className="w-80 max-w-[calc(100vw-32px)]"
                    >
                      <div className="flex items-center justify-between px-2 py-1 text-t-xs text-text-4">
                        <span>提示词库 · 共 {prompts.length} 条</span>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label="新建提示词"
                              onClick={startNewDraft}
                            >
                              <PlusIcon />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>新建提示词</TooltipContent>
                        </Tooltip>
                      </div>
                      <div className="max-h-80 overflow-y-auto">
                        {prompts.map((prompt) => (
                          <div
                            key={prompt.name}
                            className="flex items-center gap-1 rounded-md p-2 hover:bg-accent"
                          >
                            <button
                              type="button"
                              className="min-w-0 flex-1 text-left"
                              aria-label={`选择提示词 ${prompt.name}`}
                              onClick={() => {
                                interactionRef.current += 1;
                                setPromptMenuOpen(false);
                                void selectPrompt(prompt.name);
                              }}
                            >
                              <span className="block truncate text-t-md font-medium">
                                {prompt.name}
                              </span>
                              <span className="block truncate text-t-sm text-text-3">
                                {prompt.description}
                              </span>
                            </button>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="text-bad-ink"
                                  aria-label={`删除提示词 ${prompt.name}`}
                                  onClick={() => {
                                    setDeleteName(prompt.name);
                                    setDeleteDialogOpen(true);
                                    setPromptMenuOpen(false);
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
                {promptDirty && <span className="text-t-xs text-warn-ink">未保存</span>}
                <Button
                  type="button"
                  size="sm"
                  disabled={byteOver}
                  onClick={() => void saveDraft()}
                >
                  保存
                </Button>
              </div>
              <div className="mt-4">
                <Label htmlFor="prompt-desc" className="mb-2 block">
                  描述
                </Label>
                <Input
                  id="prompt-desc"
                  value={draftDescription}
                  onInput={(event) => {
                    interactionRef.current += 1;
                    setDraftDescription(event.currentTarget.value);
                  }}
                />
              </div>
              <div className="mt-4 flex min-h-0 flex-1 flex-col">
                <div className="mb-2 flex items-baseline gap-2 text-t-sm">
                  <span>正文</span>
                  <span
                    className={`text-t-xs tabular-nums ${byteOver ? "text-bad-ink" : "text-text-4"}`}
                  >
                    {bodyKiB} KiB / 32 KiB
                  </span>
                </div>
                <BodyEditor
                  value={draftBody}
                  onChange={(body) => {
                    interactionRef.current += 1;
                    setDraftBody(body);
                  }}
                />
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

            <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>删除提示词「{deleteName}」？</DialogTitle>
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

          <section
            className="flex min-h-80 min-w-0 flex-col border-t border-border px-6 py-4 lg:min-h-0 lg:border-t-0 lg:border-l"
            aria-label="调试对话列"
          >
            <div className="flex min-w-0 items-center gap-3 pb-3">
              <h2 className="shrink-0 text-t-xl font-semibold">对话</h2>
              <EndpointSwitcher
                endpoints={endpoints}
                onActivate={(name) => void activateEndpoint(name)}
                onManage={onNavigateToSettings}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="新会话"
                    className="ml-auto"
                    onClick={newSession}
                  >
                    <PlusIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>新会话</TooltipContent>
              </Tooltip>
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
              actions={
                <DropdownMenu>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" aria-label="添加 Skill">
                          <PlusIcon />
                        </Button>
                      </DropdownMenuTrigger>
                    </TooltipTrigger>
                    <TooltipContent>添加 Skill</TooltipContent>
                  </Tooltip>
                  <DropdownMenuContent
                    side="top"
                    align="start"
                    className="max-h-60 w-64 overflow-y-auto"
                  >
                    <DropdownMenuLabel>
                      Skill 库 · 共 {skills.length} 条
                    </DropdownMenuLabel>
                    {skills.map((skill) => (
                      <DropdownMenuCheckboxItem
                        key={skill.name}
                        checked={skillNames.includes(skill.name)}
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
              }
              selectedSkills={
                <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
                  {skillNames.map((name) => (
                    <span
                      key={name}
                      className="inline-flex h-(--h-xs) shrink-0 items-center gap-1 rounded-full border border-border px-2 text-t-sm"
                    >
                      {name}
                      <button
                        type="button"
                        aria-label={`移除 Skill ${name}`}
                        onClick={() => toggleSkill(name)}
                      >
                        <XIcon className="size-3.5" />
                      </button>
                    </span>
                  ))}
                </div>
              }
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
        </fieldset>
      </div>
    </TooltipProvider>
  );
}
