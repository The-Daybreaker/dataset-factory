import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

function Input({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      data-slot="input"
      className={cn(
        "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-[13.5px] transition-colors",
        "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
