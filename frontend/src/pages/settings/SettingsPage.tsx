/**
 * 设置容器（design「设置容器与技能预览」）：左二级导航（连接 / 能力 / 服务）+ 右内容区。
 * 子页切换即导航切换；未来新设置项（生成参数等）加导航项即可，不再新增主导航。
 * 子面板按 feature 目录拆分：endpoints / skills / service 各一个面板文件。
 */
import type { ReactElement } from "react";
import { TooltipProvider } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";
import { EndpointConfigPanel } from "./EndpointConfigPanel";
import { ServicePanel } from "./ServicePanel";
import { SkillsPanel } from "./SkillsPanel";

export type SettingsSection = "endpoints" | "skills" | "service";

export function SettingsPage({
  section = "endpoints",
}: {
  section?: SettingsSection;
}): ReactElement {
  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex min-h-0 flex-1 flex-col px-6 pt-8 pb-6">
          {/* 端点 / 技能子页：面板吃满剩余高度、左右两栏各自内部滚动（2026-09-13 用户反馈
              ——此前右侧滚动会带动左侧列表一起滚）；服务子页是内容流，保持整区滚动。 */}
          <section
            className={cn(
              "min-w-0 flex-1",
              section === "service"
                ? "overflow-y-auto"
                : "flex min-h-0 flex-col overflow-hidden",
            )}
            aria-label="设置内容区"
          >
            {section === "endpoints" && (
              <>
                <h2 className="mb-3 shrink-0 text-t-2xl font-semibold">端点配置</h2>
                <div className="min-h-0 flex-1">
                  <EndpointConfigPanel />
                </div>
              </>
            )}
            {section === "skills" && (
              <>
                <h2 className="mb-4 shrink-0 text-t-2xl font-semibold">技能</h2>
                <div className="min-h-0 flex-1">
                  <SkillsPanel />
                </div>
              </>
            )}
            {section === "service" && (
              <>
                <h2 className="text-t-2xl font-semibold">服务运行</h2>
                <ServicePanel />
              </>
            )}
          </section>
        </div>
      </div>
    </TooltipProvider>
  );
}
