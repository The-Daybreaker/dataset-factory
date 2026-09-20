/** 连接 · 端点配置（列表 + 详情双栏，含高级参数折叠区）。 */
import { ChevronDownIcon, LockIcon, PlusIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import type { EndpointConfigSummary, EndpointTestResult } from "../../api";
import { api, errorMessage } from "../../api";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { Textarea } from "../../components/ui/textarea";
import { type Feedback, reportError } from "../../lib/feedback";
import { cn } from "../../lib/utils";
import {
  type AdvJsonState,
  collectAdvParams,
  formToJson,
  paramsToJson,
  syncFormFromJson,
} from "./adv-params";

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

export function EndpointConfigPanel(): ReactElement {
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

  /** 失败分流：连接类失败改弹浮层（不占界面位置），后端返回的业务错误仍就地展示。 */
  const failFeedback = useCallback((err: unknown): void => {
    const text = reportError(err);
    if (text !== null) setFeedback({ kind: "error", text });
  }, []);

  const reload = useCallback(async (): Promise<EndpointConfigSummary[]> => {
    try {
      const list = await api.listEndpoints();
      setEndpoints(list);
      return list;
    } catch (err) {
      failFeedback(err);
      return [];
    }
  }, [failFeedback]);

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
      failFeedback(err);
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
      failFeedback(err);
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
      failFeedback(err);
    }
  };

  const canSave =
    draftName.trim() !== "" && draftBaseUrl.trim() !== "" && draftModel.trim() !== "";

  return (
    <div className="grid h-full min-h-0 grid-cols-[340px_1fr] overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      {/* 左：配置列表 */}
      <div className="flex min-h-0 flex-col p-2">
        <div className="flex items-baseline gap-2 px-3 pt-2.5 pb-1.5">
          <h3 className="text-t-md font-semibold">端点配置</h3>
          <span className="text-t-xs text-muted-foreground">{endpoints.length} 套</span>
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
                      "truncate text-t-md font-medium" + (active ? " text-primary" : "")
                    }
                  >
                    {item.name}
                  </span>
                </span>
                <span className="mt-0.5 block truncate pl-4 text-t-sm text-muted-foreground">
                  {item.model}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={startCreate}
            className="mt-1 flex w-full items-center gap-2 rounded-md border border-dashed border-border px-3 py-2.5 text-t-md text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
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
              <h3 className="text-t-lg font-semibold">
                {creating ? "添加配置" : current?.name}
              </h3>
              {!creating && current?.is_active && (
                <Badge variant="info">当前使用</Badge>
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
              <p className="text-t-sm text-muted-foreground">
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
              <Label htmlFor="endpoint-model">模型名称</Label>
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
                    ? "留空 = 沿用已配置密钥"
                    : "请输入密钥"
                }
                onInput={(event) => setDraftKey(event.currentTarget.value)}
              />
              <p className="flex items-center gap-1.5 text-t-sm text-muted-foreground">
                {(current?.has_api_key ?? draftKey.trim() !== "") && (
                  <span className="size-2 rounded-full bg-success" aria-hidden />
                )}
                {(current?.has_api_key ?? false)
                  ? `已配置${current?.is_active ? " · 来源：credentials 文件" : ""}`
                  : "未配置——可之后补配，或用环境变量 DSF_API_KEY 兜底"}
              </p>
              <p className="flex items-center gap-1.5 text-t-sm text-muted-foreground">
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
                variant="accent"
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
                    "text-t-sm " + (testResult.ok ? "text-success" : "text-destructive")
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
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-t-md font-medium transition-colors hover:bg-accent"
              >
                <ChevronDownIcon
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform",
                    advOpen && "rotate-180",
                  )}
                  aria-hidden
                />
                高级参数（可选）
                <span className="text-t-xs font-normal text-muted-foreground">
                  temperature / top_p / max_tokens / 超时 / 重试 / extra_body——留空 =
                  端点默认值
                </span>
              </button>
              {advOpen && (
                <div className="space-y-4 border-t border-border px-3 py-3">
                  <div className="space-y-2">
                    <p className="text-t-sm font-medium">
                      模型通用参数（JSON，与表单双向同步——可直接从厂商文档粘贴）
                    </p>
                    <div className="grid grid-cols-3 gap-2">
                      {(["temperature", "top_p", "max_tokens"] as const).map((key) => (
                        <div key={key} className="space-y-1">
                          <Label
                            htmlFor={`adv-${key}`}
                            className="text-t-xs font-normal text-muted-foreground"
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
                      className="min-h-[110px] text-t-sm"
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
                        "text-t-sm",
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
                    <p className="text-t-sm font-medium">
                      本项目传输参数（仅表单，不提供 JSON）
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1">
                        <Label
                          htmlFor="adv-timeout"
                          className="text-t-xs font-normal text-muted-foreground"
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
                          className="text-t-xs font-normal text-muted-foreground"
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
                <span className="text-t-sm text-muted-foreground">
                  切换「设为当前使用」立即生效于新请求
                </span>
              )}
              <span className="flex-1" />
              <Button type="button" disabled={!canSave} onClick={() => void save()}>
                {creating ? "创建配置" : "保存更改"}
              </Button>
              {!creating && current !== undefined && !current.is_active && (
                <Button type="button" variant="outline" onClick={() => void activate()}>
                  设为当前使用
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-t-md text-muted-foreground">
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
