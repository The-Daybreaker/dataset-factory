import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/**
 * 分隔线：本项目的分区基调是「竖装饰线」，横线一律不用（design.md 分区方式）。
 * vertical = 竖线（默认，用于分栏），horizontal 仅保留给极少数堆叠场景。
 */
function Separator({
  className,
  orientation = "vertical",
  ...props
}: ComponentProps<"div"> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      data-slot="separator"
      role="none"
      className={cn(
        "shrink-0 bg-border",
        orientation === "vertical" ? "h-full w-px" : "h-px w-full",
        className,
      )}
      {...props}
    />
  );
}

export { Separator };
