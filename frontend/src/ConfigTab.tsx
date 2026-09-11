import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { api, errorMessage } from "./api";

/** 把密钥来源标识翻成人话。 */
function sourceLabel(keySource: string | null): string {
  if (keySource === "env") {
    return "环境变量 DSF_API_KEY";
  }
  if (keySource === "file") {
    return "credentials 文件";
  }
  return "未配置";
}

/** 端点配置：base_url / 模型名 / 密钥（密钥写入凭据文件，界面不回显、接口不返回内容）。 */
export function ConfigTab(): ReactElement {
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [keySource, setKeySource] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const config = await api.getConfig();
        if (cancelled) {
          return;
        }
        setBaseUrl(config.base_url ?? "");
        setModel(config.model ?? "");
        setKeyConfigured(config.api_key_configured);
        setKeySource(config.key_source);
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async (): Promise<void> => {
    try {
      const trimmedKey = apiKey.trim();
      await api.updateConfig({
        base_url: baseUrl,
        model,
        // 没填新密钥就整个不传该字段：后端沿用已存密钥，不必重输。
        ...(trimmedKey === "" ? {} : { api_key: trimmedKey }),
      });
      setMessage("配置已保存");
      setError("");
      setApiKey("");
      const config = await api.getConfig();
      setKeyConfigured(config.api_key_configured);
      setKeySource(config.key_source);
    } catch (err) {
      setError(errorMessage(err));
      setMessage("");
    }
  };

  return (
    <div className="card">
      <h2>端点配置（OpenAI 兼容；更换 base_url / 模型名 / 密钥即切换端点）</h2>
      <label htmlFor="config-base-url">base_url</label>
      <input
        id="config-base-url"
        type="text"
        value={baseUrl}
        placeholder="如 https://opencode.ai/zen/go/v1"
        onInput={(event) => setBaseUrl(event.currentTarget.value)}
      />
      <label htmlFor="config-model">模型名</label>
      <input
        id="config-model"
        type="text"
        value={model}
        placeholder="如 deepseek-v4-flash-vision-exp"
        onInput={(event) => setModel(event.currentTarget.value)}
      />
      <label htmlFor="config-api-key">
        API 密钥（
        {keyConfigured ? `已配置（来源：${sourceLabel(keySource)}）` : "未配置"}）
      </label>
      <input
        id="config-api-key"
        type="password"
        value={apiKey}
        placeholder={keyConfigured ? "留空 = 沿用已配置密钥" : "请输入密钥"}
        onInput={(event) => setApiKey(event.currentTarget.value)}
      />
      <div className="hint">
        密钥写入本地 credentials 文件，界面不回显、接口不返回内容。
      </div>
      {error !== "" && <div className="error">{error}</div>}
      {message !== "" && <div className="ok-msg">{message}</div>}
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={baseUrl.trim() === "" || model.trim() === ""}
          onClick={() => void save()}
        >
          保存
        </button>
      </div>
    </div>
  );
}
