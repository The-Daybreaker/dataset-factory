/**
 * 后端 API 客户端：统一的 fetch 封装 + 契约生成的类型。
 *
 * 类型来源：`api-types.gen.ts` 由 `npm run gen:api` 从 backend/openapi.json 生成——
 * 后端改了字段、重新导出快照，这里的类型跟着变，字段对不上在 typecheck 当场报错，
 * 不再靠人眼对齐（这就是「API 契约」的前端侧）。
 */

import type { components } from "./api-types.gen";

/** 后端契约里的 schema 类型（别名导出：调用方不必知道生成结构）。 */
export type LabelRequest = components["schemas"]["LabelRequest"];
export type HistoryMessageView = components["schemas"]["HistoryMessageView"];
export type SessionSnapshotResponse = components["schemas"]["SessionSnapshotResponse"];
export type PromptInfo = components["schemas"]["PromptInfo"];
export type PromptFull = components["schemas"]["PromptFull"];
export type PromptSaveRequest = components["schemas"]["PromptSaveRequest"];
export type PromptRenameRequest = components["schemas"]["PromptRenameRequest"];
export type SkillInfo = components["schemas"]["SkillInfo"];
export type SkillImportResponse = components["schemas"]["SkillImportResponse"];
export type SkillRenameRequest = components["schemas"]["SkillRenameRequest"];
export type ConfigResponse = components["schemas"]["ConfigResponse"];
export type ConfigUpdateRequest = components["schemas"]["ConfigUpdateRequest"];
export type EndpointConfigSummary = components["schemas"]["EndpointConfigSummary"];
export type EndpointRequestParams = components["schemas"]["EndpointRequestParams"];
export type EndpointCreateRequest = components["schemas"]["EndpointCreateRequest"];
export type EndpointUpdateRequest = components["schemas"]["EndpointUpdateRequest"];
export type EndpointTestRequest = components["schemas"]["EndpointTestRequest"];
export type EndpointTestResult = components["schemas"]["EndpointTestResult"];
export type SkillFilesResponse = components["schemas"]["SkillFilesResponse"];
export type SkillFileInfo = components["schemas"]["SkillFileInfo"];
export type SkillFileContent = components["schemas"]["SkillFileContent"];
export type ServiceStatus = components["schemas"]["ServiceStatus"];
export type ServiceLogs = components["schemas"]["ServiceLogs"];
export interface TaskView {
  id: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  result: unknown;
  error: string | null;
}

function parseTask(value: unknown): TaskView {
  if (
    !value ||
    typeof value !== "object" ||
    !("id" in value) ||
    typeof value.id !== "string" ||
    !("status" in value) ||
    (value.status !== "running" &&
      value.status !== "succeeded" &&
      value.status !== "failed" &&
      value.status !== "cancelled") ||
    !("progress" in value) ||
    typeof value.progress !== "number" ||
    !Number.isFinite(value.progress) ||
    !("result" in value) ||
    !("error" in value) ||
    (value.error !== null && typeof value.error !== "string")
  ) {
    throw new Error("任务响应格式异常，请刷新后重试");
  }
  return {
    id: value.id,
    status: value.status,
    progress: value.progress,
    result: value.result,
    error: value.error,
  };
}
/** 错误体的契约形状（{"detail": string}）——错误路径也在契约里，不再有盲区。 */

