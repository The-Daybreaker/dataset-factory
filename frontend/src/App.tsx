import type { LucideIcon } from "lucide-react";
import {
  ActivityIcon,
  ArrowLeftIcon,
  FileTextIcon,
  FolderInputIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  SettingsIcon,
  TagsIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { ShutdownButton } from "./components/shutdown-button";
import { ThemeToggle } from "./components/theme-toggle";
import { Dialog, DialogContent, DialogTitle } from "./components/ui/dialog";
import { ToastViewport } from "./components/ui/toast";
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

/**
 * 常驻探测间隔。
 *
 * 为什么要常驻：服务被脚本直接杀掉时页面收不到任何事件，只有主动探测才会把点转红。
 * 间隔取 30 秒——够快（配合「任一请求连不上就立刻重查」，用户实际操作时当场就红），也够慢
 * （视觉与请求基线每屏取数不到 30 秒，不会往基线里掺进不定数的探测请求）。
 */
const SERVICE_PROBE_MS = 30_000;
/** UI 上的关闭是优雅停机（等手头请求做完才退），确认期每秒探一次、探到不通就定在红点。 */
const STOPPING_PROBE_MS = 1_000;
const STOPPING_TRIES = 60;

/**
 * 侧栏脚注的服务状态点（§5.14「状态点 + 版本号」组合口径）。
 *
 * 数据 = GET /api/service，不为点编造状态（§5.3）。探测时机四处：进页、窗口聚焦、
 * `df:service-changed`（任何一次请求连不上后端时由 `reportError` 广播）、常驻低频轮询。
 * 「正在停止」是点上的第四个状态：UI 关闭被受理但服务还在排空请求，这段时间既不是运行中
 * 也不是不可用，如实标出来（与「关闭前会等正在进行的请求跑完」的弹窗文案同一个事实）。
 */
function ServiceDot(): ReactElement {
  const [state, setState] = useState<"probing" | "ok" | "stopping" | "bad">("probing");
  const refresh = useCallback(() => {
    void api.getService().then(
      () => setState("ok"),
      () => setState("bad"),
    );
  }, []);
  useEffect(() => {
    refresh();
    const onWake = (): void => refresh();
    const onStopping = (): void => setState("stopping");
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, SERVICE_PROBE_MS);
    window.addEventListener("focus", onWake);
    window.addEventListener("df:service-changed", onWake);
    window.addEventListener("df:service-stopping", onStopping);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onWake);
      window.removeEventListener("df:service-changed", onWake);
      window.removeEventListener("df:service-stopping", onStopping);
    };
  }, [refresh]);
  const stopping = state === "stopping";
  useEffect(() => {
    if (!stopping) {
      return;
    }
    let tries = 0;
    const timer = window.setInterval(() => {
      tries += 1;
      void api.getService().then(
        () => {
          // 排空得比确认期慢（例如正在跑一轮长生成）：不谎报，退回常驻节奏继续探测。
          if (tries >= STOPPING_TRIES) setState("ok");
        },
        () => setState("bad"),
      );
    }, STOPPING_PROBE_MS);
    return () => window.clearInterval(timer);
  }, [stopping]);
  const label =
    state === "ok"
      ? "服务运行中"
      : state === "bad"
        ? "服务不可用"
        : state === "stopping"
          ? "正在停止服务"
          : "正在检测服务";
  const cls =
    state === "ok"
      ? "bg-ok-dot"
      : state === "bad"
        ? "bg-bad-dot"
        : "bg-info-dot animate-pulse";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          role="img"
          aria-label={label}
          className={`inline-block size-1.5 rounded-full ${cls}`}
        />
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

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
        {!collapsed && (
          <span className="ml-auto flex items-center gap-1.5">
            <ServiceDot />
            <span className="tabular-nums">{APP_VERSION}</span>
          </span>
        )}
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
            {page === "labeling" && (
              <LabelingPage
                onNavigateToSettings={openSettings}
                onOpenWorkbench={() => setPage("prompts")}
              />
            )}
          </Suspense>
        </main>
      </div>
      {/* 浮层提示挂在外壳唯一一处：连接类失败在各页面投递，渲染出口只留一个。 */}
      <ToastViewport />
    </TooltipProvider>
  );
}
