import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

function Input({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      data-slot="input"
      className={cn(
        "flex h-(--h-lg) min-w-0 w-full rounded-md border border-input bg-card px-3 py-1 text-t-md transition-colors",
        "placeholder:text-text-4 hover:border-n-400 focus:border-n-400 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:bg-muted disabled:text-n-400",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
