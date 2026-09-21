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
import { api } from "../../api";
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
  Tip,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../../components/ui/tooltip";
import type { Feedback } from "../../lib/feedback";
import { reportError } from "../../lib/feedback";
import { formatBytes } from "../../lib/format";
import { BodyEditor } from "./BodyEditor";
import { useChatSession } from "./chat-session";
import { EndpointSwitcher } from "./EndpointSwitcher";
import { InputArea } from "./InputArea";
import { MessageList } from "./MessageList";
import { StrategyToolbar } from "./StrategyToolbar";

/** 基础提示词的字节护栏（对齐 Codex project_doc_max_bytes，后端同值校验）。 */
const PROMPT_BYTE_BUDGET = 32 * 1024;

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
  const [endpointBusy, setEndpointBusy] = useState(false);

  // ---------- 对话列（状态与逻辑住在 App 级会话域：切页卸载本组件不打断流式生成） ----------
  const {
    messages,
    streaming,
    copiedId,
    skillNames,
    instruction,
    media,
    sending,
    waitSeconds,
    chatError,
    restoreState,
    restoredPromptName,
    send,
    stopGeneration,
    newSession,
    clearConversation,
    toggleSkill,
    applySkillNames,
    setInstruction,
    pickMedia,
    setMediaFps,
    setMediaMaxFrames,
    clearMedia,
    copyCaption,
    setChatError,
  } = useChatSession();

  // ---------- 端点配置（页面职责：发送时刻把 activeModel / promptName 传给会话域） ----------
  const [endpoints, setEndpoints] = useState<EndpointConfigSummary[]>([]);
  const [activeModel, setActiveModel] = useState("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const bodyInputRef = useRef<HTMLTextAreaElement>(null);
  // 会话恢复是否带回了基础提示词：带回了就不做「自动选中首条」（恢复优先于默认）。
  const restoredPromptRef = useRef(false);
  const promptRequestRef = useRef(0);
  const savingPromptRef = useRef(false);
  const interactionRef = useRef(0);
  const activatingEndpointRef = useRef(false);

  /** 失败分流：连接类失败改弹浮层（不占界面位置），后端返回的业务错误仍就地展示。 */
  const failEditor = useCallback((err: unknown): void => {
    const text = reportError(err);
    if (text !== null) setEditorFeedback({ kind: "error", text });
  }, []);

  const selectPrompt = useCallback(
    async (name: string, options?: { resetSession?: boolean }): Promise<void> => {
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
        if (options?.resetSession === true) {
          // 切提示词 = 下一轮换 system 底座（N1 同源③）：旧对话接着新配置只会
          // 让产出来源混乱——直接重开会话，界面上的清空就是最直白的告知。
          clearConversation();
        }
      } catch (err) {
        if (
          request !== promptRequestRef.current ||
          interaction !== interactionRef.current
        )
          return;
        failEditor(err);
      }
    },
    [clearConversation, failEditor],
  );

  useEffect(
    () => () => {
      promptRequestRef.current += 1;
      interactionRef.current += 1;
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
          failEditor(err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectPrompt, failEditor]);

  // 会话恢复带回了基础提示词：带回了就不做「自动选中首条」（恢复优先于默认），
  // 也不覆盖用户已选 / 已编辑的草稿——用户动过手（interactionRef > 0）就让位。
  useEffect(() => {
    if (restoreState !== "restored") return;
    restoredPromptRef.current = true;
    if (restoredPromptName === null || interactionRef.current !== 0) return;
    void selectPrompt(restoredPromptName);
  }, [restoreState, restoredPromptName, selectPrompt]);

  const reloadPrompts = useCallback(async (): Promise<void> => {
    try {
      setPrompts(await api.listPrompts());
    } catch (err) {
      failEditor(err);
    }
  }, [failEditor]);

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
      failEditor(err);
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
      failEditor(err);
    } finally {
      savingPromptRef.current = false;
      setPromptBusy(false);
    }
  };

  const activateEndpoint = async (name: string): Promise<void> => {
    if (activatingEndpointRef.current || sending || strategyBusy) return;
    interactionRef.current += 1;
    activatingEndpointRef.current = true;
    setEndpointBusy(true);
    setChatError("");
    try {
      await api.activateEndpoint(name);
      setEndpoints((current) =>
        current.map((item) => ({ ...item, is_active: item.name === name })),
      );
      setActiveModel(endpoints.find((item) => item.name === name)?.model ?? "");
    } catch (err) {
      setChatError(reportError(err) ?? "");
    } finally {
      activatingEndpointRef.current = false;
      setEndpointBusy(false);
    }
  };

  const handleToggleSkill = (name: string): void => {
    if (sending || strategyBusy || activatingEndpointRef.current) return;
    interactionRef.current += 1;
    toggleSkill(name);
  };

  const handlePickMedia = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file !== undefined) {
      interactionRef.current += 1;
    }
    pickMedia(file);
  };

  /** 发送（配置侧收口）：忙态守卫在这里，会话域只管发送本身与重入守卫。 */
  const handleSend = (): void => {
    if (endpointBusy || strategyBusy || promptBusy) return;
    interactionRef.current += 1;
    send({ promptName: selectedName === "" ? null : selectedName, activeModel });
  };

  // 中文输入法的回车上屏不属于「发送」（isComposing 判定），Shift+Enter 换行。
  const onInstructionKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

  const bodyBytes = new TextEncoder().encode(draftBody).length;
  const byteOver = bodyBytes > PROMPT_BYTE_BUDGET;
  const controlsBusy = sending || endpointBusy || strategyBusy || promptBusy;
  const canSend = !controlsBusy && (instruction.trim() !== "" || media !== null);
  const bodySize = formatBytes(bodyBytes, "KiB", 1);
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
          locked={promptDirty || controlsBusy}
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
              applySkillNames(strategy.skills);
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
              // 切策略 = 换端点 + 提示词 + Skill 的整套口径（N1 同源③）：旧对话的
              // 产出来自旧配置，接着聊只会混淆出处——直接重开会话。
              clearConversation();
            } finally {
              setStrategyBusy(false);
            }
          }}
        />
        <fieldset
          // N1④（2026-09-21 审计）：发送中只锁配置类操作、不锁整页——「能打字 /
          // 能挂附件 / 能切端点」是等待 125 秒时最基本的自由；sending 不再参与禁用。
          disabled={endpointBusy || strategyBusy || promptBusy}
          className="grid min-h-0 min-w-0 flex-1 grid-cols-1 overflow-auto lg:grid-cols-2 lg:overflow-hidden"
        >
          <section
            className="flex min-h-80 min-w-0 flex-col px-6 py-4 lg:min-h-0"
            aria-label="提示词编辑列"
          >
            <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card px-6 py-4">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="text-t-sm text-text-3">提示词</span>
                <div className="relative flex min-w-0 max-w-full items-center">
                  <input
                    id="prompt-name"
                    aria-label="名称"
                    value={draftName}
                    placeholder="新建提示词"
                    className="min-w-24 max-w-full field-sizing-content rounded-md border border-transparent bg-transparent py-1 pr-7 pl-1 text-t-xl font-medium hover:border-input focus:border-n-400"
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
                                void selectPrompt(prompt.name, { resetSession: true });
                              }}
                            >
                              <span className="block truncate text-t-md font-medium">
                                {prompt.name}
                              </span>
                              <span className="block truncate text-t-xs text-n-500">
                                {prompt.description}
                              </span>
                            </button>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
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
                <span className="flex-1" />
                <Tip label={promptDirty ? "" : "没有未保存的修改"}>
                  <Button
                    type="button"
                    size="sm"
                    variant={promptDirty ? "default" : "ghost"}
                    disabled={byteOver || !promptDirty}
                    onClick={() => void saveDraft()}
                  >
                    保存
                  </Button>
                </Tip>
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
                <div className="mb-2 flex items-baseline gap-2">
                  <span className="text-t-md font-medium text-foreground">正文</span>
                  <span
                    className={`text-t-xs font-medium tabular-nums ${byteOver ? "text-bad-ink" : "text-muted-foreground"}`}
                  >
                    {bodySize} / 32 KiB
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
                disabled={endpointBusy || strategyBusy || promptBusy}
                onActivate={(name) => void activateEndpoint(name)}
                onManage={onNavigateToSettings}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label="清空当前会话"
                    className="ml-auto"
                    onClick={() => {
                      interactionRef.current += 1;
                      newSession();
                    }}
                  >
                    <PlusIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>清空当前会话（旧会话仍保存在磁盘上）</TooltipContent>
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
                        disabled={!skill.enabled || controlsBusy}
                        onSelect={(event) => {
                          event.preventDefault();
                          handleToggleSkill(skill.name);
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
                        onClick={() => handleToggleSkill(name)}
                      >
                        <XIcon className="size-3.5" />
                      </button>
                    </span>
                  ))}
                </div>
              }
              instruction={instruction}
              onInstructionChange={(value) => {
                interactionRef.current += 1;
                setInstruction(value);
              }}
              onInstructionKeyDown={onInstructionKeyDown}
              media={media}
              onPickMedia={handlePickMedia}
              onMediaFpsChange={setMediaFps}
              onMediaMaxFramesChange={setMediaMaxFrames}
              onClearMedia={clearMedia}
              canSend={canSend}
              sending={sending}
              waitSeconds={waitSeconds}
              onSend={handleSend}
              onStop={stopGeneration}
            />
          </section>
        </fieldset>
      </div>
    </TooltipProvider>
  );
}
