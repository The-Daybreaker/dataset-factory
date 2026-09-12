import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import type { PromptInfo } from "./api";
import { api, errorMessage } from "./api";

/**
 * 提示词库：左侧列表、右侧编辑；名称即文件名（改名 = 另存为新条目）。
 *
 * onSaved：可选回调——过渡期与对话同页挂载时，App 借它通知对话列刷新提示词列表
 * （旧四页签形态下靠「切页签重挂载」顺带刷新，掩盖了列表陈旧的问题）。
 */
export function PromptsTab({
  onSaved,
}: {
  onSaved?: () => void;
}): ReactElement {
  const [prompts, setPrompts] = useState<PromptInfo[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const reload = useCallback(async (): Promise<void> => {
    try {
      setPrompts(await api.listPrompts());
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const pick = async (picked: string): Promise<void> => {
    try {
      const full = await api.getPrompt(picked);
      setName(full.name);
      setDescription(full.description);
      setBody(full.body);
      setMessage("");
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const save = async (): Promise<void> => {
    try {
      await api.savePrompt(name, { description, body });
      setMessage(`已保存提示词「${name}」`);
      setError("");
      await reload();
      onSaved?.();
    } catch (err) {
      setError(errorMessage(err));
      setMessage("");
    }
  };

  const remove = async (): Promise<void> => {
    try {
      await api.deletePrompt(name);
      setMessage(`已删除「${name}」`);
      setName("");
      setDescription("");
      setBody("");
      setError("");
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="row">
      <div className="card">
        <h2>提示词库（{prompts.length} 条）</h2>
        {prompts.length === 0 && <div className="empty">（空——右侧新建）</div>}
        <ul className="item-list">
          {prompts.map((prompt) => (
            <li key={prompt.name} className={prompt.name === name ? "selected" : ""}>
              <button
                type="button"
                className="row-button"
                onClick={() => void pick(prompt.name)}
              >
                <span className="name">{prompt.name}</span>
                <span className="desc">
                  {prompt.description === "" ? "（无描述）" : prompt.description}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="card">
        <h2>编辑（保存时按名称存；改名另存为新条目，旧条目保留）</h2>
        <label htmlFor="prompt-name">名称</label>
        <input
          id="prompt-name"
          type="text"
          value={name}
          placeholder="如 h3-video"
          onInput={(event) => setName(event.currentTarget.value)}
        />
        <label htmlFor="prompt-desc">描述（选择器里展示）</label>
        <input
          id="prompt-desc"
          type="text"
          value={description}
          placeholder="这条提示词产出什么、适合什么"
          onInput={(event) => setDescription(event.currentTarget.value)}
        />
        <label htmlFor="prompt-body">正文（提示词本体）</label>
        <textarea
          id="prompt-body"
          className="tall"
          value={body}
          placeholder="你是……"
          onInput={(event) => setBody(event.currentTarget.value)}
        />
        {error !== "" && <div className="error">{error}</div>}
        {message !== "" && <div className="ok-msg">{message}</div>}
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={name.trim() === ""}
            onClick={() => void save()}
          >
            保存
          </button>
          {name !== "" && (
            <button type="button" className="danger" onClick={() => void remove()}>
              删除「{name}」
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
