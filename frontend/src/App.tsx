import type { LucideIcon } from "lucide-react";
import {
  CombineIcon,
  DatabaseIcon,
  FileTextIcon,
  InboxIcon,
  PackageCheckIcon,
  SettingsIcon,
  ShieldCheckIcon,
  UserCheckIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { ThemeToggle } from "./components/theme-toggle";
import { Badge } from "./components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./components/ui/tooltip";
import { cn } from "./lib/utils";
import { ChatTab } from "./ChatTab";
import { ConfigTab } from "./ConfigTab";
import { PromptsTab } from "./PromptsTab";
import { SkillsTab } from "./SkillsTab";

/** 顶级页面：工作区两项 + 流水线六个规划占位（二三期长入，布局不推倒）。 */
type PageKey =
  | "prompts"
  | "settings"
  | "import"
  | "normalize"
  | "qc"
  | "dataset"
  | "review"
  | "export";

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
  enabled: boolean;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: "工作区",
    items: [
      { key: "prompts", label: "提示词", icon: FileTextIcon, enabled: true },
      { key: "settings", label: "设置", icon: SettingsIcon, enabled: true },
    ],
  },
  {
    title: "流水线 · 规划中",
    items: [
      { key: "import", label: "导入 / 素材库", icon: InboxIcon, enabled: false },
      { key: "normalize", label: "格式归一", icon: CombineIcon, enabled: false },
      { key: "qc", label: "机器质检", icon: ShieldCheckIcon, enabled: false },
      { key: "dataset", label: "数据集", icon: DatabaseIcon, enabled: false },
      { key: "review", label: "复核", icon: UserCheckIcon, enabled: false },
      { key: "export", label: "导出", icon: PackageCheckIcon, enabled: false },
    ],
  },
];

const PIPELINE_LABEL: Partial<Record<PageKey, string>> = {
  import: "导入 / 素材库",
  normalize: "格式归一",
  qc: "机器质检",
  dataset: "数据集",
  review: "复核",
  export: "导出",
};

/** 流水线占位页（低保真）：只定「有什么、摆哪里」，不定视觉细节；正式设计随对应期走。 */
function PipelinePlaceholder({ label }: { label: string }): ReactElement {
  return (
    <div className="relative flex h-full flex-col items-center justify-center gap-4 p-8">
      <div className="flex w-full max-w-3xl flex-1 flex-col justify-end rounded-lg border-2 border-dashed border-muted-foreground/30 p-6">
        <div className="space-y-2 text-[12px] text-muted-foreground">
          <p>页面骨架占位：该环节的二、三期功能将以本页为落点设计（列名、按钮位、关键说明）。</p>
          <p>结构标注区——正式设计随对应期的 PRD 与方案走。</p>
        </div>
      </div>
      <Badge variant="muted" className="absolute top-4 right-4">
        低保真占位 · 二期 / 三期实现
      </Badge>
      <p className="text-[13px] text-muted-foreground">{label}——规划中</p>
    </div>
  );
}

/**
 * 应用外壳：可折叠侧栏（展开 250px / 折叠 64px，DF 图标复用折叠钮）+ 满高内容画布。
 *
 * 提示词与设置两页暂嵌旧四页签组件过渡（功能不变），T25 / T26 按新信息架构逐页替换。
 * promptSaved 计数器：提示词保存后通知对话列重拉列表（同页挂载后不再有页签重挂载的顺带刷新）。
 */
export function App(): ReactElement {
  const [page, setPage] = useState<PageKey>("prompts");
  const [collapsed, setCollapsed] = useState(false);
  const [promptSaved, setPromptSaved] = useState(0);

  return (
    <TooltipProvider>
      <div className="flex h-full min-w-128 overflow-hidden">
        <aside
          data-testid="sidebar"
          className={cn(
            "flex h-full flex-col bg-sidebar text-sidebar-foreground transition-[width] duration-150",
            collapsed ? "w-16" : "w-62.5",
          )}
        >
          {/* 品牌 + 折叠钮：折叠态复用 DF 图标（悬停切换为展开箭头含义的 tooltip）。 */}
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            className="mx-4 mt-4 flex items-center gap-2.5 rounded-md p-1 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          >
            <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary font-semibold text-primary-foreground">
              DF
            </span>
            {!collapsed && (
              <span className="truncate text-[15px] font-semibold tracking-tight">
                Dataset Factory
              </span>
            )}
          </button>

          <nav className="mt-4 flex-1 space-y-5 overflow-y-auto px-3 pb-4">
            {NAV_GROUPS.map((group) => (
              <div key={group.title}>
                {!collapsed && (
                  <p className="mb-1 px-3 text-[12px] text-muted-foreground">
                    {group.title}
                  </p>
                )}
                {collapsed && <div className="mx-auto mb-2 h-px w-8 bg-border" />}
                <ul className="space-y-0.5">
                  {group.items.map((item) => {
                    const active = item.enabled && page === item.key;
                    const button = (
                      <button
                        type="button"
                        disabled={!item.enabled}
                        aria-current={active ? "page" : undefined}
                        onClick={() => setPage(item.key)}
                        className={cn(
                          "relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors",
                          "hover:bg-accent disabled:cursor-not-allowed disabled:opacity-45",
                          active && "bg-primary/10 text-primary font-medium",
                        )}
                      >
                        {active && (
                          <span className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-primary" />
                        )}
                        <item.icon className="size-4 shrink-0" />
                        {!collapsed && <span className="truncate">{item.label}</span>}
                      </button>
                    );
                    return (
                      <li key={item.key}>
                        {collapsed || !item.enabled ? (
                          <Tooltip>
                            <TooltipTrigger asChild>{button}</TooltipTrigger>
                            <TooltipContent side="right">
                              {collapsed
                                ? item.label
                                : "规划中——随二、三期长入本底座"}
                            </TooltipContent>
                          </Tooltip>
                        ) : (
                          button
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>

          <div className="flex items-center justify-end px-4 py-3">
            <ThemeToggle />
          </div>
        </aside>

        <main className="h-full min-w-0 flex-1 overflow-y-auto">
          {page === "prompts" && (
            <div className="grid h-full grid-cols-2 gap-3 p-4 max-lg:grid-cols-1">
              <PromptsTab onSaved={() => setPromptSaved((n) => n + 1)} />
              <ChatTab refreshSignal={promptSaved} />
            </div>
          )}
          {page === "settings" && (
            <div className="grid h-full grid-cols-2 gap-3 p-4 max-lg:grid-cols-1">
              <ConfigTab />
              <SkillsTab />
            </div>
          )}
          {PIPELINE_LABEL[page] !== undefined && (
            <PipelinePlaceholder label={PIPELINE_LABEL[page] ?? ""} />
          )}
        </main>
      </div>
    </TooltipProvider>
  );
}
