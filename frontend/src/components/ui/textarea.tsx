import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-16 w-full rounded-md border border-input bg-background px-3 py-2 text-t-md transition-colors",
        // 与 input.tsx 同款（ui-spec §5.1）：悬停与聚焦都只把描边加深到 --n-400，
        // 不出现任何彩色外环。原先的 focus-visible:ring-* 因 --ring 未定义会画出
        // 2px currentcolor 实心环，删（2026-09-20）；13.5px 是刻度外字号，归位 --t-md。
        "placeholder:text-muted-foreground hover:border-n-400 focus:border-n-400 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
