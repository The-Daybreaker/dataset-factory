/**
 * UI 状态持久化的键清单、镜像形状与读写助手（三期「页面状态保持」）。
 *
 * 键名是跨模块契约：写入方（各页面的 usePersistedState）与读取方（会话域的启动
 * 分流、打标页的一次性恢复）都从这里拿常量，不散落字符串字面量——同一件事实
 * 只放一处。值的序列化仍走 usePersistedState（JSON + 类型守卫 + 读写兜底）；
 * 这里只放键名、形状守卫和不挂 state 的「读一次 / 写一次」助手。
 */

/** 外壳：上次停留的页面（重开后回到它）。 */
export const SHELL_PAGE_KEY = "dsf-shell-page";
export type ShellPage = "prompts" | "settings" | "labeling";
export function isShellPage(value: unknown): value is ShellPage {
  return value === "prompts" || value === "settings" || value === "labeling";
}

/** 外壳：侧栏折叠。 */
export const SHELL_SIDEBAR_COLLAPSED_KEY = "dsf-shell-sidebar-collapsed";

/** 外壳：设置页当前节（endpoints / skills / service）。 */
export const SHELL_SETTINGS_SECTION_KEY = "dsf-settings-section";

/**
 * 策略页：编辑器状态镜像——选中提示词 + 名称 / 描述 / 正文草稿 + 干净基线 +
 * 是否新建草稿。恢复即视为「最近一次用户意图」，优先级高于后端会话快照
 * （规则见 PromptWorkbench 启动分流与 ADR 2026-09-22 条）。
 */
export const WORKBENCH_EDITOR_KEY = "dsf-workbench-editor";
export interface WorkbenchEditorMirror {
  selectedName: string;
  draftName: string;
  draftDescription: string;
  draftBody: string;
  savedPrompt: { name: string; description: string; body: string };
  isNewDraft: boolean;
}
export function isWorkbenchEditorMirror(
  value: unknown,
): value is WorkbenchEditorMirror {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const saved = v.savedPrompt;
  return (
    typeof v.selectedName === "string" &&
    typeof v.draftName === "string" &&
    typeof v.draftDescription === "string" &&
    typeof v.draftBody === "string" &&
    typeof v.isNewDraft === "boolean" &&
    typeof saved === "object" &&
    saved !== null &&
    typeof (saved as Record<string, unknown>).name === "string" &&
    typeof (saved as Record<string, unknown>).description === "string" &&
    typeof (saved as Record<string, unknown>).body === "string"
  );
}

/**
 * 未挂策略的固定桶 id（三期 v3）：新建策略态 / 无策略直接改提示词时，会话归属
 * 都记到这个伪 id 下——它不与任何真实策略 id（后端随机生成的 `s` 前缀短 id）相等，
 * 结构上杜绝「新建策略的对话错绑到已有策略」。后端 LabelRequest.strategy_id 直接
 * 透传该值，语义两侧一致。
 */
export const NEW_STRATEGY_ID = "__new__";

/** 会话域：输入框草稿（未发送的指令文本），按会话桶分键（切策略互不串）。 */
export const CHAT_INSTRUCTION_KEY_PREFIX = "dsf-chat-instruction";
export function chatInstructionKey(bucketId: string): string {
  return `${CHAT_INSTRUCTION_KEY_PREFIX}:${bucketId}`;
}

/**
 * 策略页：当前选中的策略（策略工具栏的「我在哪个策略里工作」）。与编辑器镜像
 * 成对落盘——策略与提示词是两个维度：两条策略可共用同一篇提示词，只凭提示词
 * 名分不出是谁。会话恢复的签名对账同时用两者（见 chat-session.tsx）。
 */
export const WORKBENCH_STRATEGY_KEY = "dsf-workbench-strategy";
export interface StrategySelection {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  prompt: string;
  skills: string[];
}
export function isStrategySelection(value: unknown): value is StrategySelection {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.name === "string" &&
    typeof v.description === "string" &&
    typeof v.endpoint === "string" &&
    typeof v.prompt === "string" &&
    Array.isArray(v.skills) &&
    v.skills.every((item) => typeof item === "string")
  );
}

/** 打标页：左列筛选词 / 选中素材（素材 id 批次内有效，恢复时对装载结果校验）。 */
export const LABELING_QUERY_KEY = "dsf-labeling-query";
export const LABELING_SELECTED_ITEM_KEY = "dsf-labeling-selected-item";

/** Skill 组合等值：顺序不敏感（组合是集合语义，落盘顺序随写入口可能漂移）。 */
export function sameSkills(a: string[], b: string[]): boolean {
  return (
    a.length === b.length &&
    JSON.stringify([...a].sort()) === JSON.stringify([...b].sort())
  );
}

function isStringValue(value: unknown): value is string {
  return typeof value === "string";
}

/** 启动时读一次已存的 JSON 值（不存在 / 坏 JSON / 类型不符返回 null）。 */
export function readStoredJson<T>(
  key: string,
  validate: (value: unknown) => value is T,
): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return validate(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** 读一次已存字符串值（不存在 / 类型不符返回 null）。 */
export function readStoredString(key: string): string | null {
  return readStoredJson(key, isStringValue);
}

/** 写一个 JSON 值（隐私模式 / 配额满时静默放弃——记忆失败可接受，与 usePersistedState 同款兜底）。 */
export function writeStoredJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 存不进去就算了：下次进来退回初始态，不算故障。
  }
}
