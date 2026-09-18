import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ComponentProps, ReactElement, ReactNode } from "react";

import { cn } from "../../lib/utils";

function TooltipProvider({
  delayDuration = 320,
  ...props
}: ComponentProps<typeof TooltipPrimitive.Provider>) {
  return <TooltipPrimitive.Provider delayDuration={delayDuration} {...props} />;
}

const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

function TooltipContent({
  className,
  sideOffset = 6,
  children,
  ...props
}: ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        className={cn(
          "z-50 w-fit rounded-sm bg-foreground px-2.5 py-1 text-[12px] text-background",
          "data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=delayed-open]:zoom-in-95",
          className,
        )}
        {...props}
      >
        {children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
}

/** 悬停气泡提示（§5.11 全站提示形态）：替代原生 title，避免系统直角灰框。
 * 自带 Provider：页面会被单独渲染（组件测试 / 弹窗复用），不能依赖外壳的 Provider。 */
function Tip({ label, children }: { label: ReactNode; children: ReactElement }) {
  return (
    <TooltipProvider delayDuration={320}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { Tip, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger };
