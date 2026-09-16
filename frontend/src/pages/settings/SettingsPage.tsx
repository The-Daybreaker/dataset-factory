/**
 * 设置容器（design「设置容器与技能预览」）：左二级导航（连接 / 能力 / 服务）+ 右内容区。
 * 子页切换即导航切换；未来新设置项（生成参数等）加导航项即可，不再新增主导航。
 * 子面板按 feature 目录拆分：endpoints / skills / service 各一个面板文件。
 */
import type { ReactElement } from "react";
import { useState } from "react";
import { ShutdownButton } from "../../components/shutdown-button";
import { ThemeToggle } from "../../components/theme-toggle";
import { TooltipProvider } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";
import { ActiveEndpointChip } from "./ActiveEndpointChip";
import { EndpointConfigPanel } from "./EndpointConfigPanel";
import { ServicePanel } from "./ServicePanel";
import { SkillsPanel } from "./SkillsPanel";

type SettingsSection = "endpoints" | "skills" | "service";

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
