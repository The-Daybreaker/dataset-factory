/**
 * 对话会话域：会话状态与流式发送逻辑住在 App 层的 Provider 里。
 *
 * 历史上三个页面是条件渲染，切到打标页会把 PromptWorkbench 整个卸载——会话状态
 * 跟着组件走时，进行中的流式回调把结果写进已卸载组件的 state（静默 no-op），回复
 * 就此在界面上消失（后端其实已落盘，刷新才看得见）。状态住到 App 层后，切页对流式
 * 生成完全无感。三期起页面改 Activity 保活、切页不再卸载，这层上提依然保留：会话
 * 的生命周期本来就比任何一页长，层级与「哪页在显示」解耦，不依赖保活细节。
 *
 * 会话归属（三期 v3，2026-09-22 用户实测定案）：每个会话在创建时盖 strategy_id 章
 * （后端 meta.json），「会话属于谁」以归属为准——签名（提示词 + Skill 组合）只是
 * 无归属时代的近似，已退役。本域维护「当前桶」：启动按策略镜像进桶，切策略 / 新建
 * 策略即换桶（拉该桶最近会话，有则接上、无则空白）；发送把桶 id 传给后端盖章。
 * 输入草稿按桶分键，切策略互不串。
 */
import {
  createContext,
  type ReactElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { ApiError, api } from "../../api";
import { reportError } from "../../lib/feedback";
import {
  chatInstructionKey,
  isStrategySelection,
  NEW_STRATEGY_ID,
  readStoredJson,
  readStoredString,
  WORKBENCH_STRATEGY_KEY,
  writeStoredJson,
} from "../../lib/ui-storage";
import type { ChatMessage, PendingMedia } from "./types";

type SessionSnapshot = Awaited<ReturnType<typeof api.latestSession>>;

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

/** 会话恢复进度：restored=已带回快照；empty=首次使用（404）；error=恢复失败。 */
export type ChatRestoreState = "pending" | "restored" | "empty" | "error";

interface ChatSessionValue {
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: { reasoning: string; content: string } | null;
  sending: boolean;
  waitSeconds: number;
  chatError: string;
  copiedId: number | null;
  skillNames: string[];
  instruction: string;
  media: PendingMedia | null;
  restoreState: ChatRestoreState;
  restoredPromptName: string | null;
  /** 发送一轮打标：promptName / activeModel 由组件在发送时刻传入（配置仍归页面管）。 */
  send(input: { promptName: string | null; activeModel: string }): void;
  stopGeneration(): void;
  newSession(): void;
  /** 清对话列（切提示词 = 换 system 底座）：保留输入与附件，不重开输入状态。 */
  clearConversation(): void;
  /**
   * 进入一个会话桶（三期 v3）：切策略 / 新建策略时调用——拉该桶最近会话，
   * 有则接上、无则空白；输入草稿随桶切换。桶 id 是策略 id 或 NEW_STRATEGY_ID。
   */
  attachBucket(bucketId: string): void;
  /**
   * 把当前活跃会话改挂到新策略 id（保存新策略时用）：本地桶状态与后端归属
   * 一起更新；没有活跃会话则只换桶。
   */
  assignActiveSession(strategyId: string): void;
  toggleSkill(name: string): void;
  /** 整组替换 Skill 组合（策略应用时用；与 toggleSkill 同为会话域状态）。 */
  applySkillNames(names: string[]): void;
  setInstruction(value: string): void;
  pickMedia(file: File | undefined): void;
  setMediaFps(fps: number): void;
  setMediaMaxFrames(maxFrames: number): void;
  clearMedia(): void;
  copyCaption(message: ChatMessage): void;
  /** 对话列错误条的直写口（端点激活等页面侧流程也往这里报）。 */
  setChatError(value: string): void;
}

const ChatSessionContext = createContext<ChatSessionValue | null>(null);

export function ChatSessionProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // 输入框草稿跨重启持久化（三期 v3 起按会话桶分键）：没发出去的话重启还在；
  // 发送 / 新会话 / 清空会把它归零，落盘值随之清掉。附件不持久化（体积与隐私
  // 不划算，显式不做）。桶切换时草稿跟着切（attachBucket 内迁移）。
  const [instruction, setInstructionState] = useState("");
  const [media, setMedia] = useState<PendingMedia | null>(null);
  const [sending, setSending] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [chatError, setChatErrorState] = useState("");
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [streaming, setStreaming] = useState<{
    reasoning: string;
    content: string;
  } | null>(null);
  const [restoreState, setRestoreState] = useState<ChatRestoreState>("pending");
  const [restoredPromptName, setRestoredPromptName] = useState<string | null>(null);

  const sendingRef = useRef(false);
  // 「停止生成」（N1④）：发送期间持有的 AbortController，停止钮触发即中止本轮。
  const abortRef = useRef<AbortController | null>(null);
  const mediaRequestRef = useRef(0);
  // 会话恢复前的用户动作计数：恢复落地时用户已经动过手（发过消息 / 动过组合 / 打过字），
  // 快照就是过时的，整体放弃应用（迟到的恢复不覆盖当前状态）。
  const userActedRef = useRef(0);
  // 当前会话桶（策略 id 或 NEW_STRATEGY_ID）：send 盖章、草稿分键都以它为准。
  // ref 与 state 并存——ref 供回调同步读（send / 草稿迁移），state 驱动 UI。
  const bucketRef = useRef<string>(NEW_STRATEGY_ID);
  // attachBucket 的请求序号：快速连续切桶时，旧桶的迟到响应不覆盖新桶状态。
  const attachSeqRef = useRef(0);
  // instruction 的同步镜像：桶切换迁移草稿时读「此刻输入框内容」，不进依赖数组。
  const instructionRef = useRef("");
  instructionRef.current = instruction;

  /** 把输入草稿写入当前桶的落盘键（切桶迁移 / 发送清空都走这里）。 */
  const persistInstruction = useCallback((value: string): void => {
    writeStoredJson(chatInstructionKey(bucketRef.current), value);
  }, []);

  const setInstructionForBucket = useCallback(
    (value: string): void => {
      setInstructionState(value);
      persistInstruction(value);
    },
    [persistInstruction],
  );

  // 会话快照的统一应用口：启动恢复与切桶接续共用。历史附件直连会话附件端点（B5）：
  // 缩略图不再依赖内存 dataURL，刷新不丢。
  const applySnapshot = useCallback((snapshot: SessionSnapshot): void => {
    setRestoredPromptName(snapshot.settings.prompt_name);
    setSessionId(snapshot.session_id);
    setSkillNames(snapshot.settings.skill_names);
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
    setRestoreState("restored");
  }, []);

  // 启动恢复（三期 v3）：按策略镜像进桶——
  //   · 镜像键存在且指向策略 → 拉该桶最近会话接上（镜像指向已删策略时桶为空，
  //     404 即空白，恢复链不猜）；
  //   · 镜像键存在且为 null（用户停在新建策略态）→ 进 __new__ 桶；
  //   · 镜像键不存在（首启 / 清了站点数据）→ 认领：全局最新会话的归属命中什么
  //     桶就进什么桶（无归属的存量会话进 __new__——签名近似已退役，不猜不认错）。
  // 认领结论（含 404 / 新建态）一律落回镜像键：本 Provider 是认领的唯一发起方，
  // 策略工具栏只等镜像键出现再按 id 恢复选中，不自己发请求——boot 的请求面因此
  // 确定（E2E requests 基线不再竞速）。
  useEffect(() => {
    let cancelled = false;
    const rawMirror = localStorage.getItem(WORKBENCH_STRATEGY_KEY);
    const mirror =
      rawMirror === null
        ? undefined
        : readStoredJson(WORKBENCH_STRATEGY_KEY, isStrategySelection);
    const settleMirror = (value: unknown): void => {
      if (!cancelled) writeStoredJson(WORKBENCH_STRATEGY_KEY, value);
    };
    void (async () => {
      try {
        if (mirror === undefined) {
          // 认领：全局最新会话的归属就是「上次工作的地方」。
          const latest = await api.latestSession();
          if (cancelled || userActedRef.current > 0) return;
          const owner = latest.strategy_id ?? NEW_STRATEGY_ID;
          bucketRef.current = owner;
          setInstructionState(readStoredString(chatInstructionKey(owner)) ?? "");
          settleMirror(
            owner === NEW_STRATEGY_ID
              ? null
              : {
                  id: owner,
                  name: "",
                  description: "",
                  endpoint: "",
                  prompt: "",
                  skills: [],
                },
          );
          applySnapshot(latest);
          return;
        }
        const owner = mirror?.id ?? NEW_STRATEGY_ID;
        const snapshot = await api.latestSession(owner);
        if (cancelled || userActedRef.current > 0) return;
        bucketRef.current = owner;
        setInstructionState(readStoredString(chatInstructionKey(owner)) ?? "");
        applySnapshot(snapshot);
      } catch (err) {
        if (cancelled || userActedRef.current > 0) return;
        const noSessionYet = err instanceof ApiError && err.status === 404;
        if (noSessionYet) {
          // 桶里还没有会话（或还没有任何会话）：空白起步，落镜像免得工具栏再等。
          if (mirror === undefined) settleMirror(null);
          setRestoreState("empty");
        } else {
          setChatErrorState(reportError(err) ?? "");
          setRestoreState("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applySnapshot]);

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

  const send = useCallback(
    ({
      promptName,
      activeModel,
    }: {
      promptName: string | null;
      activeModel: string;
    }): void => {
      if (sendingRef.current) return;
      if (instruction.trim() === "" && media === null) return;
      userActedRef.current += 1;
      mediaRequestRef.current += 1;
      sendingRef.current = true;
      setSending(true);
      setChatErrorState("");
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
                  ? {
                      attachmentDurationSec: sentMedia.durationSec,
                      // 封面随消息走（C0）：pickMedia 异步抽帧，若发送时还没抽完，
                      // 这里就没有 poster——当轮落到图标，属可接受的竞态边界。
                      ...(sentMedia.posterUrl !== undefined
                        ? { attachmentPosterUrl: sentMedia.posterUrl }
                        : {}),
                    }
                  : {}),
              }
            : {}),
        },
      ]);
      setInstructionForBucket("");
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
      void (async () => {
        try {
          await api.labelStream(
            {
              session_id: sessionId,
              prompt_name: promptName,
              skill_names: skillNames,
              instruction: sentInstruction,
              image_base64: sentMedia?.kind === "image" ? sentMedia.dataUrl : null,
              image_name: sentMedia?.kind === "image" ? sentMedia.name : "image.png",
              video_base64: sentMedia?.kind === "video" ? sentMedia.dataUrl : null,
              video_name: sentMedia?.kind === "video" ? sentMedia.name : "video.mp4",
              video_fps: sentMedia?.kind === "video" ? sentMedia.fps : 2,
              video_max_frames: sentMedia?.kind === "video" ? sentMedia.maxFrames : 16,
              // 会话归属章（三期 v3）：新建会话时后端按它进桶；续接时后端忽略。
              strategy_id: bucketRef.current,
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
                setChatErrorState(message);
              },
            },
            controller.signal,
          );
        } catch (err) {
          keepPartial();
          // 用户主动停止不当失败展示（停止本身就是那轮的结局）。
          if (!controller.signal.aborted) {
            setChatErrorState(reportError(err) ?? "");
          }
        } finally {
          abortRef.current = null;
          sendingRef.current = false;
          setSending(false);
          setStreaming(null);
        }
      })();
    },
    [instruction, media, sessionId, skillNames, setInstructionForBucket],
  );

  /** 停止生成（N1④）：中止当前请求；已收到的部分按半截消息留痕。 */
  const stopGeneration = useCallback((): void => {
    abortRef.current?.abort();
  }, []);

  /** 清空当前会话（Q4 改名）：只清内存与界面——旧会话仍完整保存在磁盘上。 */
  const newSession = useCallback((): void => {
    userActedRef.current += 1;
    mediaRequestRef.current += 1;
    setSessionId(null);
    setMessages([]);
    setChatErrorState("");
    // 旧输入 / 旧附件 / 旧 Skill 不带进新会话（N1 同源②）。
    setInstructionForBucket("");
    setMedia(null);
    setSkillNames([]);
  }, [setInstructionForBucket]);

  const clearConversation = useCallback((): void => {
    userActedRef.current += 1;
    setSessionId(null);
    setMessages([]);
    setChatErrorState("");
  }, []);

  /**
   * 进入一个会话桶（三期 v3，替代签名对账）：切策略 / 新建策略统一走这里。
   * 草稿随桶迁移（当前值落旧桶键、读入新桶键）；该桶最近会话有则接上、无则
   * 空白。快速连续切桶时用序号丢弃迟到响应。
   */
  const attachBucket = useCallback(
    (nextBucket: string): void => {
      userActedRef.current += 1;
      const request = ++attachSeqRef.current;
      if (bucketRef.current !== nextBucket) {
        persistInstruction(instructionRef.current);
        bucketRef.current = nextBucket;
        setInstructionState(readStoredString(chatInstructionKey(nextBucket)) ?? "");
      }
      void (async () => {
        try {
          const snapshot = await api.latestSession(nextBucket);
          if (request !== attachSeqRef.current) return;
          applySnapshot(snapshot);
        } catch (err) {
          if (request !== attachSeqRef.current) return;
          clearConversation();
          if (!(err instanceof ApiError && err.status === 404)) {
            setChatErrorState(reportError(err) ?? "");
          }
        }
      })();
    },
    [applySnapshot, clearConversation, persistInstruction],
  );

  /**
   * 把当前活跃会话改挂到新策略 id（保存新策略后调用）：后端归属与本地桶状态
   * 一起更新；没有活跃会话（草稿没发过言）只换桶。改挂失败（会话已不存在等）
   * 不阻断保存流程，桶状态照常切换。
   */
  const assignActiveSession = useCallback(
    (strategyId: string): void => {
      const current = sessionId;
      bucketRef.current = strategyId;
      if (current === null) return;
      void api
        .assignSessionStrategy(current, strategyId)
        .then(() => undefined)
        .catch((err: unknown) => {
          setChatErrorState(reportError(err) ?? "");
        });
    },
    [sessionId],
  );

  const toggleSkill = useCallback((name: string): void => {
    if (sendingRef.current) return;
    userActedRef.current += 1;
    setSkillNames((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    );
  }, []);

  const applySkillNames = useCallback((names: string[]): void => {
    userActedRef.current += 1;
    setSkillNames(names);
  }, []);

  const setInstruction = useCallback(
    (value: string): void => {
      userActedRef.current += 1;
      setInstructionForBucket(value);
    },
    [setInstructionForBucket],
  );

  const pickMedia = useCallback((file: File | undefined): void => {
    if (file === undefined) {
      return;
    }
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
        setChatErrorState("附件读取失败，请重新选择。");
    };
    reader.readAsDataURL(file);
  }, []);

  // 抽帧参数改动走函数式更新：值已在 InputArea 的事件处理里同步取出，这里只合成新 media。
  const setMediaFps = useCallback((fps: number): void => {
    setMedia((current) => (current === null ? current : { ...current, fps }));
  }, []);
  const setMediaMaxFrames = useCallback((maxFrames: number): void => {
    setMedia((current) => (current === null ? current : { ...current, maxFrames }));
  }, []);

  const clearMedia = useCallback((): void => {
    mediaRequestRef.current += 1;
    setMedia(null);
  }, []);

  const copyCaption = useCallback((message: ChatMessage): void => {
    void navigator.clipboard.writeText(message.text).then(() => {
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId(null), 1500);
    });
  }, []);

  const setChatError = useCallback((value: string): void => {
    setChatErrorState(value);
  }, []);

  return (
    <ChatSessionContext.Provider
      value={{
        sessionId,
        messages,
        streaming,
        sending,
        waitSeconds,
        chatError,
        copiedId,
        skillNames,
        instruction,
        media,
        restoreState,
        restoredPromptName,
        send,
        stopGeneration,
        newSession,
        clearConversation,
        attachBucket,
        assignActiveSession,
        toggleSkill,
        applySkillNames,
        setInstruction,
        pickMedia,
        setMediaFps,
        setMediaMaxFrames,
        clearMedia,
        copyCaption,
        setChatError,
      }}
    >
      {children}
    </ChatSessionContext.Provider>
  );
}

/** 会话域的消费者钩子；必须在 ChatSessionProvider 内使用。 */
export function useChatSession(): ChatSessionValue {
  const value = useContext(ChatSessionContext);
  if (value === null) {
    throw new Error("useChatSession 必须在 ChatSessionProvider 内使用");
  }
  return value;
}
