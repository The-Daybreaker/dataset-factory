// Dataset Factory 打标测试台 —— Preact + htm 免构建单页应用。
// 覆盖一期全部能力：聊天打标（发图 + 指令 + 迭代改写 + 会话恢复）、提示词库管理、
// Skill 导入/启用、配置管理（密钥不回显）。只调后端 /api/*，无业务逻辑（入口层薄）。
/* global preact, preactHooks, htm */

const { h, render } = preact;
const { useState, useEffect } = preactHooks;
const html = htm.bind(h);

// ---- API 辅助：统一 fetch 封装，错误取 detail（后端结构化错误体）----
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw Array.isArray(detail)
      ? detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("；")
      : detail;
  }
  return data;
}

// ---- 根组件：四个 tab ----
function App() {
  const [tab, setTab] = useState("chat");
  const tabs = [
    ["chat", "聊天打标"],
    ["prompts", "提示词库"],
    ["skills", "Skill"],
    ["config", "配置"],
  ];
  return html`
    <div class="app">
      <header>
        <h1>Dataset Factory 打标测试台</h1>
        <nav class="tabs">
          ${tabs.map(
            ([key, label]) => html`
              <button
                key=${key}
                class=${tab === key ? "active" : ""}
                onClick=${() => setTab(key)}
              >
                ${label}
              </button>
            `,
          )}
        </nav>
      </header>
      ${tab === "chat" && html`<${ChatTab} />`}
      ${tab === "prompts" && html`<${PromptsTab} />`}
      ${tab === "skills" && html`<${SkillsTab} />`}
      ${tab === "config" && html`<${ConfigTab} />`}
    </div>
  `;
}

