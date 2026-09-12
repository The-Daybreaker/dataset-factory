import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import type { ReactElement } from "react";

import { type ThemeMode, useTheme } from "../hooks/use-theme";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

/** 三态循环：跟随系统 → 亮色 → 暗色 → 跟随系统。 */
const CYCLE: readonly ThemeMode[] = ["system", "light", "dark"];

const MODE_LABEL: Record<ThemeMode, string> = {
  system: "跟随系统",
  light: "亮色",
  dark: "暗色",
};

function modeIcon(mode: ThemeMode) {
  return mode === "system" ? MonitorIcon : mode === "light" ? SunIcon : MoonIcon;
}

/** 主题切换按钮：点击在三态间循环，选择记进 localStorage（跟随系统时实时响应系统切换）。 */
export function ThemeToggle(): ReactElement {
  const { mode, setMode } = useTheme();
  // noUncheckedIndexedAccess 下数组下标访问带 undefined，兜底回 system（三态循环不会真走到）。
  const next =
    (CYCLE[(CYCLE.indexOf(mode) + 1) % CYCLE.length] satisfies ThemeMode | undefined) ??
    "system";
  const Icon = modeIcon(mode);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`主题：${MODE_LABEL[mode]}，切换为${MODE_LABEL[next]}`}
          onClick={() => setMode(next)}
        >
          <Icon />
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        主题：{MODE_LABEL[mode]}（切换为{MODE_LABEL[next]}）
      </TooltipContent>
    </Tooltip>
  );
}
