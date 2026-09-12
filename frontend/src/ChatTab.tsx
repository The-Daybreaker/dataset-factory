import type { ChangeEvent, ReactElement } from "react";
import { useEffect, useState } from "react";
import type { HistoryMessageView, PromptInfo, SkillInfo } from "./api";
import { ApiError, api, errorMessage } from "./api";

/** 待发送的图片：原始文件名 + data URL（后端接受 data URL 或纯 base64）。 */
interface PendingImage {
  name: string;
  dataUrl: string;
}

/**
 * 界面里的消息 = 后端历史消息 + 一个渲染用的稳定 id。
 *
 * 为什么要自己编号：后端返回的历史里没有消息 id；而对话是「只追加」的，
 * 用序号当 key 既稳定又唯一（不必拿数组下标硬撑，React 的 diff 也更准）。
 */
interface ChatMessage extends HistoryMessageView {
  id: number;
}

/** 聊天打标：发图 + 指令产出 caption；同一会话内多轮即迭代改写。 */
export function ChatTab({ refreshSignal = 0 }: { refreshSignal?: number }): ReactElement {
  const [prompts, setPrompts] = useState<PromptInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [promptName, setPromptName] = useState("");
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [instruction, setInstruction] = useState("");
  const [image, setImage] = useState<PendingImage | null>(null);
  const [sending, setSending] = useState(false);
  // 等待中的秒数计时：让「卡住多久、卡在等待模型」可见，而不是按钮一黑到底。
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [error, setError] = useState("");

  // 进页面先拉提示词与 skill 列表（会话设置里要选它们）。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [promptList, skillList] = await Promise.all([
          api.listPrompts(),
          api.listSkills(),
        ]);
        if (!cancelled) {
          setPrompts(promptList);
          setSkills(skillList);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshSignal]);

  // 恢复最近一次会话：重启程序后历史仍在。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const snapshot = await api.latestSession();
        if (cancelled) {
          return;
        }
        setSessionId(snapshot.session_id);
        setPromptName(snapshot.settings.prompt_name ?? "");
        setSkillNames(snapshot.settings.skill_names);
        setMessages(snapshot.messages.map((item, index) => ({ ...item, id: index })));
      } catch (err) {
        // 「还没有任何会话」（404）是首次使用的正常情况，不当错误展示；按状态码判断
        // 而非匹配错误文案——后端改措辞不应静默改变界面行为。
        const noSessionYet = err instanceof ApiError && err.status === 404;
        if (!cancelled && !noSessionYet) {
          setError(errorMessage(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleSkill = (name: string): void => {
    setSkillNames((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    );
  };

  // 发送期间每秒累加等待时间；结束（成功 / 失败）时归零。清理由 sending 的变化触发。
  useEffect(() => {
    if (!sending) {
      return;
    }
    setWaitSeconds(0);
    const timer = setInterval(() => {
      setWaitSeconds((current) => current + 1);
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [sending]);

  const pickImage = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file === undefined) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setImage({ name: file.name, dataUrl: String(reader.result) });
    };
    reader.readAsDataURL(file);
  };

  const send = async (): Promise<void> => {
    if (instruction.trim() === "" && image === null) {
      return;
    }
    setSending(true);
    setError("");
    try {
      const result = await api.label({
        session_id: sessionId,
        prompt_name: promptName === "" ? null : promptName,
        skill_names: skillNames,
        instruction,
        image_base64: image?.dataUrl ?? null,
        image_name: image?.name ?? "image.png",
      });
      setMessages((current) => [
        ...current,
        {
          id: current.length,
          role: "user",
          text: instruction,
          attachment: image?.name ?? null,
        },
        {
          id: current.length + 1,
          role: "assistant",
          text: result.caption,
          attachment: null,
        },
      ]);
      setSessionId(result.session_id);
      setInstruction("");
      setImage(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const newSession = (): void => {
    setSessionId(null);
    setMessages([]);
    setError("");
  };

  return (
    <div>
      <div className="card">
        <h2>会话设置（每个会话定一个基础提示词，可中途切换）</h2>
        <div className="row">
          <div>
            <label htmlFor="chat-prompt">基础提示词（必选）</label>
            <select
              id="chat-prompt"
              value={promptName}
              onChange={(event) => setPromptName(event.target.value)}
            >
              <option value="">（请选择）</option>
              {prompts.map((prompt) => (
                <option key={prompt.name} value={prompt.name}>
                  {prompt.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="chat-skill">启用的 Skill（可选叠加，勾选即注入全文）</label>
            <div id="chat-skill">
              {skills.length === 0 && (
                <div className="empty">（Skill 库为空——到 Skill 页导入）</div>
              )}
              {skills.map((skill) => (
                <label key={skill.name} className="skill-check">
                  <input
                    type="checkbox"
                    checked={skillNames.includes(skill.name) && skill.enabled}
                    disabled={!skill.enabled}
                    onChange={() => toggleSkill(skill.name)}
                  />
                  {skill.name}
                  {skill.enabled ? "" : "（已停用）"}
                </label>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>对话{sessionId === null ? "（新会话）" : `（会话 ${sessionId}）`}</h2>
        <div className="chat-log">
          {messages.length === 0 && (
            <div className="empty">（还没有消息——选好提示词，发图 + 指令开始打标）</div>
          )}
          {messages.map((message) => (
            <div key={message.id} className={`msg ${message.role}`}>
              {message.text}
              {message.attachment !== null && (
                <span className="attachment">[@{message.attachment}]</span>
              )}
            </div>
          ))}
        </div>
        {error !== "" && <div className="error">{error}</div>}
        <div className="chat-input" style={{ marginTop: "12px" }}>
          <textarea
            placeholder="打标指令（如：给这张图打个标 / 改成两句话…）"
            value={instruction}
            onInput={(event) => setInstruction(event.currentTarget.value)}
          />
          <div className="chat-actions">
            <input type="file" accept="image/*" onChange={pickImage} />
            {image !== null && (
              <img className="preview" src={image.dataUrl} alt="待打标图片" />
            )}
            <div className="chat-buttons">
              <button type="button" className="ghost" onClick={newSession}>
                新会话
              </button>
              <button
                type="button"
                className="primary"
                disabled={sending || (instruction.trim() === "" && image === null)}
                onClick={() => void send()}
              >
                {sending
                  ? waitSeconds < 3
                    ? "已发送，等待模型…"
                    : `等待模型响应…（已 ${waitSeconds}s）`
                  : "发送"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
