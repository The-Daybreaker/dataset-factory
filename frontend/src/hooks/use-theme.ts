/**
 * 主题（亮 / 暗）状态管理：默认跟随系统，手动切换后记住选择（design「主题行为」）。
 *
 * 实现走 class 策略：把 resolved 亮暗写到 <html class="dark">，Tailwind 的 dark: 变体
 * 全部跟着它走。index.html 里有一段内联脚本在首帧前做同样的判定，避免刷新时闪一下
 * 错误主题（FOUC）。
 */
import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "dsf-theme";

function readStoredMode(): ThemeMode {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
}

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolveMode(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") {
    return systemPrefersDark() ? "dark" : "light";
  }
  return mode;
}

function applyMode(mode: ThemeMode): void {
  document.documentElement.classList.toggle("dark", resolveMode(mode) === "dark");
}

export function useTheme(): {
  mode: ThemeMode;
  resolved: "light" | "dark";
  setMode: (mode: ThemeMode) => void;
} {
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode);
  const [resolved, setResolved] = useState<"light" | "dark">(() => resolveMode(mode));

  useEffect(() => {
    applyMode(mode);
    setResolved(resolveMode(mode));
    if (mode !== "system") {
      return;
    }
    // 跟随系统时，系统切换亮暗要实时生效。
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (): void => {
      applyMode("system");
      setResolved(resolveMode("system"));
    };
    media.addEventListener("change", onChange);
    return () => {
      media.removeEventListener("change", onChange);
    };
  }, [mode]);

  const setMode = useCallback((next: ThemeMode): void => {
    localStorage.setItem(STORAGE_KEY, next);
    setModeState(next);
  }, []);

  return { mode, resolved, setMode };
}
