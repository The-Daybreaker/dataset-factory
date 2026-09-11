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

/** 统一请求：自动带 JSON 头、204 视为无内容、错误抛成 Error（消息可直接展示）。 */
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) {
    // 无内容响应：调用方声明的是 void，这里的断言只是让类型收口。
    return undefined as T;
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(extractDetail(data, response.status));
  }
  return data as T;
}

/** 后端接口的薄封装：一处集中管理路径与类型，界面代码只管调用。 */
export const api = {
  /** 发一轮打标；带 session_id 即续接该会话（迭代改写）。 */
  label: (payload: LabelRequest) =>
    request<LabelResponse>("POST", "/api/label", payload),

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
