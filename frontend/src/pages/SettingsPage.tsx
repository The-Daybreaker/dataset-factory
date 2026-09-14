/**
 * 设置容器（design「设置容器与技能预览」）：左二级导航（连接 / 能力 / 服务）+ 右内容区。
 * 子页切换即导航切换；未来新设置项（生成参数等）加导航项即可，不再新增主导航。
 */
import {
  ChevronDownIcon,
  CopyIcon,
  FileTextIcon,
  FolderOpenIcon,
  ImageIcon,
  LockIcon,
  PlusIcon,
  SearchIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  EndpointConfigSummary,
  EndpointRequestParams,
  EndpointTestResult,
  ServiceLogs,
  ServiceStatus,
  SkillFileInfo,
  SkillImportResponse,
  SkillInfo,
} from "../api";
import { api, errorMessage } from "../api";
import { ShutdownButton } from "../components/shutdown-button";
import { ThemeToggle } from "../components/theme-toggle";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { Switch } from "../components/ui/switch";
import { Textarea } from "../components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../components/ui/tooltip";
import { cn } from "../lib/utils";

type SettingsSection = "endpoints" | "skills" | "service";

interface Feedback {
  kind: "success" | "error";
  text: string;
}

/** 注入正文字符数（SKILL.md + references）→ 列表徽标文案（即打标请求的实际注入量）。 */
function formatChars(count: number): string {
  if (count >= 10_000) {
    return `${(count / 10_000).toFixed(1)} 万字`;
  }
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}k 字`;
  }
  return `${count} 字`;
}

/** 一期唯一支持的调用格式；其余选项灰显「暂未支持」，未来补适配器即启用。 */
const SUPPORTED_API_FORMAT = "openai-chat-completions";

const API_FORMATS: ReadonlyArray<{ value: string; label: string; enabled: boolean }> = [
  { value: SUPPORTED_API_FORMAT, label: "OpenAI Chat Completions", enabled: true },
  { value: "openai-responses", label: "OpenAI Responses（暂未支持）", enabled: false },
  {
    value: "anthropic-messages",
    label: "Anthropic Messages（暂未支持）",
    enabled: false,
  },
];

/* ---------- 高级参数区（原型稿 ui-draft-05 同构：表单 ⇄ JSON 双向同步） ---------- */

/** 「模型通用参数」的表单键——JSON ⇄ 表单双向同步只发生在这些键上。 */
const ADV_STANDARD_KEYS: readonly string[] = ["temperature", "top_p", "max_tokens"];

/** JSON 同步状态（原型稿 advJsonState 同款三态）。 */
interface AdvJsonState {
  kind: "ok" | "invalid" | "ignored";
  text: string;
}

/** 已设置的请求参数 → 展示用 JSON（标准键在前、extra_body 最后；全空 = 空串，让输入框显示参数说明）。 */
function paramsToJson(params: EndpointRequestParams): string {
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
function formToJson(
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
function syncFormFromJson(text: string): {
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
function collectAdvParams(input: {
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

/* ================= 连接 · 端点配置（列表 + 详情双栏） ================= */

function EndpointConfigPanel(): ReactElement {
  const [endpoints, setEndpoints] = useState<EndpointConfigSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftBaseUrl, setDraftBaseUrl] = useState("");
  const [draftFormat, setDraftFormat] = useState<string>(SUPPORTED_API_FORMAT);
  const [draftModel, setDraftModel] = useState("");
  const [draftKey, setDraftKey] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<EndpointTestResult | null>(null);
  // 高级参数区（默认折叠）：模型通用参数（表单 ⇄ JSON）+ 本项目传输参数（仅表单）。
  const [advOpen, setAdvOpen] = useState(false);
  const [advForm, setAdvForm] = useState({
    temperature: "",
    top_p: "",
    max_tokens: "",
  });
  const [advTransport, setAdvTransport] = useState({
    timeout_seconds: "",
    max_retries: "",
  });
  const [advJson, setAdvJson] = useState("{}");
  const [advState, setAdvState] = useState<AdvJsonState>({
    kind: "ok",
    text: "已同步",
  });

  const reload = useCallback(async (): Promise<EndpointConfigSummary[]> => {
    try {
      const list = await api.listEndpoints();
      setEndpoints(list);
      return list;
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
      return [];
    }
  }, []);

  useEffect(() => {
    void reload().then((list) => {
      const active = list.find((item) => item.is_active);
      if (active !== undefined) {
        setSelected(active.name);
      }
    });
  }, [reload]);

  const current = endpoints.find((item) => item.name === selected);

  // 选中变化（或保存后列表刷新）时把详情回填为落盘值；创建态保留空白草稿。
  useEffect(() => {
    if (creating || current === undefined) {
      return;
    }
    setDraftName(current.name);
    setDraftBaseUrl(current.base_url);
    setDraftFormat(current.api_format);
    setDraftModel(current.model);
    setDraftKey("");
    // 高级参数区同样回填落盘值；折叠态复位（换一套配置重新看）。
    // ?? {} 是运行时防御：契约里 request_params 必填，但「旧后端进程 + 新页面」的
    // 热升级窗口里响应可能没有这个字段（实机白屏事故的根因，2026-09-13）——缺字段
    // 按未设置处理，绝不让详情页崩树。
    const params = current.request_params ?? {};
    const text = (value: number | null | undefined): string =>
      value === null || value === undefined ? "" : String(value);
    setAdvForm({
      temperature: text(params.temperature),
      top_p: text(params.top_p),
      max_tokens: text(params.max_tokens),
    });
    setAdvTransport({
      timeout_seconds: text(params.timeout_seconds),
      max_retries: text(params.max_retries),
    });
    setAdvJson(paramsToJson(params));
    setAdvState({ kind: "ok", text: "已同步" });
    setAdvOpen(false);
  }, [
    creating,
    current?.name,
    current?.base_url,
    current?.model,
    current?.api_format,
    current,
  ]);

  const pick = (name: string): void => {
    setSelected(name);
    setCreating(false);
    setFeedback(null);
    setTestResult(null);
  };

  const startCreate = (): void => {
    setCreating(true);
    setSelected("");
    setDraftName("");
    setDraftBaseUrl("");
    setDraftFormat(SUPPORTED_API_FORMAT);
    setDraftModel("");
    setDraftKey("");
    setAdvForm({ temperature: "", top_p: "", max_tokens: "" });
    setAdvTransport({ timeout_seconds: "", max_retries: "" });
    setAdvJson("{}");
    setAdvState({ kind: "ok", text: "已同步" });
    setAdvOpen(false);
    setFeedback(null);
    setTestResult(null);
  };

  /** 模型通用参数表单输入 → 同步刷新 JSON（标准键取表单值，非标准键从既有 JSON 带走）。 */
  const onAdvFormField = (
    key: "temperature" | "top_p" | "max_tokens",
    value: string,
  ): void => {
    const nextForm = { ...advForm, [key]: value };
    setAdvForm(nextForm);
    setAdvJson(formToJson(nextForm, advJson));
    setAdvState({ kind: "ok", text: "已同步" });
  };

  /** 粘贴 / 编辑 JSON → 同步回表单三键；无效时只改状态提示（不同步、不报错打断）。 */
  const onAdvJsonInput = (value: string): void => {
    setAdvJson(value);
    const synced = syncFormFromJson(value);
    if (synced.state.kind !== "invalid") {
      setAdvForm(synced.form);
    }
    setAdvState(synced.state);
  };

  const testConnection = async (): Promise<void> => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testEndpoint({
        base_url: draftBaseUrl,
        model: draftModel,
        api_format: draftFormat,
        ...(creating ? {} : { name: selected }),
        ...(draftKey.trim() === "" ? {} : { api_key: draftKey }),
      });
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, message: errorMessage(err), latency_ms: 0 });
    } finally {
      setTesting(false);
    }
  };

  const save = async (): Promise<void> => {
    const key = draftKey.trim();
    // 高级参数先本地校验（JSON 语法 / 数值合法性），不过关就拦下——不给后端扔必错的请求。
    const adv = collectAdvParams({
      form: advForm,
      transport: advTransport,
      json: advJson,
    });
    if (adv.error !== null) {
      setFeedback({ kind: "error", text: adv.error });
      return;
    }
    try {
      if (creating) {
        const created = await api.createEndpoint({
          name: draftName,
          base_url: draftBaseUrl,
          model: draftModel,
          api_format: draftFormat,
          request_params: adv.params,
          // 密钥可留空：之后可再编辑补配，或用环境变量 DSF_API_KEY 兜底。
          ...(key === "" ? {} : { api_key: key }),
        });
        setFeedback({ kind: "success", text: `已创建配置「${created.name}」` });
        setCreating(false);
        setSelected(created.name);
      } else {
        const updated = await api.updateEndpoint(selected, {
          base_url: draftBaseUrl,
          model: draftModel,
          api_format: draftFormat,
          request_params: adv.params,
          // 没填新密钥就整个不传：后端沿用该配置已存密钥，不必重输。
          ...(key === "" ? {} : { api_key: key }),
        });
        setFeedback({ kind: "success", text: `已保存「${updated.name}」的更改` });
      }
      setDraftKey("");
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const activate = async (): Promise<void> => {
    try {
      await api.activateEndpoint(selected);
      await reload();
      setFeedback({
        kind: "success",
        text: `已切换当前使用的配置为「${selected}」，对新请求立即生效`,
      });
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const remove = async (): Promise<void> => {
    try {
      await api.deleteEndpoint(selected);
      setDeleteDialogOpen(false);
      setFeedback({ kind: "success", text: `已删除配置「${selected}」` });
      setSelected("");
      await reload();
    } catch (err) {
      setDeleteDialogOpen(false);
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const canSave =
    draftName.trim() !== "" && draftBaseUrl.trim() !== "" && draftModel.trim() !== "";

  return (
    <div className="grid h-full min-h-0 grid-cols-[340px_1fr] overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      {/* 左：配置列表 */}
      <div className="flex min-h-0 flex-col p-2">
        <div className="flex items-baseline gap-2 px-3 pt-2.5 pb-1.5">
          <h3 className="text-[13px] font-semibold">端点配置</h3>
          <span className="text-[11px] text-muted-foreground">
            {endpoints.length} 套
          </span>
        </div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
          {endpoints.map((item) => {
            const active = item.name === selected;
            return (
              <button
                key={item.name}
                type="button"
                onClick={() => pick(item.name)}
                aria-current={active ? "true" : undefined}
                className={
                  "relative block w-full rounded-md px-3 py-2.5 text-left transition-colors " +
                  // 激活行 hover 保持蓝系（与导航同口径，2026-09-13 用户反馈）。
                  (active ? "bg-primary/10 hover:bg-primary/15" : "hover:bg-accent")
                }
              >
                {active && (
                  <span
                    className="absolute top-1 bottom-1 left-0 w-0.5 rounded-full bg-primary"
                    aria-hidden
                  />
                )}
                <span className="flex items-center gap-2">
                  <span
                    className={
                      "size-2 rounded-full " +
                      (item.is_active ? "bg-success" : "bg-muted-foreground/30")
                    }
                    title={item.is_active ? "当前使用" : undefined}
                  />
                  <span
                    className={
                      "truncate text-[13.5px] font-medium" +
                      (active ? " text-primary" : "")
                    }
                  >
                    {item.name}
                  </span>
                </span>
                <span className="mt-0.5 block truncate pl-4 text-[12.5px] text-muted-foreground">
                  {item.model}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={startCreate}
            className="mt-1 flex w-full items-center gap-2 rounded-md border border-dashed border-border px-3 py-2.5 text-[13px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
          >
            <PlusIcon className="size-4" /> 添加配置
          </button>
        </div>
      </div>

      {/* 右：详情 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto border-l border-border p-5">
        {creating || current !== undefined ? (
          <div className="mx-auto w-full max-w-xl space-y-4">
            <div className="flex items-center gap-2">
              <h3 className="text-[15px] font-semibold">
                {creating ? "添加配置" : current?.name}
              </h3>
              {!creating && current?.is_active && (
                <Badge variant="success">当前使用</Badge>
              )}
              {!creating && (
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  className="ml-auto"
                  onClick={() => setDeleteDialogOpen(true)}
                >
                  删除
                </Button>
              )}
            </div>

            <div className="space-y-1">
              <Label htmlFor="endpoint-name">名称</Label>
              <Input
                id="endpoint-name"
                value={draftName}
                disabled={!creating}
                placeholder="如 siliconflow"
                onInput={(event) => setDraftName(event.currentTarget.value)}
              />
              <p className="text-[12px] text-muted-foreground">
                {creating
                  ? "即数据目录名，创建后不可改。"
                  : "名称即目录名，创建后不可改。"}
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-base-url">Base URL</Label>
              <Input
                id="endpoint-base-url"
                value={draftBaseUrl}
                placeholder="如 https://api.siliconflow.cn/v1"
                onInput={(event) => setDraftBaseUrl(event.currentTarget.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-format">API 格式</Label>
              <Select
                value={draftFormat}
                onValueChange={setDraftFormat}
                disabled={!creating && current === undefined}
              >
                <SelectTrigger id="endpoint-format">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {API_FORMATS.map((format) => (
                    <SelectItem
                      key={format.value}
                      value={format.value}
                      disabled={!format.enabled}
                    >
                      {format.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-model">模型名</Label>
              <Input
                id="endpoint-model"
                value={draftModel}
                placeholder="如 Qwen/Qwen3.5-4B"
                onInput={(event) => setDraftModel(event.currentTarget.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="endpoint-api-key">API 密钥</Label>
              <Input
                id="endpoint-api-key"
                type="password"
                value={draftKey}
                placeholder={
                  !creating && (current?.has_api_key ?? false)
                    ? "留空 = 沿用已存密钥"
                    : "请输入密钥"
                }
                onInput={(event) => setDraftKey(event.currentTarget.value)}
              />
              <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                {(current?.has_api_key ?? draftKey.trim() !== "") && (
                  <span className="size-2 rounded-full bg-success" aria-hidden />
                )}
                {(current?.has_api_key ?? false)
                  ? `已配置${current?.is_active ? " · 来源：credentials 文件" : ""}`
                  : "未配置——可之后补配，或用环境变量 DSF_API_KEY 兜底"}
              </p>
              <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <LockIcon className="size-3" /> 密钥写入该配置的 credentials
                文件，界面不回显、接口不返回内容。
              </p>
            </div>

            {feedback !== null && (
              <Alert variant={feedback.kind === "error" ? "destructive" : "success"}>
                <AlertDescription>{feedback.text}</AlertDescription>
              </Alert>
            )}

            <div className="flex items-center gap-2.5">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={
                  testing || draftBaseUrl.trim() === "" || draftModel.trim() === ""
                }
                onClick={() => void testConnection()}
              >
                {testing ? "测试中…" : "测试连接"}
              </Button>
              {testResult !== null && (
                <span
                  className={
                    "text-[12.5px] " +
                    (testResult.ok ? "text-success" : "text-destructive")
                  }
                  role="status"
                >
                  {testResult.message}
                  {testResult.ok ? ` · ${Math.round(testResult.latency_ms)} ms` : ""}
                </span>
              )}
            </div>

            {/* 高级参数（可选）：默认折叠；原型稿 ui-draft-05 为视觉事实源。 */}
            <div className="rounded-lg border border-border">
              <button
                type="button"
                aria-expanded={advOpen}
                onClick={() => setAdvOpen((open) => !open)}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-[13px] font-medium transition-colors hover:bg-accent"
              >
                <ChevronDownIcon
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform",
                    advOpen && "rotate-180",
                  )}
                  aria-hidden
                />
                高级参数（可选）
                <span className="text-[11.5px] font-normal text-muted-foreground">
                  temperature / top_p / max_tokens / 超时 / 重试 / extra_body——留空 =
                  端点默认值
                </span>
              </button>
              {advOpen && (
                <div className="space-y-4 border-t border-border px-3 py-3">
                  <div className="space-y-2">
                    <p className="text-[12px] font-medium">
                      模型通用参数（JSON，与表单双向同步——可直接从厂商文档粘贴）
                    </p>
                    <div className="grid grid-cols-3 gap-2">
                      {(["temperature", "top_p", "max_tokens"] as const).map((key) => (
                        <div key={key} className="space-y-1">
                          <Label
                            htmlFor={`adv-${key}`}
                            className="font-mono text-[11.5px] font-normal text-muted-foreground"
                          >
                            {key}
                          </Label>
                          <Input
                            id={`adv-${key}`}
                            type="number"
                            step={key === "max_tokens" ? "1" : "any"}
                            inputMode={key === "max_tokens" ? "numeric" : "decimal"}
                            value={advForm[key]}
                            placeholder={
                              key === "max_tokens"
                                ? "留空使用端点默认，或输入正整数（如 1024）"
                                : "留空使用端点默认，或输入 0 以上的数值"
                            }
                            onInput={(event) =>
                              onAdvFormField(key, event.currentTarget.value)
                            }
                          />
                        </div>
                      ))}
                    </div>
                    <Textarea
                      aria-label="模型通用参数 JSON"
                      spellCheck={false}
                      className="min-h-[110px] font-mono text-[12.5px]"
                      value={advJson}
                      placeholder={`{
  "temperature": 0.7,
  "top_p": 0.9,
  "max_tokens": 1024,
  "extra_body": { "top_k": 50 }
}`}
                      onInput={(event) => onAdvJsonInput(event.currentTarget.value)}
                    />
                    <p
                      role="status"
                      className={cn(
                        "text-[12px]",
                        advState.kind === "invalid" && "text-destructive",
                        advState.kind === "ignored" &&
                          "text-amber-600 dark:text-amber-500",
                        advState.kind === "ok" && "text-muted-foreground",
                      )}
                    >
                      {advState.text}
                    </p>
                  </div>
                  <div className="space-y-2">
                    <p className="text-[12px] font-medium">
                      本项目传输参数（仅表单，不提供 JSON）
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1">
                        <Label
                          htmlFor="adv-timeout"
                          className="font-mono text-[11.5px] font-normal text-muted-foreground"
                        >
                          timeout_seconds · 单次请求超时秒数
                        </Label>
                        <Input
                          id="adv-timeout"
                          type="number"
                          step="any"
                          inputMode="decimal"
                          value={advTransport.timeout_seconds}
                          placeholder="留空 = 120 秒（内置默认）"
                          onInput={(event) => {
                            // 先取值再进更新函数：React 的事件对象在更新器执行时已失效。
                            const value = event.currentTarget.value;
                            setAdvTransport((current) => ({
                              ...current,
                              timeout_seconds: value,
                            }));
                          }}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label
                          htmlFor="adv-retries"
                          className="font-mono text-[11.5px] font-normal text-muted-foreground"
                        >
                          max_retries · 失败自动重试次数
                        </Label>
                        <Input
                          id="adv-retries"
                          type="number"
                          step="1"
                          inputMode="numeric"
                          value={advTransport.max_retries}
                          placeholder="留空 = 2 次（内置默认）"
                          onInput={(event) => {
                            const value = event.currentTarget.value;
                            setAdvTransport((current) => ({
                              ...current,
                              max_retries: value,
                            }));
                          }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center gap-2 border-t border-border pt-4">
              {!creating && (
                <span className="text-[12px] text-muted-foreground">
                  切换「设为当前使用」立即生效于新请求
                </span>
              )}
              <span className="flex-1" />
              <Button
                type="button"
                disabled={!canSave}
                variant="outline"
                onClick={() => void save()}
              >
                {creating ? "创建配置" : "保存更改"}
              </Button>
              {!creating && current !== undefined && !current.is_active && (
                <Button type="button" onClick={() => void activate()}>
                  设为当前使用
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
            左侧选择一套配置，或「添加配置」新建。
          </div>
        )}
      </div>

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除端点配置「{selected}」？</DialogTitle>
            <DialogDescription>
              将连同该配置的密钥文件一起移除；当前使用中的配置需先切换才能删。此操作不可撤销。
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
              onClick={() => void remove()}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ================= 能力 · 技能（列表 + 详情双栏，含包内容预览） ================= */

function SkillFileChip({
  entry,
  active,
  onSelect,
}: {
  entry: SkillFileInfo;
  active: boolean;
  onSelect: (path: string) => void;
}): ReactElement {
  const chip = (
    <button
      type="button"
      disabled={!entry.previewable}
      onClick={() => onSelect(entry.path)}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[12px] transition-colors",
        entry.previewable
          ? "border-border bg-card hover:border-primary/50 hover:text-primary"
          : "cursor-not-allowed border-dashed border-border text-muted-foreground/60 opacity-70",
        active && entry.previewable && "border-primary bg-primary/10 text-primary",
      )}
    >
      {entry.path.startsWith("references/") ? (
        <FileTextIcon className="size-3" />
      ) : entry.path.startsWith("assets/") ? (
        <ImageIcon className="size-3" />
      ) : (
        <CopyIcon className="size-3" />
      )}
      {entry.path}
    </button>
  );
  if (entry.previewable) {
    return chip;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{chip}</span>
      </TooltipTrigger>
      <TooltipContent>不参与注入（注入范围 = SKILL.md 与 references/）</TooltipContent>
    </Tooltip>
  );
}

function SkillsPanel(): ReactElement {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const [files, setFiles] = useState<SkillFileInfo[]>([]);
  const [previewPath, setPreviewPath] = useState("");
  const [previewContent, setPreviewContent] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pathValue, setPathValue] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mdInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const list = await api.listSkills();
      setSkills(list);
      // 首次加载默认选中第一个技能（详情区直接有内容；用户可再点选其他）。
      setSelected((current) =>
        current === "" && list.length > 0 ? (list[0]?.name ?? "") : current,
      );
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 选中变化 → 拉包文件清单；默认预览 SKILL.md（注入源）。
  useEffect(() => {
    if (selected === "") {
      setFiles([]);
      setPreviewPath("");
      setPreviewContent("");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.listSkillFiles(selected);
        if (cancelled) {
          return;
        }
        setFiles(result.files);
        const first = result.files.find((entry) => entry.previewable);
        if (first !== undefined) {
          const content = await api.readSkillFile(selected, first.path);
          if (!cancelled) {
            setPreviewPath(first.path);
            setPreviewContent(content.content);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setFeedback({ kind: "error", text: errorMessage(err) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const current = skills.find((item) => item.name === selected);
  const keyword = search.trim().toLowerCase();
  const visible =
    keyword === ""
      ? skills
      : skills.filter(
          (item) =>
            item.name.toLowerCase().includes(keyword) ||
            item.description.toLowerCase().includes(keyword),
        );

  const pick = (name: string): void => {
    setSelected(name);
    setFeedback(null);
  };

  const toggle = async (skill: SkillInfo): Promise<void> => {
    try {
      await api.setSkillEnabled(skill.name, !skill.enabled);
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const applyImportResult = (result: SkillImportResponse): void => {
    const sizeKb = (result.total_bytes / 1024).toFixed(1);
    setFeedback({
      kind: "success",
      text: `已导入「${result.name}」（${sizeKb} KiB——skill 全文将注入打标请求，体积偏大时留意 token 消耗）`,
    });
    setSelected(result.name);
  };

  const doImport = async (picked: File[]): Promise<void> => {
    setImporting(true);
    try {
      applyImportResult(await api.importSkillFiles(picked));
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setImporting(false);
    }
  };

  const doImportFile = async (picked: File | undefined): Promise<void> => {
    if (picked === undefined) {
      return;
    }
    setImporting(true);
    try {
      applyImportResult(await api.importSkillFile(picked));
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setImporting(false);
    }
  };

  const doImportPath = async (): Promise<void> => {
    const path = pathValue.trim();
    if (path === "") {
      return;
    }
    setImporting(true);
    try {
      applyImportResult(await api.importSkill(path));
      setPathValue("");
      await reload();
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    } finally {
      setImporting(false);
    }
  };

  const remove = async (): Promise<void> => {
    if (selected === "") {
      return;
    }
    try {
      await api.deleteSkill(selected);
      setDeleteDialogOpen(false);
      setFeedback({ kind: "success", text: `已删除「${selected}」` });
      setSelected("");
      await reload();
    } catch (err) {
      setDeleteDialogOpen(false);
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  const openPreview = async (path: string): Promise<void> => {
    if (selected === "") {
      return;
    }
    try {
      const content = await api.readSkillFile(selected, path);
      setPreviewPath(path);
      setPreviewContent(content.content);
    } catch (err) {
      setFeedback({ kind: "error", text: errorMessage(err) });
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[340px_1fr] overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      {/* 左：列表 + 导入 */}
      <div className="flex min-h-0 flex-col p-2">
        <div className="flex items-baseline gap-2 px-3 pt-2.5 pb-1.5">
          <h3 className="text-[13px] font-semibold">技能</h3>
          <span className="text-[11px] text-muted-foreground">{skills.length} 个</span>
        </div>
        <div className="relative mx-1 mb-2">
          <SearchIcon className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索技能"
            placeholder="搜索名称或描述…"
            className="pl-8"
            value={search}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-1">
          {visible.length === 0 && (
            <p className="px-1 text-[12px] text-muted-foreground">
              （{skills.length === 0 ? "Skill 库为空——下方导入" : "没有匹配的技能"}）
            </p>
          )}
          {visible.map((skill) => {
            const active = skill.name === selected;
            return (
              <div
                key={skill.name}
                className={
                  "rounded-lg border p-2.5 transition-colors " +
                  (active
                    ? "border-primary bg-primary/10"
                    : "border-border bg-card hover:border-primary/40")
                }
              >
                <div className="flex items-center gap-2">
                  <Switch
                    checked={skill.enabled}
                    aria-label={`启用 ${skill.name}`}
                    onClick={() => void toggle(skill)}
                  />
                  <button
                    type="button"
                    onClick={() => pick(skill.name)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className="min-w-0 truncate text-[13px] font-medium">
                        {skill.name}
                      </span>
                      <span
                        className="shrink-0 rounded-full bg-muted px-1.5 text-[10.5px] leading-[1.6] text-muted-foreground"
                        title="注入正文字符数（SKILL.md + references，即打标请求的注入量）"
                      >
                        {formatChars(skill.body_chars)}
                      </span>
                    </span>
                    {skill.description === "" ? (
                      <span className="line-clamp-2 text-[12px] leading-relaxed text-muted-foreground">
                        （无描述）
                      </span>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          {/* 去掉 block：line-clamp-2 自带 -webkit-box 显示模式与省略号，
                              block 会把它覆盖成普通块级导致截断失效（长下划线词把卡片撑破
                              左栏宽度，2026-09-13 用户反馈）；anywhere 断长词兜底 */}
                          <span
                            className={
                              "line-clamp-2 text-[12px] leading-relaxed [overflow-wrap:anywhere] " +
                              (skill.description.startsWith("文件损坏：")
                                ? "text-destructive"
                                : "text-muted-foreground")
                            }
                          >
                            {skill.description}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-80 whitespace-normal leading-relaxed">
                          {skill.description}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </button>
                  <Badge variant={skill.enabled ? "success" : "muted"}>
                    {skill.enabled ? "已启用" : "已停用"}
                  </Badge>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-2 border-t border-border px-3 pt-3 pb-1">
          <p className="mb-2 text-[12px] font-medium">
            导入 skill 包（agentskills.io 标准）
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            aria-label="选择 skill 文件夹"
            // @ts-expect-error -- webkitdirectory 为浏览器非标准属性，React DOM 类型未收录
            webkitdirectory=""
            onChange={(event) => {
              const picked = Array.from(event.currentTarget.files ?? []);
              if (picked.length > 0) {
                void doImport(picked);
              }
              event.currentTarget.value = "";
            }}
          />
          <input
            ref={mdInputRef}
            type="file"
            accept=".md"
            hidden
            aria-label="选择 SKILL.md 文件"
            onChange={(event) => {
              void doImportFile(event.currentTarget.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={importing}
              onClick={() => fileInputRef.current?.click()}
            >
              <FolderOpenIcon className="size-4" />
              文件夹…
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={importing}
              onClick={() => mdInputRef.current?.click()}
            >
              <FileTextIcon className="size-4" />
              SKILL.md 文件…
            </Button>
          </div>
          <div className="mt-2 flex gap-2">
            <Input
              aria-label="skill 本机路径"
              placeholder="粘贴本机路径后导入…"
              value={pathValue}
              onInput={(event) => setPathValue(event.currentTarget.value)}
            />
            <Button
              type="button"
              variant="outline"
              disabled={importing || pathValue.trim() === ""}
              onClick={() => void doImportPath()}
            >
              导入
            </Button>
          </div>
          {feedback !== null && (
            <Alert
              variant={feedback.kind === "error" ? "destructive" : "success"}
              className="mt-2"
            >
              <AlertDescription>{feedback.text}</AlertDescription>
            </Alert>
          )}
        </div>
      </div>

      {/* 右：详情 + 包内容预览 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto border-l border-border p-5">
        {current !== undefined ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <h3 className="text-[15px] font-semibold">{current.name}</h3>
              <Badge variant={current.enabled ? "success" : "muted"}>
                {current.enabled ? "已启用" : "已停用"}
              </Badge>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                className="ml-auto"
                onClick={() => setDeleteDialogOpen(true)}
              >
                删除
              </Button>
            </div>
            <p className="text-[13px] text-muted-foreground">
              {current.description === "" ? "（无描述）" : current.description}
            </p>

            <div>
              <p className="mb-1.5 text-[12px] text-muted-foreground">
                包文件——SKILL.md 与 references/ 注入请求；assets / scripts 不参与注入
              </p>
              <div className="flex flex-wrap gap-1.5">
                {files.map((entry) => (
                  <SkillFileChip
                    key={entry.path}
                    entry={entry}
                    active={entry.path === previewPath}
                    onSelect={(path) => void openPreview(path)}
                  />
                ))}
              </div>
            </div>

            {previewPath !== "" && (
              <div className="min-h-0 overflow-hidden rounded-md border border-border">
                <div className="border-b border-border bg-muted/40 px-3 py-1.5 text-[12px] text-muted-foreground">
                  {previewPath}
                </div>
                <pre className="max-h-96 overflow-auto p-3 font-mono text-[12.5px] leading-[1.7] whitespace-pre-wrap">
                  {previewContent}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
            左侧选择一个技能查看详情与包内容。
          </div>
        )}
      </div>

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除技能「{selected}」？</DialogTitle>
            <DialogDescription>
              将从 Skill 库整目录移除该包。此操作不可撤销；停用 ≠ 删除。
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
              onClick={() => void remove()}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ================= 服务 · 服务运行（状态 + 日志） ================= */

function ServicePanel(): ReactElement {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [logs, setLogs] = useState<ServiceLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [nextStatus, nextLogs] = await Promise.all([
        api.getService(),
        api.getServiceLogs(),
      ]);
      setStatus(nextStatus);
      setLogs(nextLogs);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
        {status !== null ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-2 text-[13.5px] font-medium">
              <span className="size-2 rounded-full bg-success" aria-hidden />
              服务运行中 · {status.version}
            </span>
            <span className="text-[12.5px] text-muted-foreground">
              监听 {status.host}:{status.port}
            </span>
            <span className="flex-1" />
            <ShutdownButton expanded />
          </div>
        ) : (
          <p className="text-[13px] text-muted-foreground">{error ?? "读取中…"}</p>
        )}
      </div>
      <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-[13px] font-semibold">运行日志</h3>
          <span className="text-[11px] text-muted-foreground">最近 200 行</span>
          <span className="flex-1" />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => void reload()}
          >
            刷新
          </Button>
        </div>
        {logs?.exists ? (
          <pre className="max-h-80 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-[11.5px] leading-[1.75]">
            {logs.content}
          </pre>
        ) : (
          <p className="text-[12.5px] text-muted-foreground">
            {error ?? "（暂无日志）"}
          </p>
        )}
      </div>
    </div>
  );
}

/** 页头状态 chip：当前激活的端点配置（只读展示；切换在工作台切换器 / 本子页详情）。 */
function ActiveEndpointChip(): ReactElement {
  const [active, setActive] = useState<EndpointConfigSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .listEndpoints()
      .then((list) => {
        if (!cancelled) {
          setActive(list.find((item) => item.is_active) ?? null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setActive(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <span className="inline-flex h-[30px] items-center gap-[7px] rounded-full border border-border bg-card px-3 text-[12px] text-muted-foreground">
      <span
        className={
          "size-[7px] rounded-full " +
          (active !== null ? "bg-success" : "bg-muted-foreground/40")
        }
        aria-hidden
      />
      {active !== null ? `${active.name} · ${active.model}` : "未配置端点"}
    </span>
  );
}

/* ================= 设置容器 ================= */

const SECTIONS: ReadonlyArray<{ key: SettingsSection; group: string; label: string }> =
  [
    { key: "endpoints", group: "连接", label: "端点配置" },
    { key: "skills", group: "能力", label: "技能" },
    { key: "service", group: "服务", label: "服务运行" },
  ];

export function SettingsPage(): ReactElement {
  const [section, setSection] = useState<SettingsSection>("endpoints");

  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex items-center justify-between px-7 pt-[18px] pb-3.5">
          <div>
            <h1 className="text-[20px] leading-[1.3] font-semibold">设置</h1>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              模型端点与 Skill 库的集中管理。
            </p>
          </div>
          <div className="flex items-center gap-3">
            <ActiveEndpointChip />
            <ShutdownButton />
            <ThemeToggle />
          </div>
        </header>
        <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr] grid-rows-[minmax(0,1fr)] gap-4 px-7 pb-6">
          <nav className="pt-1" aria-label="设置二级导航">
            {(["连接", "能力", "服务"] as const).map((group) => (
              <div key={group}>
                <p className="px-2.5 pt-2.5 pb-1 text-[11.5px] font-semibold text-muted-foreground">
                  {group}
                </p>
                <ul className="space-y-0.5">
                  {SECTIONS.filter((item) => item.group === group).map((item) => {
                    const active = section === item.key;
                    return (
                      <li key={item.key}>
                        <button
                          type="button"
                          aria-current={active ? "true" : undefined}
                          onClick={() => setSection(item.key)}
                          className={
                            "flex h-[34px] w-full items-center gap-2 rounded-md px-2.5 text-[13.5px] transition-colors " +
                            // 激活项 hover 保持蓝系（与侧栏同口径，2026-09-13 用户反馈）。
                            (active
                              ? "bg-primary/10 font-medium text-primary hover:bg-primary/15"
                              : "text-muted-foreground hover:bg-accent hover:text-accent-foreground")
                          }
                        >
                          {item.label}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>

          {/* 端点 / 技能子页：面板吃满剩余高度、左右两栏各自内部滚动（2026-09-13 用户反馈
              ——此前右侧滚动会带动左侧列表一起滚）；服务子页是内容流，保持整区滚动。 */}
          <section
            className={cn(
              "min-w-0",
              section === "service"
                ? "overflow-y-auto"
                : "flex min-h-0 flex-col overflow-hidden",
            )}
            aria-label="设置内容区"
          >
            {section === "endpoints" && (
              <>
                <h2 className="mt-0.5 mb-3 shrink-0 text-[17px] font-semibold">
                  端点配置
                </h2>
                <p className="-mt-2 mb-3.5 shrink-0 text-[12.5px] text-muted-foreground">
                  OpenAI
                  兼容端点——可保存多套配置，随时切换当前使用；切换立即对新请求生效。
                </p>
                <div className="min-h-0 flex-1">
                  <EndpointConfigPanel />
                </div>
              </>
            )}
            {section === "skills" && (
              <>
                <h2 className="mt-0.5 mb-3 shrink-0 text-[17px] font-semibold">技能</h2>
                <p className="-mt-2 mb-3.5 shrink-0 text-[12.5px] text-muted-foreground">
                  导入 agentskills.io 标准 Skill 包；启用后 SKILL.md 与 references/
                  全文注入打标请求。
                </p>
                <div className="min-h-0 flex-1">
                  <SkillsPanel />
                </div>
              </>
            )}
            {section === "service" && (
              <>
                <h2 className="mt-0.5 mb-3 text-[17px] font-semibold">服务运行</h2>
                <ServicePanel />
              </>
            )}
          </section>
        </div>
      </div>
    </TooltipProvider>
  );
}
