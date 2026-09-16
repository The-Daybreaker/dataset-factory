/**
 * 端点高级参数的纯逻辑（原型稿 ui-draft-05 同构：表单 ⇄ JSON 双向同步）：
 * 展示 JSON 组装、表单回填、表单→JSON 同步与保存载荷收集校验——只做计算，不碰 React 状态。
 */
import type { EndpointRequestParams } from "../../api";

/** 「模型通用参数」的表单键——JSON ⇄ 表单双向同步只发生在这些键上。 */
const ADV_STANDARD_KEYS: readonly string[] = ["temperature", "top_p", "max_tokens"];

/** JSON 同步状态（原型稿 advJsonState 同款三态）。 */
export interface AdvJsonState {
  kind: "ok" | "invalid" | "ignored";
  text: string;
}

/** 已设置的请求参数 → 展示用 JSON（标准键在前、extra_body 最后；全空 = 空串，让输入框显示参数说明）。 */
export function paramsToJson(params: EndpointRequestParams): string {
  const obj: Record<string, unknown> = {};
  if (params.temperature !== null && params.temperature !== undefined) {
    obj.temperature = params.temperature;
  }
  if (params.top_p !== null && params.top_p !== undefined) {
    obj.top_p = params.top_p;
  }
  if (params.max_tokens !== null && params.max_tokens !== undefined) {
    obj.max_tokens = params.max_tokens;
  }
  if (params.extra_body !== null && params.extra_body !== undefined) {
    obj.extra_body = params.extra_body;
  }
  if (Object.keys(obj).length === 0) {
    return "";
  }
  return JSON.stringify(obj, null, 2);
}

/** 数值输入的统一解读：空串 = 不设（null）；非有限数字 = "bad"（保存时拦截）。 */
function parseNumField(text: string): number | null | "bad" {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : "bad";
}

/** 表单当前值 + 既有 JSON（携带非标准键）→ 新 JSON（表单 → JSON 方向）。 */
export function formToJson(
  form: { temperature: string; top_p: string; max_tokens: string },
  currentJson: string,
): string {
  const obj: Record<string, unknown> = {};
  for (const key of ADV_STANDARD_KEYS) {
    const parsedNumber = parseNumField(form[key as keyof typeof form]);
    if (typeof parsedNumber === "number") {
      obj[key] = parsedNumber;
    }
  }
  // 当前 JSON 里非标准键（extra_body 与厂商专有键）随表单编辑一起带走，不被抹掉；
  // 当前 JSON 无效时带不走既有内容（与原型稿同口径），保存会被拦下。
  try {
    const parsed = JSON.parse(currentJson) as Record<string, unknown>;
    for (const key of Object.keys(parsed)) {
      if (!ADV_STANDARD_KEYS.includes(key)) {
        obj[key] = parsed[key];
      }
    }
  } catch {
    // 原 JSON 无效：忽略
  }
  // 全空 = 未设置任何参数：显示空串（输入框的参数说明 placeholder 可见，2026-09-13 用户反馈）。
  if (Object.keys(obj).length === 0) {
    return "";
  }
  return JSON.stringify(obj, null, 2);
}

/** 粘贴 JSON → 表单三键 + 同步状态（JSON → 表单方向；未知键提示已忽略、不报错）。 */
export function syncFormFromJson(text: string): {
  form: { temperature: string; top_p: string; max_tokens: string };
  state: AdvJsonState;
} {
  // 清空输入框 = 未填写，不是语法错误：表单同步清空、状态回到「已同步」。
  if (text.trim() === "") {
    return {
      form: { temperature: "", top_p: "", max_tokens: "" },
      state: { kind: "ok", text: "已同步（留空 = 全部用端点默认值）" },
    };
  }
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(text) as Record<string, unknown>;
  } catch {
    return {
      form: { temperature: "", top_p: "", max_tokens: "" },
      state: { kind: "invalid", text: "JSON 无效——修好前不同步到表单" },
    };
  }
  const read = (key: string): string => {
    const value = parsed[key];
    return value === undefined || value === null ? "" : String(value);
  };
  const ignored = Object.keys(parsed).filter(
    (key) => !ADV_STANDARD_KEYS.includes(key) && key !== "extra_body",
  );
  return {
    form: {
      temperature: read("temperature"),
      top_p: read("top_p"),
      max_tokens: read("max_tokens"),
    },
    state:
      ignored.length > 0
        ? {
            kind: "ignored",
            text: `已同步已知参数 · 暂不支持、已忽略：${ignored.join("、")}（端点专有参数可放 extra_body 透传）`,
          }
        : { kind: "ok", text: "已同步" },
  };
}

/** 高级参数表单 → 保存载荷；有问题返回 error（JSON 无效 / 数值字段非数字）。 */
export function collectAdvParams(input: {
  form: { temperature: string; top_p: string; max_tokens: string };
  transport: { timeout_seconds: string; max_retries: string };
  json: string;
}): { params: EndpointRequestParams; error: string | null } {
  const badNumber = (label: string): string =>
    `高级参数「${label}」不是有效数字——请修正后再保存（留空 = 用端点默认）。`;
  const temperature = parseNumField(input.form.temperature);
  if (temperature === "bad") {
    return { params: {}, error: badNumber("temperature") };
  }
  const topP = parseNumField(input.form.top_p);
  if (topP === "bad") {
    return { params: {}, error: badNumber("top_p") };
  }
  const maxTokens = parseNumField(input.form.max_tokens);
  if (maxTokens === "bad") {
    return { params: {}, error: badNumber("max_tokens") };
  }
  if (maxTokens !== null && !Number.isInteger(maxTokens)) {
    return { params: {}, error: "高级参数「max_tokens」应是整数——请修正后再保存。" };
  }
  const timeoutSeconds = parseNumField(input.transport.timeout_seconds);
  if (timeoutSeconds === "bad") {
    return { params: {}, error: badNumber("timeout_seconds") };
  }
  const maxRetries = parseNumField(input.transport.max_retries);
  if (maxRetries === "bad") {
    return { params: {}, error: badNumber("max_retries") };
  }
  if (maxRetries !== null && !Number.isInteger(maxRetries)) {
    return { params: {}, error: "高级参数「max_retries」应是整数——请修正后再保存。" };
  }
  let parsed: Record<string, unknown>;
  if (input.json.trim() === "") {
    // 清空 = 全部不设（与「表单全空 + 无 extra_body」同义），不是语法错误。
    parsed = {};
  } else {
    try {
      parsed = JSON.parse(input.json) as Record<string, unknown>;
    } catch {
      return {
        params: {},
        error: "高级参数的 JSON 写法无效——修好后再保存，或清空该输入框。",
      };
    }
  }
  const extraBody = parsed.extra_body;
  if (
    extraBody !== undefined &&
    extraBody !== null &&
    (typeof extraBody !== "object" || Array.isArray(extraBody))
  ) {
    return {
      params: {},
      error: "高级参数「extra_body」应是 JSON 对象（键值对）——请检查写法。",
    };
  }
  const params: EndpointRequestParams = {};
  if (temperature !== null) {
    params.temperature = temperature;
  }
  if (topP !== null) {
    params.top_p = topP;
  }
  if (maxTokens !== null) {
    params.max_tokens = maxTokens;
  }
  if (extraBody !== undefined && extraBody !== null) {
    params.extra_body = extraBody as Record<string, unknown>;
  }
  if (timeoutSeconds !== null) {
    params.timeout_seconds = timeoutSeconds;
  }
  if (maxRetries !== null) {
    params.max_retries = maxRetries;
  }
  return { params, error: null };
}
