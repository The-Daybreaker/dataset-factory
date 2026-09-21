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
import { ApiError, api } from "../../api";
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
import { EndpointSwitcher } from "./EndpointSwitcher";
import { InputArea } from "./InputArea";
import { MessageList } from "./MessageList";
import { StrategyToolbar } from "./StrategyToolbar";
import type { ChatMessage, PendingMedia } from "./types";

/** 基础提示词的字节护栏（对齐 Codex project_doc_max_bytes，后端同值校验）。 */
const PROMPT_BYTE_BUDGET = 32 * 1024;

/** 抽视频首帧与时长（L26/V8）：本地 <video> 解码，失败静默降级为图标（不打扰发送）。 */
function captureVideoMeta(
  dataUrl: string,
): Promise<{ posterUrl?: string; durationSec?: number }> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    let settled = false;
    const finish = (meta: { posterUrl?: string; durationSec?: number }): void => {
      if (settled) return;
      settled = true;
      video.removeAttribute("src");
      resolve(meta);
    };
    const timer = window.setTimeout(() => finish({}), 3000);
    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => {
      const duration = Number.isFinite(video.duration) ? video.duration : undefined;
      video.onseeked = () => {
        window.clearTimeout(timer);
        try {
          const canvas = document.createElement("canvas");
          canvas.width = video.videoWidth || 160;
          canvas.height = video.videoHeight || 90;
          const context = canvas.getContext("2d");
          context?.drawImage(video, 0, 0, canvas.width, canvas.height);
          finish({
            posterUrl: canvas.toDataURL("image/jpeg", 0.7),
            durationSec: duration,
          });
        } catch {
          finish({ durationSec: duration });
        }
      };
      video.currentTime = Math.min(0.1, duration ?? 0.1);
    };
    video.onerror = () => {
      window.clearTimeout(timer);
      finish({});
    };
    video.src = dataUrl;
  });
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
  const [endpointBusy, setEndpointBusy] = useState(false);

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
  const activatingEndpointRef = useRef(false);
  const sendingRef = useRef(false);
  const mediaRequestRef = useRef(0);
  // 「停止生成」（N1④）：发送期间持有的 AbortController，停止钮触发即中止本轮。
  const abortRef = useRef<AbortController | null>(null);

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
          setSessionId(null);
          setMessages([]);
          setChatError("");
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
    [failEditor],
  );

  useEffect(
    () => () => {
      promptRequestRef.current += 1;
      interactionRef.current += 1;
      mediaRequestRef.current += 1;
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
        // 历史附件直连会话附件端点（B5）：缩略图不再依赖内存 dataURL，刷新不丢。
        setMessages(
          snapshot.messages.map((item, index) => ({
            ...item,
            id: index,
            ...(item.attachment !== null
              ? {
                  attachmentUrl: api.sessionAttachmentUrl(
                    snapshot.session_id,
                    item.attachment,
                  ),
                }
              : {}),
          })),
        );
        if (snapshot.settings.prompt_name !== null) {
          await selectPrompt(snapshot.settings.prompt_name);
        }
      } catch (err) {
        const noSessionYet = err instanceof ApiError && err.status === 404;
        if (!cancelled && interaction === interactionRef.current && !noSessionYet) {
          setChatError(reportError(err) ?? "");
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
    if (activatingEndpointRef.current || sendingRef.current || strategyBusy) return;
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

  const toggleSkill = (name: string): void => {
    if (sendingRef.current || strategyBusy || activatingEndpointRef.current) return;
    interactionRef.current += 1;
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
    interactionRef.current += 1;
    const request = ++mediaRequestRef.current;
    const isVideo = file.type.startsWith("video/");
    const reader = new FileReader();
    reader.onload = () => {
      if (request !== mediaRequestRef.current) return;
      const dataUrl = String(reader.result);
      const base = {
        name: file.name,
        dataUrl,
        kind: (isVideo ? "video" : "image") as "video" | "image",
        mime: file.type,
        byteSize: file.size,
        fps: 2,
        maxFrames: 16,
      };
      // 附件卡先上屏（视频封面 / 时长异步后补，不挡操作）。
      setMedia(base);
      if (!isVideo) return;
      void captureVideoMeta(dataUrl).then((meta) => {
        if (request !== mediaRequestRef.current) return;
        setMedia((current) =>
          current !== null && current.dataUrl === dataUrl
            ? { ...current, posterUrl: meta.posterUrl, durationSec: meta.durationSec }
            : current,
        );
      });
    };
    reader.onerror = () => {
      if (request === mediaRequestRef.current)
        setChatError("附件读取失败，请重新选择。");
    };
    reader.readAsDataURL(file);
  };

  const send = async (): Promise<void> => {
    if (
      sendingRef.current ||
      activatingEndpointRef.current ||
      strategyBusy ||
      savingPromptRef.current ||
      (instruction.trim() === "" && media === null)
    ) {
      return;
    }
    interactionRef.current += 1;
    mediaRequestRef.current += 1;
    sendingRef.current = true;
    setSending(true);
    setChatError("");
    const startedAt = Date.now();
    // 发送前取定值，随后立刻清输入（N1①，2026-09-21 审计：输入框不再等模型说完才清，
    // 失败路径同样清——失败的那句话已进气泡，输入框里挂着旧话只会诱发重复发送）。
    const sentInstruction = instruction;
    const sentMedia = media;
    const sentAttachment = media?.name ?? null;
    const controller = new AbortController();
    abortRef.current = controller;
    // 用户消息先上屏（乐观更新）；回复走流式增量，done 后再落终稿消息。
    setMessages((current) => [
      ...current,
      {
        id: current.length,
        role: "user",
        partial: false,
        text: sentInstruction,
        attachment: sentAttachment,
        ...(sentMedia !== null
          ? {
              attachmentDataUrl: sentMedia.dataUrl,
              ...(sentMedia.kind === "video"
                ? { attachmentDurationSec: sentMedia.durationSec }
                : {}),
            }
          : {}),
      },
    ]);
    setInstruction("");
    setMedia(null);
    setStreaming({ reasoning: "", content: "" });
    let reasoningText = "";
    let contentText = "";
    let firstContentAt: number | null = null;
    /** 断流 / 报错 / 主动停止：已收到的半截也留痕（B5 + N1 同源①），不整段丢弃。 */
    const keepPartial = (): void => {
      if (contentText === "" && reasoningText === "") {
        return;
      }
      setMessages((current) => [
        ...current,
        {
          id: current.length,
          role: "assistant",
          partial: true,
          text: contentText,
          attachment: null,
          ...(reasoningText === "" ? {} : { reasoning: reasoningText }),
        },
      ]);
    };
    try {
      await api.labelStream(
        {
          session_id: sessionId,
          prompt_name: selectedName === "" ? null : selectedName,
          skill_names: skillNames,
          instruction: sentInstruction,
          image_base64: sentMedia?.kind === "image" ? sentMedia.dataUrl : null,
          image_name: sentMedia?.kind === "image" ? sentMedia.name : "image.png",
          video_base64: sentMedia?.kind === "video" ? sentMedia.dataUrl : null,
          video_name: sentMedia?.kind === "video" ? sentMedia.name : "video.mp4",
          video_fps: sentMedia?.kind === "video" ? sentMedia.fps : 2,
          video_max_frames: sentMedia?.kind === "video" ? sentMedia.maxFrames : 16,
        },
        {
          onStart: (id) => setSessionId(id),
          onDelta: (kind, text) => {
            // 思考增量另存一份到局部变量：done 时挂到消息上（结束后保留可回看）；
            // 首个正文增量的时刻 = 思考耗时（V7 的本地口径）。
            if (kind === "reasoning") {
              reasoningText += text;
            } else {
              contentText += text;
              if (firstContentAt === null) {
                firstContentAt = Date.now();
              }
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
                partial: false,
                text: caption,
                attachment: null,
                model: activeModel || undefined,
                durationSeconds: Math.round((Date.now() - startedAt) / 1000),
                reasoningSeconds:
                  firstContentAt !== null
                    ? Math.max(1, Math.round((firstContentAt - startedAt) / 1000))
                    : undefined,
                createdAt: new Date(),
                ...(reasoningText === "" ? {} : { reasoning: reasoningText }),
              },
            ]);
            setSessionId(id);
            // 与追加消息同一同步块里清流式面板：合并成一次提交，避免「终稿 + 流式面板」
            // 短暂同屏一帧（e2e 严格模式抓到过）。finally 的清算是错误路径兜底。
            setStreaming(null);
          },
          onError: (message) => {
            keepPartial();
            setChatError(message);
          },
        },
        controller.signal,
      );
    } catch (err) {
      keepPartial();
      // 用户主动停止不当失败展示（停止本身就是那轮的结局）。
      if (!controller.signal.aborted) {
        setChatError(reportError(err) ?? "");
      }
    } finally {
      abortRef.current = null;
      sendingRef.current = false;
      setSending(false);
      setStreaming(null);
    }
  };

  /** 停止生成（N1④）：中止当前请求；已收到的部分按半截消息留痕。 */
  const stopGeneration = (): void => {
    abortRef.current?.abort();
  };

  /** 清空当前会话（Q4 改名）：只清内存与界面——旧会话仍完整保存在磁盘上。 */
  const newSession = (): void => {
    interactionRef.current += 1;
    mediaRequestRef.current += 1;
    setSessionId(null);
    setMessages([]);
    setChatError("");
    // 旧输入 / 旧附件 / 旧 Skill 不带进新会话（N1 同源②）。
    setInstruction("");
    setMedia(null);
    setSkillNames([]);
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
              // 切策略 = 换端点 + 提示词 + Skill 的整套口径（N1 同源③）：旧对话的
              // 产出来自旧配置，接着聊只会混淆出处——直接重开会话。
              setSessionId(null);
              setMessages([]);
              setChatError("");
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
                    onClick={newSession}
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
              onInstructionChange={(value) => {
                interactionRef.current += 1;
                setInstruction(value);
              }}
              onInstructionKeyDown={onInstructionKeyDown}
              media={media}
              onPickMedia={pickMedia}
              onMediaFpsChange={setMediaFps}
              onMediaMaxFramesChange={setMediaMaxFrames}
              onClearMedia={() => {
                mediaRequestRef.current += 1;
                setMedia(null);
              }}
              canSend={canSend}
              sending={sending}
              waitSeconds={waitSeconds}
              onSend={() => void send()}
              onStop={stopGeneration}
            />
          </section>
        </fieldset>
      </div>
    </TooltipProvider>
  );
}
