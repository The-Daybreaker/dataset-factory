import type { LucideIcon } from "lucide-react";
import {
  ActivityIcon,
  ArrowLeftIcon,
  DatabaseIcon,
  EyeIcon,
  FileTextIcon,
  FolderInputIcon,
  FolderOutputIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  ScanLineIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  TagsIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { lazy, Suspense, useState } from "react";
import { ShutdownButton } from "./components/shutdown-button";
import { ThemeToggle } from "./components/theme-toggle";
import { Dialog, DialogContent, DialogTitle } from "./components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./components/ui/tooltip";
import { useTheme } from "./hooks/use-theme";
import { cn } from "./lib/utils";
import logo from "./logo-speed-d.png";
import { PromptWorkbench } from "./pages/prompt-workbench/PromptWorkbench";
import type { SettingsSection } from "./pages/settings/SettingsPage";

/**
 * 打标页与设置容器切成分包、进页面时才载（G4「减少无谓开销」）。
 *
 * 为什么这样切：应用永远从工作台起画（`useState<PageKey>("prompts")`），工作台是首屏，
 * 保持静态导入不动；另两页的代码（含它们独用的 radix 弹层与大量业务组件）在首屏那一帧
 * 是纯浪费的下载与解析。加载在本地服务下是一帧内的事。
 */
const LabelingPage = lazy(() =>
  import("./pages/labeling/LabelingPage").then((m) => ({ default: m.LabelingPage })),
);
const SettingsPage = lazy(() =>
  import("./pages/settings/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

/** 顶级页面：一期两项（提示词工作台 / 设置容器）；后续期页面届时挂主导航长入。 */
type PageKey = "prompts" | "settings" | "labeling";

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

const PLANNED_ITEMS = [
  { label: "导入 / 素材库", icon: FolderInputIcon },
  { label: "格式归一", icon: SlidersHorizontalIcon },
  { label: "机器质检", icon: ScanLineIcon },
  { label: "数据集", icon: DatabaseIcon },
  { label: "复核", icon: EyeIcon },
  { label: "导出", icon: FolderOutputIcon },
];

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: "工作区",
    items: [{ key: "prompts", label: "策略", icon: FileTextIcon }],
  },
  { title: "流水线", items: [{ key: "labeling", label: "打标", icon: TagsIcon }] },
];

/** 产品版本号（侧栏脚注）；与 package.json / 后端 app version 同步维护。 */
const APP_VERSION = "v0.1.0";

/** 可折叠侧栏、全局操作与独立滚动的工作画布。 */
export function App(): ReactElement {
  const { mode, setMode } = useTheme();
  const [page, setPage] = useState<PageKey>("prompts");
  const [collapsed, setCollapsed] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("endpoints");
  const [workPage, setWorkPage] = useState<PageKey>("prompts");

  function openSettings() {
    if (page !== "settings") setWorkPage(page);
    setPage("settings");
    setNavigationOpen(false);
  }

  const navigation = (
    <aside
      data-testid="sidebar"
      className={cn(
        "flex h-full shrink-0 flex-col bg-n-75 text-text-1 transition-[width] duration-150",
        collapsed && !navigationOpen ? "w-16" : "w-62.5",
      )}
    >
      {page !== "settings" && (
        <div className="flex items-center gap-3 px-4 pt-4 pb-3">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() =>
                  navigationOpen
                    ? setNavigationOpen(false)
                    : setCollapsed((value) => !value)
                }
                className="group flex size-8 shrink-0 items-center justify-center rounded-md bg-black hover:bg-nav-hover"
                aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              >
                <img
                  src={logo}
                  alt=""
                  className="size-[27px] object-contain group-hover:hidden"
                />
                {collapsed ? (
                  <PanelLeftOpenIcon className="hidden size-4 group-hover:block" />
                ) : (
                  <PanelLeftCloseIcon className="hidden size-4 group-hover:block" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent>{collapsed ? "展开侧栏" : "收起侧栏"}</TooltipContent>
          </Tooltip>
          {!collapsed && (
            <span className="min-w-0">
              <span className="block truncate font-medium">Dataset Factory</span>
              <span className="block truncate text-t-sm text-text-3">
                打标流水线工具
              </span>
            </span>
          )}
        </div>
      )}

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-1">
        {page === "settings" ? (
          <>
            <button
              type="button"
              aria-label="返回工作区"
              onClick={() => {
                setPage(workPage);
                setNavigationOpen(false);
              }}
              className="mt-2 flex h-(--h-md) w-full items-center gap-3 rounded-md px-3 text-text-3 hover:bg-nav-hover"
            >
              <ArrowLeftIcon className="size-4 shrink-0" />
              {!collapsed && "返回工作区"}
            </button>
            {!collapsed && (
              <p className="px-2 pt-4 pb-1 text-t-sm font-medium text-text-3">设置</p>
            )}
            {(
              [
                { key: "endpoints", label: "端点配置", icon: SettingsIcon },
                { key: "skills", label: "技能", icon: FolderInputIcon },
                { key: "service", label: "服务运行", icon: ActivityIcon },
              ] as const
            ).map((item) => (
              <button
                key={item.key}
                type="button"
                aria-label={item.label}
                aria-current={settingsSection === item.key ? "page" : undefined}
                onClick={() => {
                  setSettingsSection(item.key);
                  setNavigationOpen(false);
                }}
                className={cn(
                  "flex h-(--h-md) w-full items-center gap-3 rounded-md px-3 text-text-3 hover:bg-nav-hover",
                  collapsed && "justify-center px-0",
                  settingsSection === item.key &&
                    "bg-nav-active font-medium text-text-1",
                )}
              >
                <item.icon className="size-4 shrink-0" />
                {!collapsed && item.label}
              </button>
            ))}
          </>
        ) : (
          NAV_GROUPS.map((group) => (
            <div key={group.title}>
              {!collapsed && (
                <p className="px-2 pt-4 pb-1 text-t-sm font-medium text-text-3">
                  {group.title}
                </p>
              )}
              {collapsed && <div className="mx-1 my-3 h-px bg-border" />}
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const active = page === item.key;
                  return (
                    <li key={item.key}>
                      <button
                        type="button"
                        aria-label={item.label}
                        aria-current={active ? "page" : undefined}
                        onClick={() => {
                          setPage(item.key);
                          setNavigationOpen(false);
                        }}
                        className={cn(
                          "flex h-(--h-md) w-full items-center gap-3 rounded-md px-3 transition-colors",
                          collapsed && "justify-center px-0",
                          active
                            ? "bg-nav-active font-medium text-text-1"
                            : "text-text-3 hover:bg-nav-hover hover:text-text-1",
                        )}
                      >
                        <item.icon className="size-4 shrink-0" />
                        {!collapsed && <span className="truncate">{item.label}</span>}
                      </button>
                    </li>
                  );
                })}
                {group.title === "流水线" &&
                  PLANNED_ITEMS.map((item) => (
                    <li key={item.label}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="block">
                            <button
                              type="button"
                              disabled
                              aria-label={item.label}
                              className={cn(
                                "flex h-(--h-md) w-full cursor-not-allowed items-center gap-3 rounded-md px-3 text-n-400",
                                collapsed && "justify-center px-0",
                              )}
                            >
                              <item.icon className="size-4 shrink-0" />
                              {!collapsed && item.label}
                            </button>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{item.label} · 规划中</TooltipContent>
                      </Tooltip>
                    </li>
                  ))}
              </ul>
            </div>
          ))
        )}
      </nav>

      <div
        className={cn(
          "flex items-center gap-2 border-t border-border p-3 text-t-xs text-text-4",
          collapsed ? "flex-col" : "px-4",
        )}
      >
        <ShutdownButton sidebar />
        <ThemeToggle sidebar mode={mode} onModeChange={setMode} />
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="设置"
              aria-current={page === "settings" ? "page" : undefined}
              onClick={openSettings}
              className="flex size-(--h-md) shrink-0 items-center justify-center rounded-md text-text-3 hover:bg-nav-hover hover:text-text-1"
            >
              <SettingsIcon className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent>设置</TooltipContent>
        </Tooltip>
        {!collapsed && <span className="ml-auto tabular-nums">{APP_VERSION}</span>}
      </div>
    </aside>
  );

  return (
    <TooltipProvider>
      <div className="flex h-full min-w-0 flex-col overflow-hidden md:flex-row">
        <div className="hidden h-full shrink-0 md:block">{navigation}</div>
        <div className="flex shrink-0 items-center gap-3 bg-n-75 px-4 py-2 md:hidden">
          <button
            type="button"
            aria-label="打开导航"
            onClick={() => {
              setCollapsed(false);
              setNavigationOpen(true);
            }}
            className="flex size-(--h-md) shrink-0 items-center justify-center rounded-md text-text-3 hover:bg-nav-hover"
          >
            <PanelLeftOpenIcon className="size-4" />
          </button>
          <span className="truncate font-medium">Dataset Factory</span>
        </div>
        <Dialog open={navigationOpen} onOpenChange={setNavigationOpen}>
          <DialogContent
            aria-describedby={undefined}
            className="left-0 top-0 h-dvh w-62.5 max-w-full translate-x-0 translate-y-0 gap-0 rounded-none border-0 p-0"
          >
            <DialogTitle className="sr-only">导航</DialogTitle>
            {navigation}
          </DialogContent>
        </Dialog>
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {/* fallback 给 null 而非骨架屏：占位元素本身就是新像素，切换路由时宁可空一帧。 */}
          <Suspense fallback={null}>
            {page === "prompts" && (
              <PromptWorkbench onNavigateToSettings={openSettings} />
            )}
            {page === "settings" && <SettingsPage section={settingsSection} />}
            {page === "labeling" && <LabelingPage />}
          </Suspense>
        </main>
      </div>
    </TooltipProvider>
  );
}