// ---- 聊天打标 tab ----
function ChatTab() {
  const [prompts, setPrompts] = useState([]);
  const [skills, setSkills] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [promptName, setPromptName] = useState("");
  const [skillNames, setSkillNames] = useState([]);
  const [messages, setMessages] = useState([]);
  const [instruction, setInstruction] = useState("");
  const [image, setImage] = useState(null); // { name, dataUrl }
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        setPrompts(await api("GET", "/api/prompts"));
        setSkills(await api("GET", "/api/skills"));
        const snapshot = await api("GET", "/api/sessions/latest");
        setSessionId(snapshot.session_id);
        setPromptName(snapshot.settings.prompt_name || "");
        setSkillNames(snapshot.settings.skill_names);
        setMessages(snapshot.messages);
      } catch (err) {
        if (!String(err).includes("还没有任何会话")) setError(String(err));
      }
    })();
  }, []);

  const toggleSkill = (name) => {
    setSkillNames((current) =>
      current.includes(name)
        ? current.filter((n) => n !== name)
        : [...current, name],
    );
  };

  const pickImage = (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImage({ name: file.name, dataUrl: reader.result });
    reader.readAsDataURL(file);
  };

  const send = async () => {
    if (!instruction.trim() && !image) return;
    setSending(true);
    setError("");
    const request = {
      session_id: sessionId,
      prompt_name: promptName || null,
      skill_names: skillNames,
      instruction,
      image_base64: image ? image.dataUrl : null,
      image_name: image ? image.name : "image.png",
    };
    try {
      const result = await api("POST", "/api/label", request);
      setMessages((current) => [
        ...current,
        {
          role: "user",
          text: instruction,
          attachment: image ? image.name : null,
        },
        { role: "assistant", text: result.caption, attachment: null },
      ]);
      setSessionId(result.session_id);
      setInstruction("");
      setImage(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setSending(false);
    }
  };

  const newSession = () => {
    setSessionId(null);
    setMessages([]);
    setError("");
  };

  return html`
    <div>
      <div class="card">
        <h2>会话设置（每个会话定一个基础提示词，可中途切换）</h2>
        <div class="row">
          <div>
            <label>基础提示词（必选）</label>
            <select
              value=${promptName}
              onChange=${(e) => setPromptName(e.target.value)}
            >
              <option value="">（请选择）</option>
              ${prompts.map(
                (p) => html`<option key=${p.name} value=${p.name}>${p.name}</option>`,
              )}
            </select>
          </div>
          <div>
            <label>启用的 Skill（可选叠加，勾选即注入全文）</label>
            ${skills.length === 0 &&
            html`<div class="empty">（Skill 库为空——到 Skill 页导入）</div>`}
            ${skills.map(
              (s) => html`
                <label key=${s.name} class="skill-check">
                  <input
                    type="checkbox"
                    checked=${skillNames.includes(s.name) && s.enabled}
                    disabled=${!s.enabled}
                    onChange=${() => toggleSkill(s.name)}
                  />
                  ${s.name}${s.enabled ? "" : "（已停用）"}
                </label>
              `,
            )}
          </div>
        </div>
      </div>

      <div class="card">
        <h2>对话${sessionId ? `（会话 ${sessionId}）` : "（新会话）"}</h2>
        <div class="chat-log">
          ${messages.length === 0 &&
          html`<div class="empty">（还没有消息——选好提示词，发图 + 指令开始打标）</div>`}
          ${messages.map(
            (m, i) => html`
              <div key=${i} class="msg ${m.role}">
                ${m.text}
                ${m.attachment &&
                html`<span class="attachment">[@${m.attachment}]</span>`}
              </div>
            `,
          )}
        </div>
        ${error && html`<div class="error">${error}</div>`}
        <div class="chat-input" style="margin-top:12px">
          <textarea
            placeholder="打标指令（如：给这张图打个标 / 改成两句话…）"
            value=${instruction}
            onInput=${(e) => setInstruction(e.target.value)}
          ></textarea>
          <div style="display:flex;gap:12px;align-items:center;margin-top:8px">
            <input type="file" accept="image/*" onChange=${pickImage} />
            ${image &&
            html`<img class="preview" src=${image.dataUrl} alt="待打标图片" />`}
            <div style="margin-left:auto;display:flex;gap:8px">
              <button class="ghost" onClick=${newSession}>新会话</button>
              <button
                class="primary"
                disabled=${sending || (!instruction.trim() && !image)}
                onClick=${send}
              >
                ${sending ? "打标中…" : "发送"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ---- 提示词库 tab ----
function PromptsTab() {
  const [prompts, setPrompts] = useState([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const refresh = async () => {
    try {
      setPrompts(await api("GET", "/api/prompts"));
    } catch (err) {
      setError(String(err));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const pick = async (picked) => {
    try {
      const full = await api("GET", `/api/prompts/${encodeURIComponent(picked)}`);
      setName(full.name);
      setDescription(full.description);
      setBody(full.body);
      setMessage("");
      setError("");
    } catch (err) {
      setError(String(err));
    }
  };

  const save = async () => {
    try {
      await api("PUT", `/api/prompts/${encodeURIComponent(name)}`, {
        description,
        body,
      });
      setMessage(`已保存提示词「${name}」`);
      setError("");
      await refresh();
    } catch (err) {
      setError(String(err));
      setMessage("");
    }
  };

  const remove = async () => {
    try {
      await api("DELETE", `/api/prompts/${encodeURIComponent(name)}`);
      setMessage(`已删除「${name}」`);
      setName("");
      setDescription("");
      setBody("");
      setError("");
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  return html`
    <div class="row">
      <div class="card">
        <h2>提示词库（${prompts.length} 条）</h2>
        ${prompts.length === 0 && html`<div class="empty">（空——右侧新建）</div>`}
        <ul class="item-list">
          ${prompts.map(
            (p) => html`
              <li
                key=${p.name}
                class=${p.name === name ? "selected" : ""}
                onClick=${() => pick(p.name)}
              >
                <span class="name">${p.name}</span>
                <span class="desc">${p.description || "（无描述）"}</span>
              </li>
            `,
          )}
        </ul>
      </div>
      <div class="card">
        <h2>编辑（保存时按名称存；改名另存为新条目，旧条目保留）</h2>
        <label>名称</label>
        <input
          type="text"
          value=${name}
          placeholder="如 h3-video"
          onInput=${(e) => setName(e.target.value)}
        />
        <label>描述（选择器里展示）</label>
        <input
          type="text"
          value=${description}
          placeholder="这条提示词产出什么、适合什么"
          onInput=${(e) => setDescription(e.target.value)}
        />
        <label>正文（提示词本体）</label>
        <textarea
          style="min-height:200px"
          value=${body}
          placeholder="你是……"
          onInput=${(e) => setBody(e.target.value)}
        ></textarea>
        ${error && html`<div class="error">${error}</div>`}
        ${message && html`<div class="ok-msg">${message}</div>`}
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="primary" disabled=${!name.trim()} onClick=${save}>
            保存
          </button>
          ${name &&
          html`<button class="danger" onClick=${remove}>删除「${name}」</button>`}
        </div>
      </div>
    </div>
  `;
}

// ---- Skill tab ----
function SkillsTab() {
  const [skills, setSkills] = useState([]);
  const [importPath, setImportPath] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const refresh = async () => {
    try {
      setSkills(await api("GET", "/api/skills"));
    } catch (err) {
      setError(String(err));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const doImport = async () => {
    try {
      const result = await api("POST", "/api/skills/import", { path: importPath });
      const sizeKb = (result.total_bytes / 1024).toFixed(1);
      setMessage(
        `已导入「${result.name}」（${sizeKb} KiB——skill 全文将注入打标请求，体积偏大时留意 token 消耗）`,
      );
      setError("");
      setImportPath("");
      await refresh();
    } catch (err) {
      setError(String(err));
      setMessage("");
    }
  };

  const toggle = async (skill) => {
    try {
      await api(
        "POST",
        `/api/skills/${encodeURIComponent(skill.name)}/${skill.enabled ? "disable" : "enable"}`,
      );
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const remove = async (skill) => {
    if (!confirm(`确认删除 skill「${skill.name}」？（整目录移除）`)) return;
    try {
      await api("DELETE", `/api/skills/${encodeURIComponent(skill.name)}`);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  return html`
    <div class="card">
      <h2>导入 skill 包（agentskills.io 标准，填本机 skill 目录路径）</h2>
      <div style="display:flex;gap:8px">
        <input
          type="text"
          style="flex:1"
          value=${importPath}
          placeholder="如 D:\skills\h3-prompt-writing"
          onInput=${(e) => setImportPath(e.target.value)}
        />
        <button class="primary" disabled=${!importPath.trim()} onClick=${doImport}>
          导入
        </button>
      </div>
      ${error && html`<div class="error">${error}</div>`}
      ${message && html`<div class="ok-msg">${message}</div>`}
    </div>
    <div class="card">
      <h2>Skill 库（${skills.length} 个）</h2>
      ${skills.length === 0 && html`<div class="empty">（空——上方导入）</div>`}
      <ul class="item-list">
        ${skills.map(
          (s) => html`
            <li key=${s.name}>
              <label style="margin:0">
                <input
                  type="checkbox"
                  checked=${s.enabled}
                  onChange=${() => toggle(s)}
                />
              </label>
              <span class="name">${s.name}</span>
              <span class="desc">${s.description || "（无描述）"}</span>
              <button class="danger" onClick=${() => remove(s)}>删除</button>
            </li>
          `,
        )}
      </ul>
      <div class="hint">勾选 = 启用（打标时注入全文）；取消 = 停用（保留在库中）。</div>
    </div>
  `;
}

// ---- 配置 tab ----
function ConfigTab() {
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [keySource, setKeySource] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const config = await api("GET", "/api/config");
        setBaseUrl(config.base_url || "");
        setModel(config.model || "");
        setKeyConfigured(config.api_key_configured);
        setKeySource(config.key_source);
      } catch (err) {
        setError(String(err));
      }
    })();
  }, []);

  const save = async () => {
    try {
      const body = { base_url: baseUrl, model };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      await api("PUT", "/api/config", body);
      setMessage("配置已保存");
      setError("");
      setApiKey("");
      const config = await api("GET", "/api/config");
      setKeyConfigured(config.api_key_configured);
      setKeySource(config.key_source);
    } catch (err) {
      setError(String(err));
      setMessage("");
    }
  };

  const sourceLabel =
    keySource === "env"
      ? "环境变量 DSF_API_KEY"
      : keySource === "file"
        ? "credentials 文件"
        : "未配置";

  return html`
    <div class="card">
      <h2>端点配置（OpenAI 兼容；更换 base_url / 模型名 / 密钥即切换端点）</h2>
      <label>base_url</label>
      <input
        type="text"
        value=${baseUrl}
        placeholder="如 https://opencode.ai/zen/go/v1"
        onInput=${(e) => setBaseUrl(e.target.value)}
      />
      <label>模型名</label>
      <input
        type="text"
        value=${model}
        placeholder="如 deepseek-v4-flash-vision-exp"
        onInput=${(e) => setModel(e.target.value)}
      />
      <label>
        API 密钥（${keyConfigured ? `已配置（来源：${sourceLabel}）` : "未配置"}）
      </label>
      <input
        type="password"
        value=${apiKey}
        placeholder=${keyConfigured ? "留空 = 沿用已配置密钥" : "请输入密钥"}
        onInput=${(e) => setApiKey(e.target.value)}
      />
      <div class="hint">密钥写入本地 credentials 文件，界面不回显、接口不返回内容。</div>
      ${error && html`<div class="error">${error}</div>`}
      ${message && html`<div class="ok-msg">${message}</div>`}
      <div style="margin-top:10px">
        <button
          class="primary"
          disabled=${!baseUrl.trim() || !model.trim()}
          onClick=${save}
        >
          保存
        </button>
      </div>
    </div>
  `;
}

render(html`<${App} />`, document.getElementById("app"));
