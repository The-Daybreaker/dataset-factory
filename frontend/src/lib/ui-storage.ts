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

/** 会话域：输入框草稿（未发送的指令文本）。 */
export const CHAT_INSTRUCTION_KEY = "dsf-chat-instruction";

/** 打标页：左列筛选词 / 选中素材（素材 id 批次内有效，恢复时对装载结果校验）。 */
export const LABELING_QUERY_KEY = "dsf-labeling-query";
export const LABELING_SELECTED_ITEM_KEY = "dsf-labeling-selected-item";

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
