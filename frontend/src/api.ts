/**
 * 后端 API 客户端：统一的 fetch 封装 + 与后端一致的请求 / 响应类型。
 *
 * 这里的类型目前是手写的（与后端 `api/schemas.py` 一一对应），只是过渡——T16 会换成
 * 从后端 OpenAPI spec 生成的类型，那时字段改名会在编译期就报出来，不再靠人眼对齐。
 */

export interface LabelRequest {
  session_id: string | null;
  prompt_name: string | null;
  skill_names: string[] | null;
  instruction: string;
  image_base64: string | null;
  image_name: string;
}

export interface LabelResponse {
  session_id: string;
  caption: string;
}

export interface SettingsView {
  prompt_name: string | null;
  skill_names: string[];
}

export interface HistoryMessageView {
  role: string;
  text: string;
  attachment: string | null;
}

export interface SessionSnapshotResponse {
  session_id: string;
  settings: SettingsView;
  messages: HistoryMessageView[];
}

export interface PromptInfo {
  name: string;
  description: string;
}

export interface PromptFull extends PromptInfo {
  body: string;
}

export interface PromptSaveRequest {
  description: string;
  body: string;
}

export interface SkillInfo {
  name: string;
  description: string;
  enabled: boolean;
}

export interface SkillImportResponse {
  name: string;
  description: string;
  enabled: boolean;
  total_bytes: number;
}

export interface ConfigResponse {
  base_url: string | null;
  model: string | null;
  api_key_configured: boolean;
  key_source: string | null;
}

export interface ConfigUpdateRequest {
  base_url: string;
  model: string;
  api_key?: string;
}

/** 把任意抛出的东西变成可展示的一句话（界面上不该出现 "[object Object]"）。 */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 请求超时的默认值：打标要等模型生成（可能几十秒），其余管理操作都该秒回。 */
const LABEL_TIMEOUT_MS = 240_000;
const DEFAULT_TIMEOUT_MS = 15_000;

/** 带上下文的 API 错误：界面上不止一句话，还能拿到「哪一层」与「请求 id」。 */
export class ApiError extends Error {
  /** HTTP 状态码；请求根本没到服务器（网络断 / 超时）时为 null。 */
  readonly status: number | null;
  /** 错误类别：timeout（前端主动放弃）/ network（连不上后端）/ http（后端返回了错误）。 */
  readonly kind: "timeout" | "network" | "http";
  /** 后端中间件写在 X-Request-ID 响应头里的请求 id；拿它去后端日志里串一整条链。 */
  readonly requestId: string | null;

  constructor(
    kind: ApiError["kind"],
    message: string,
    status: number | null,
    requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.requestId = requestId;
  }
}

/**
 * 从后端错误响应里取出可读消息。
 *
 * 后端有两套错误体：域异常 → `{"detail": "一句话"}`；FastAPI 校验失败 → `{"detail":
 * [{loc, msg}, ...]}`。这里把两种都摊平成文本，一次把校验错误报全。
 */
function extractDetail(data: unknown, status: number): string {
  if (data !== null && typeof data === "object" && "detail" in data) {
    const detail: unknown = (data as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (item !== null && typeof item === "object") {
            const entry = item as { loc?: unknown; msg?: unknown };
            const where = Array.isArray(entry.loc) ? entry.loc.join(".") : "";
            return where === "" ? String(entry.msg) : `${where}: ${String(entry.msg)}`;
          }
          return String(item);
        })
        .join("；");
    }
  }
  return `HTTP ${status}`;
}

/**
 * 统一请求：自动带 JSON 头、204 视为无内容、错误抛成带上下文的 ApiError。
 *
 * 三层失败各有自己的说法（这是「错误按层展示」的地基）：
 * - 连不上后端（fetch 直接抛 TypeError）→ network，提示查 dsf serve 是否在跑；
 * - 超时（AbortController 主动放弃）→ timeout，说明等了多久、后端可能仍在处理；
 * - 后端返回错误 → http，透传后端的一句话 + 请求 id。
 */
async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  // 超时用 AbortController 实现：到点放弃等待，而不是无限期挂着。
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    // fetch 对「主动 abort」和「网络错误」都抛同一个异常族，用 abort 标志区分。
    if (controller.signal.aborted) {
      const seconds = Math.round(timeoutMs / 1000);
      throw new ApiError(
        "timeout",
        `等待 ${seconds} 秒没有响应，已主动放弃（后端可能仍在处理，稍后可在会话列表里查看是否完成）`,
        null,
        null,
      );
    }
    throw new ApiError(
      "network",
      "无法连接后端服务——请确认 dsf serve 已启动、端口没有填错",
      null,
      null,
    );
  } finally {
    clearTimeout(timer);
  }
  // 请求 id 无论成败都从响应头取：错误时展示给用户，成功时也留在日志可查。
  const requestId = response.headers.get("X-Request-ID");
  if (response.status === 204) {
    // 无内容响应：调用方声明的是 void，这里的断言只是让类型收口。
    return undefined as T;
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const suffix =
      requestId === null ? "" : `（请求 id: ${requestId}，可拿它对后端日志）`;
    throw new ApiError(
      "http",
      `${extractDetail(data, response.status)}${suffix}`,
      response.status,
      requestId,
    );
  }
  return data as T;
}

/** 后端接口的薄封装：一处集中管理路径与类型，界面代码只管调用。 */
export const api = {
  /** 发一轮打标；带 session_id 即续接该会话（迭代改写）。超时给长（要等模型生成）。 */
  label: (payload: LabelRequest) =>
    request<LabelResponse>("POST", "/api/label", payload, LABEL_TIMEOUT_MS),

  /** 取最新会话快照（重启后恢复界面的入口）。 */
  latestSession: () => request<SessionSnapshotResponse>("GET", "/api/sessions/latest"),

  /** 列出提示词（名称 + 描述）。 */
  listPrompts: () => request<PromptInfo[]>("GET", "/api/prompts"),

  /** 取某个提示词的全文。 */
  getPrompt: (name: string) =>
    request<PromptFull>("GET", `/api/prompts/${encodeURIComponent(name)}`),

  /** 新建或覆盖提示词（名称即文件名）。 */
  savePrompt: (name: string, payload: PromptSaveRequest) =>
    request<void>("PUT", `/api/prompts/${encodeURIComponent(name)}`, payload),

  /** 删除提示词。 */
  deletePrompt: (name: string) =>
    request<void>("DELETE", `/api/prompts/${encodeURIComponent(name)}`),

  /** 列出 skill（含启用状态）。 */
  listSkills: () => request<SkillInfo[]>("GET", "/api/skills"),

  /** 从本机目录导入 skill 包（服务端可访问的路径）。 */
  importSkill: (path: string) =>
    request<SkillImportResponse>("POST", "/api/skills/import", { path }),

  /** 启用 / 停用 skill（停用不删除）。 */
  setSkillEnabled: (name: string, enabled: boolean) =>
    request<void>(
      "POST",
      `/api/skills/${encodeURIComponent(name)}/${enabled ? "enable" : "disable"}`,
    ),

  /** 从库中移除 skill（整目录）。 */
  deleteSkill: (name: string) =>
    request<void>("DELETE", `/api/skills/${encodeURIComponent(name)}`),

  /** 读当前端点配置（密钥只报来源、绝不回内容）。 */
  getConfig: () => request<ConfigResponse>("GET", "/api/config"),

  /** 写端点配置（api_key 缺省表示沿用已存密钥）。 */
  updateConfig: (payload: ConfigUpdateRequest) =>
    request<void>("PUT", "/api/config", payload),
};
