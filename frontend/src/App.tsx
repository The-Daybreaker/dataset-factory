import type { LucideIcon } from "lucide-react";
import {
  FileTextIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  SettingsIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";
import { cn } from "./lib/utils";
import { PromptWorkbench } from "./pages/PromptWorkbench";
import { SettingsPage } from "./pages/SettingsPage";

/** 顶级页面：一期两项（提示词工作台 / 设置容器）；后续期页面届时挂主导航长入。 */
type PageKey = "prompts" | "settings";

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: "工作区",
    items: [
      { key: "prompts", label: "提示词", icon: FileTextIcon },
      { key: "settings", label: "设置", icon: SettingsIcon },
    ],
  },
];

/** 产品版本号（侧栏脚注）；与 package.json / 后端 app version 同步维护。 */
const APP_VERSION = "v0.1.0";

/**
 * 应用外壳：可折叠侧栏（展开 250px / 折叠 64px）+ 满高内容画布（原型稿 ui-draft-05）。
 *
 * 侧栏 = 品牌（DF 图标复用折叠钮，悬停切换折叠 / 展开图标）+ 导航分组 + 版本脚注；
 * 主题切换在各页页头区（工作台对话列顶行 / 设置页头），侧栏不放。
 */
export function App(): ReactElement {
  const [page, setPage] = useState<PageKey>("prompts");
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-full min-w-128 overflow-hidden">
      <aside
        data-testid="sidebar"
        className={cn(
          "flex h-full flex-col border-r border-border bg-sidebar text-sidebar-foreground transition-[width] duration-150",
          collapsed ? "w-16" : "w-62.5",
        )}
      >
        {/* 品牌 + 折叠钮：折叠态复用 DF 图标（悬停切换为展开箭头含义的图标）。 */}
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          className="group flex items-center gap-2.5 px-4 pt-4 pb-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-[13px] font-semibold text-primary-foreground">
            <span className="group-hover:hidden">DF</span>
            {collapsed ? (
              <PanelLeftOpenIcon className="hidden size-4 group-hover:block" />
            ) : (
              <PanelLeftCloseIcon className="hidden size-4 group-hover:block" />
            )}
          </span>
          {!collapsed && (
            <span className="min-w-0">
              <span className="block truncate font-semibold">Dataset Factory</span>
              <span className="block truncate text-[12px] text-muted-foreground">
                打标流水线工具
              </span>
            </span>
          )}
        </button>

        <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2.5 py-1">
          {NAV_GROUPS.map((group) => (
            <div key={group.title}>
              {!collapsed && (
                <p className="px-2.5 pt-3.5 pb-1 text-[11.5px] font-semibold text-muted-foreground">
                  {group.title}
                </p>
              )}
              {collapsed && <div className="mx-1 my-2.5 h-px bg-border" />}
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const active = page === item.key;
                  return (
                    <li key={item.key}>
                      <button
                        type="button"
                        aria-current={active ? "page" : undefined}
                        onClick={() => setPage(item.key)}
                        className={cn(
                          "flex h-[34px] w-full items-center gap-2.5 rounded-md px-2.5 transition-colors",
                          "hover:bg-accent hover:text-accent-foreground",
                          active
                            ? "bg-primary/10 font-medium text-primary"
                            : "text-foreground/78",
                        )}
                      >
                        <item.icon className="size-4 shrink-0" />
                        {!collapsed && <span className="truncate">{item.label}</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div
          className={cn(
            "border-t border-border text-[12px] text-muted-foreground",
            collapsed ? "py-3 text-center text-[10px]" : "px-4.5 py-3",
          )}
        >
          {APP_VERSION}
        </div>
      </aside>

      <main className="h-full min-w-0 flex-1 overflow-y-auto">
        {page === "prompts" && (
          <PromptWorkbench onNavigateToSettings={() => setPage("settings")} />
        )}
        {page === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