/** 把任意抛出的东西变成可展示的一句话（界面上不该出现 "[object Object]"）。 */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 管理操作的请求超时：都该秒回（打标走流式 labelStream，自带增量反馈、不设硬超时）。 */
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
  responseType: "json" | "text" = "json",
): Promise<T> {
  // 超时用 AbortController 实现：到点放弃等待，而不是无限期挂着。
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // FormData（multipart 上传）由浏览器自动带边界头，不能手动设 Content-Type。
  const isFormData = body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers:
        body === undefined || isFormData ? {} : { "Content-Type": "application/json" },
      body:
        body === undefined
          ? undefined
          : isFormData
            ? (body as FormData)
            : JSON.stringify(body),
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
  if (response.ok && responseType === "text") {
    return (await response.text()) as T;
  }
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
  listDirectory: (
    path: string,
    showFiles = false,
    showHidden = false,
    suffixes: string[] = [],
  ) => {
    const query = new URLSearchParams({
      show_files: String(showFiles),
      show_hidden: String(showHidden),
    });
    if (path) query.set("path", path);
    for (const suffix of suffixes) query.append("suffixes", suffix);
    return request<components["schemas"]["DirectoryListing"]>(
      "GET",
      `/api/filesystem?${query}`,
    );
  },

  renameDirectory: (path: string, newName: string) =>
    request<components["schemas"]["WorkdirRelocateAccepted"]>(
      "POST",
      "/api/filesystem/rename",
      { path, new_name: newName },
    ),

  createDirectory: (parent: string, name: string) =>
    request<components["schemas"]["DirectoryPath"]>(
      "POST",
      "/api/filesystem/directories",
      { parent, name },
    ),

  filesystemCapabilities: () =>
    request<components["schemas"]["FilesystemCapabilities"]>(
      "GET",
      "/api/filesystem/capabilities",
    ),

  openDirectory: (path: string) =>
    request<void>("POST", "/api/filesystem/open", { path }),

  exportPlan: (wid: string, batch: string, sequential: boolean) =>
    request<components["schemas"]["ExportPlanView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/export/plan?batch=${encodeURIComponent(batch)}&sequential=${sequential}`,
    ),

  startExport: (wid: string, batch: string, sequential: boolean) =>
    request<components["schemas"]["ExportAccepted"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/export`,
      { batch, mode: "current", sequential },
    ),

  setExclusions: (wid: string, batch: string, items: string[], excluded: boolean) =>
    request<components["schemas"]["ExclusionsView"]>(
      excluded ? "POST" : "DELETE",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/exclusions`,
      { items },
    ),

  latestRun: (wid: string, batch: string) =>
    request<components["schemas"]["RunHistoryView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs/latest`,
    ),

  readRunText: (
    wid: string,
    batch: string,
    runId: string,
    file: "run.log" | "items.jsonl",
  ) =>
    request<components["schemas"]["RunTextView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs/${encodeURIComponent(runId)}/text?file=${encodeURIComponent(file)}`,
    ),

  rebuildImportRecords: (wid: string) =>
    request<components["schemas"]["ImportAccepted"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/imports/rebuild`,
    ),

  scanIntegrity: (wid: string, batch: string) =>
    request<components["schemas"]["IntegrityReport"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/integrity/scan?batch=${encodeURIComponent(batch)}`,
    ),

  listStrategies: () =>
    request<components["schemas"]["StrategyView"][]>("GET", "/api/strategies"),

  getStrategy: (id: string) =>
    request<components["schemas"]["StrategyView"]>(
      "GET",
      `/api/strategies/${encodeURIComponent(id)}`,
    ),

  createStrategy: (body: components["schemas"]["StrategySaveRequest"]) =>
    request<components["schemas"]["StrategyView"]>("POST", "/api/strategies", body),

  updateStrategy: (id: string, body: components["schemas"]["StrategySaveRequest"]) =>
    request<components["schemas"]["StrategyView"]>(
      "PUT",
      `/api/strategies/${encodeURIComponent(id)}`,
      body,
    ),

  copyStrategy: (id: string) =>
    request<components["schemas"]["StrategyView"]>(
      "POST",
      `/api/strategies/${encodeURIComponent(id)}/copy`,
    ),

  rebindStrategy: (id: string, body: components["schemas"]["StrategyRebindRequest"]) =>
    request<components["schemas"]["StrategyView"]>(
      "POST",
      `/api/strategies/${encodeURIComponent(id)}/rebind`,
      body,
    ),

  deleteStrategy: (id: string) =>
    request<void>("DELETE", `/api/strategies/${encodeURIComponent(id)}`),

  createWorkdir: (body: components["schemas"]["WorkdirCreateRequest"]) =>
    request<components["schemas"]["WorkdirCreateAccepted"]>(
      "POST",
      "/api/workdirs",
      body,
    ),

  createBatch: (wid: string, body: components["schemas"]["BatchCreateRequest"]) =>
    request<components["schemas"]["BatchView"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/batches`,
      body,
    ),

  importMaterials: (wid: string, body: components["schemas"]["WorkdirImportRequest"]) =>
    request<components["schemas"]["ImportAccepted"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/imports`,
      body,
    ),

  reimportMaterials: (wid: string, names: string[], forceNames?: string[]) =>
    request<components["schemas"]["ImportAccepted"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/imports/reimport`,
      { names, ...(forceNames ? { force_names: forceNames } : {}) },
    ),

  removeUnimported: (wid: string, names: string[]) =>
    request<components["schemas"]["CleanupResult"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/unimported/remove`,
      { names },
    ),

  getTask: async (id: string) =>
    parseTask(await request<unknown>("GET", `/api/tasks/${encodeURIComponent(id)}`)),

  cancelTask: async (id: string) =>
    parseTask(
      await request<unknown>("POST", `/api/tasks/${encodeURIComponent(id)}/cancel`),
    ),

  readCaption: (wid: string, batch: string, item: string) =>
    request<string>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/items/${encodeURIComponent(item)}/txt`,
      undefined,
      DEFAULT_TIMEOUT_MS,
      "text",
    ),

  addRetryItems: (wid: string, batch: string, items: string[]) =>
    request<components["schemas"]["RetryListView"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/retry-list`,
      { items },
    ),

  removeRetryItem: (wid: string, batch: string, item: string) =>
    request<components["schemas"]["RetryListView"]>(
      "DELETE",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/retry-list/${encodeURIComponent(item)}`,
    ),

  clearRetryItems: (wid: string, batch: string) =>
    request<components["schemas"]["RetryListView"]>(
      "DELETE",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/retry-list`,
    ),

  startRun: (wid: string, batch: string, mode: "full" | "retry", items?: string[]) =>
    request<components["schemas"]["RunAccepted"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs`,
      { mode, ...(items ? { items } : {}) },
    ),

  currentRun: (wid: string, batch: string) =>
    request<components["schemas"]["RunStatusView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs/current`,
    ),

  stopRun: (wid: string, batch: string) =>
    request<void>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/runs/stop`,
      {},
    ),

  listWorkdirs: () =>
    request<components["schemas"]["WorkdirInfo"][]>("GET", "/api/workdirs"),

  getWorkdir: (wid: string) =>
    request<components["schemas"]["WorkdirInfo"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}`,
    ),

  getWorkdirStats: (wid: string) =>
    request<components["schemas"]["WorkdirStatsView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/stats`,
    ),

  previewProductCleanup: (wid: string) =>
    request<components["schemas"]["CleanupPreview"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/cleanup-preview`,
    ),

  previewWorkdirDeletion: (wid: string) =>
    request<components["schemas"]["DeletionPreview"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/delete-preview`,
    ),

  relocateWorkdir: (wid: string, path: string) =>
    request<components["schemas"]["WorkdirRelocateAccepted"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/relocate`,
      { path },
    ),

  relocationStatus: (wid: string) =>
    request<components["schemas"]["WorkdirRelocationStatus"][]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/relocate/status`,
    ),

  retryRelocationCleanup: (wid: string, oldPath: string) =>
    request<components["schemas"]["WorkdirCleanupRetryResult"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/relocate/cleanup`,
      { old_path: oldPath },
    ),

  deleteWorkdir: (wid: string, confirmedPath: string) =>
    request<components["schemas"]["DeletionResult"]>(
      "DELETE",
      `/api/workdirs/${encodeURIComponent(wid)}`,
      { confirmed_path: confirmedPath },
    ),

  previewRunCleanup: (wid: string) =>
    request<components["schemas"]["RunCleanupEntry"][]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/cleanup-runs-preview`,
    ),

  cleanupWorkdir: (wid: string, kind: "products" | "runs", names: string[]) =>
    request<components["schemas"]["CleanupResult"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/${kind === "runs" ? "cleanup-runs" : "cleanup"}`,
      { names },
    ),

  updateBatch: (
    wid: string,
    batch: string,
    body: components["schemas"]["BatchUpdateRequest"],
  ) =>
    request<components["schemas"]["BatchView"]>(
      "PATCH",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}`,
      body,
    ),

  setBatchActive: (wid: string, batch: string, active: boolean) =>
    request<components["schemas"]["BatchView"]>(
      "POST",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/${active ? "unhide" : "hide"}`,
      {},
    ),

  deleteBatch: (wid: string, batch: string) =>
    request<void>(
      "DELETE",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}`,
    ),

  listBatches: (wid: string) =>
    request<components["schemas"]["BatchView"][]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches`,
    ),

  getBatchSnapshot: (wid: string, batch: string) =>
    request<components["schemas"]["BatchSnapshotView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/snapshot`,
    ),

  listItems: (wid: string, batch: string) =>
    request<components["schemas"]["ItemListView"]>(
      "GET",
      `/api/workdirs/${encodeURIComponent(wid)}/batches/${encodeURIComponent(batch)}/items`,
    ),

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

  /** 重命名提示词（改文件名；历史备份随迁）。 */
  renamePrompt: (name: string, payload: PromptRenameRequest) =>
    request<void>("POST", `/api/prompts/${encodeURIComponent(name)}/rename`, payload),

  /** 删除提示词。 */
  deletePrompt: (name: string) =>
    request<void>("DELETE", `/api/prompts/${encodeURIComponent(name)}`),

  /** 列出 skill（含启用状态）。 */
  listSkills: () => request<SkillInfo[]>("GET", "/api/skills"),

  /** 从本机路径导入 skill（服务端可访问的路径；目录整包或单个 SKILL.md 文件均可）。 */
  importSkill: (path: string) =>
    request<SkillImportResponse>("POST", "/api/skills/import", { path }),

  /** 上传导入 skill 包（文件夹选择器 / 拖拽选中的文件集；传内容不传路径）。 */
  importSkillFiles: (files: File[]) => {
    const form = new FormData();
    for (const file of files) {
      const relative = file.webkitRelativePath || file.name;
      form.append("files", file, relative);
    }
    return request<SkillImportResponse>("POST", "/api/skills/import-upload", form);
  },

  /** 上传导入单个 SKILL.md 文件（无文件夹结构的单文件 skill；统一按 SKILL.md 交付）。 */
  importSkillFile: (file: File) => {
    const form = new FormData();
    form.append("files", file, "SKILL.md");
    return request<SkillImportResponse>("POST", "/api/skills/import-upload", form);
  },

  /** 重命名 skill（目录改名，SKILL.md 的 name 同步改写；重名报 409）。 */
  renameSkill: (name: string, payload: SkillRenameRequest) =>
    request<void>("POST", `/api/skills/${encodeURIComponent(name)}/rename`, payload),

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

  /** 列出端点多配置概要（密钥只报有无）。 */
  listEndpoints: () => request<EndpointConfigSummary[]>("GET", "/api/endpoints"),

  /** 新增一套端点配置；当前没有生效配置时后端自动设为当前使用。 */
  createEndpoint: (payload: EndpointCreateRequest) =>
    request<EndpointConfigSummary>("POST", "/api/endpoints", payload),

  /** 更新一套端点配置（api_key 缺省沿用已存密钥）。 */
  updateEndpoint: (name: string, payload: EndpointUpdateRequest) =>
    request<EndpointConfigSummary>(
      "PUT",
      `/api/endpoints/${encodeURIComponent(name)}`,
      payload,
    ),

  /** 删除一套端点配置（当前使用中的会被后端拒绝）。 */
  deleteEndpoint: (name: string) =>
    request<void>("DELETE", `/api/endpoints/${encodeURIComponent(name)}`),

  /** 把一套配置设为当前使用；对新请求立即生效。 */
  activateEndpoint: (name: string) =>
    request<void>("POST", `/api/endpoints/${encodeURIComponent(name)}/activate`),

  /** 测试端点连通性（用表单当前值发极小真实请求；密钥缺省回落该配置已存密钥）。 */
  testEndpoint: (payload: EndpointTestRequest) =>
    request<EndpointTestResult>("POST", "/api/endpoints/test", payload),

  /** 列出技能包内文件（角色标注：SKILL.md / references 可预览，assets / scripts 不可）。 */
  listSkillFiles: (name: string) =>
    request<SkillFilesResponse>("GET", `/api/skills/${encodeURIComponent(name)}/files`),

  /** 读技能包内一个可预览文件的文本内容（UTF-8）。 */
  readSkillFile: (name: string, path: string) =>
    request<SkillFileContent>(
      "GET",
      `/api/skills/${encodeURIComponent(name)}/files/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    ),

  /** 保存技能文本，原始内容用于检测并发修改。 */
  saveSkillFile: (
    name: string,
    path: string,
    payload: components["schemas"]["SkillFileSaveRequest"],
  ) =>
    request<SkillFileContent>(
      "PUT",
      `/api/skills/${encodeURIComponent(name)}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
      payload,
    ),

  /** 服务运行状态（serve 启动时注入；非 serve 场景后端返回 409）。 */
  getService: () => request<ServiceStatus>("GET", "/api/service"),

  /** 运行日志尾部（最近 lines 行，1–1000；文件未创建时 exists=false）。 */
  getServiceLogs: (lines = 200) =>
    request<ServiceLogs>("GET", `/api/service/logs?lines=${lines}`),

  /** 请求停止服务（服务把手头请求做完再退出；成功即 202）。 */
  shutdownService: () => request<void>("POST", "/api/service/shutdown", {}),

  /**
   * 流式打标（SSE）：逐段回调思考 / 正文增量，done 回调带终稿与会话 id。
   *
   * POST + fetch 流式读取（EventSource 不支持 POST）；HTTP 层错误（预备段 4xx/5xx）
   * 直接抛 ApiError，流中的模型错误走 onError 回调（SSE 已开始、状态码改不了）。
   */
  labelStream: async (
    payload: LabelRequest,
    handlers: {
      onStart: (sessionId: string) => void;
      onDelta: (kind: "reasoning" | "content", text: string) => void;
      onDone: (sessionId: string, caption: string) => void;
      onError: (message: string) => void;
    },
  ): Promise<void> => {
    let response: Response;
    try {
      response = await fetch("/api/label/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch {
      throw new ApiError(
        "network",
        "无法连接后端服务——请确认 dsf serve 已启动、端口没有填错",
        null,
        null,
      );
    }
    if (!response.ok || response.body === null) {
      const data: unknown = await response.json().catch(() => null);
      throw new ApiError(
        "http",
        extractDetail(data, response.status),
        response.status,
        response.headers.get("X-Request-ID"),
      );
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      // 读流中途断掉 = 服务在生成途中退了（外部脚本杀进程最常见）。归入连接类失败，让界面
      // 按「连不上后端」统一提示，而不是把 `Failed to fetch` 这种原话丢给用户。
      const chunk = await reader.read().catch((): never => {
        throw new ApiError(
          "network",
          "与后端的连接中断，本轮没有完成——请确认服务在运行后重发",
          null,
          null,
        );
      });
      const { done, value } = chunk;
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const lines = frame.split("\n");
        const eventLine = lines.find((line) => line.startsWith("event: "));
        const dataLine = lines.find((line) => line.startsWith("data: "));
        if (eventLine === undefined || dataLine === undefined) {
          continue;
        }
        const event = eventLine.slice(7);
        // SSE data 字段理论上恒在；缺字段时给空串兜底（noUncheckedIndexedAccess 下不裸索引）。
        let data: Record<string, string | undefined>;
        try {
          data = JSON.parse(dataLine.slice(6)) as Record<string, string | undefined>;
        } catch {
          // 帧解析失败是「服务端发了不认识的东西」，与网络断开同类：给一句能读懂的
          // 话再中止本次读取，别把 SyntaxError 的原始报文丢给用户看。
          throw new ApiError("http", "收到的响应帧无法解析——请重发本轮。", null, null);
        }
        const sessionId = data.session_id ?? "";
        if (event === "start") {
          handlers.onStart(sessionId);
        } else if (event === "delta") {
          handlers.onDelta(
            data.kind === "reasoning" ? "reasoning" : "content",
            data.text ?? "",
          );
        } else if (event === "done") {
          handlers.onDone(sessionId, data.caption ?? "");
        } else if (event === "error") {
          handlers.onError(data.message ?? "生成中断：未知错误");
        }
      }
    }
  },
};
