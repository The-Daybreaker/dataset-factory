import type { ReactElement } from "react";
import { useState } from "react";

import { ChatTab } from "./ChatTab";
import { ConfigTab } from "./ConfigTab";
import { PromptsTab } from "./PromptsTab";
import { SkillsTab } from "./SkillsTab";

/** 四个页签：与 CLI 对等，覆盖一期全部能力。 */
type TabKey = "chat" | "prompts" | "skills" | "config";

const TABS: ReadonlyArray<readonly [TabKey, string]> = [
  ["chat", "聊天打标"],
  ["prompts", "提示词库"],
  ["skills", "Skill"],
  ["config", "配置"],
];

/** 应用根组件：页签切换 + 各页签主体。 */
export function App(): ReactElement {
  const [tab, setTab] = useState<TabKey>("chat");

  return (
    <div className="app">
      <header>
        <h1>Dataset Factory 打标测试台</h1>
        <nav className="tabs">
          {TABS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={tab === key ? "active" : ""}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>
      {tab === "chat" && <ChatTab />}
      {tab === "prompts" && <PromptsTab />}
      {tab === "skills" && <SkillsTab />}
      {tab === "config" && <ConfigTab />}
    </div>
  );
}
