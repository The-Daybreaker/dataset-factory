import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-16 w-full rounded-md border border-input bg-background px-3 py-2 text-t-md transition-colors",
        // 与 input.tsx 同款（ui-spec §5.1 对 input / textarea / select 是同一条）：
        // 悬停与聚焦都只把描边加深到 --n-400，不出现任何彩色外环——这里不能加
        // focus-visible:ring（--ring 不在主题，ring-2 会退化成 currentcolor 实心环，
        // 2026-09-20 实测过）；字号取 --t-md（基准正文档）。
        "placeholder:text-muted-foreground hover:border-n-400 focus:border-n-400 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
