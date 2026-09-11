import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import type { SkillInfo } from "./api";
import { api, errorMessage } from "./api";

/** Skill 库：从本机目录导入、启用 / 停用、移除（停用不删除）。 */
export function SkillsTab(): ReactElement {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [importPath, setImportPath] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const reload = useCallback(async (): Promise<void> => {
    try {
      setSkills(await api.listSkills());
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const doImport = async (): Promise<void> => {
    try {
      const result = await api.importSkill(importPath);
      const sizeKb = (result.total_bytes / 1024).toFixed(1);
      setMessage(
        `已导入「${result.name}」（${sizeKb} KiB——skill 全文将注入打标请求，体积偏大时留意 token 消耗）`,
      );
      setError("");
      setImportPath("");
      await reload();
    } catch (err) {
      setError(errorMessage(err));
      setMessage("");
    }
  };

  const toggle = async (skill: SkillInfo): Promise<void> => {
    try {
      await api.setSkillEnabled(skill.name, !skill.enabled);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const remove = async (skill: SkillInfo): Promise<void> => {
    if (!window.confirm(`确认删除 skill「${skill.name}」？（整目录移除）`)) {
      return;
    }
    try {
      await api.deleteSkill(skill.name);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <>
      <div className="card">
        <h2>导入 skill 包（agentskills.io 标准，填本机 skill 目录路径）</h2>
        <div className="inline-form">
          <input
            type="text"
            value={importPath}
            placeholder="如 D:/skills/h3-prompt-writing"
            onInput={(event) => setImportPath(event.currentTarget.value)}
          />
          <button
            type="button"
            className="primary"
            disabled={importPath.trim() === ""}
            onClick={() => void doImport()}
          >
            导入
          </button>
        </div>
        {error !== "" && <div className="error">{error}</div>}
        {message !== "" && <div className="ok-msg">{message}</div>}
      </div>
      <div className="card">
        <h2>Skill 库（{skills.length} 个）</h2>
        {skills.length === 0 && <div className="empty">（空——上方导入）</div>}
        <ul className="item-list">
          {skills.map((skill) => (
            <li key={skill.name}>
              <label className="skill-toggle">
                <input
                  type="checkbox"
                  checked={skill.enabled}
                  onChange={() => void toggle(skill)}
                />
              </label>
              <span className="name">{skill.name}</span>
              <span className="desc">
                {skill.description === "" ? "（无描述）" : skill.description}
              </span>
              <button
                type="button"
                className="danger"
                onClick={() => void remove(skill)}
              >
                删除
              </button>
            </li>
          ))}
        </ul>
        <div className="hint">
          勾选 = 启用（打标时注入全文）；取消 = 停用（保留在库中）。
        </div>
      </div>
    </>
  );
}
