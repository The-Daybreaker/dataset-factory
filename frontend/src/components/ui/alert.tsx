import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/**
 * 页内反馈条（design「反馈条」）：操作结果直接呈现在页面里。
 * 成功 = success 10% 底 + 深绿文字；错误 = destructive 底；信息 = muted。
 */
const alertVariants = cva(
  "relative flex w-full items-start gap-2 rounded-md border px-3 py-2 text-[13px] [&>svg]:size-4 [&>svg]:mt-0.5",
  {
    variants: {
      variant: {
        default: "border-border bg-card text-foreground",
        success: "border-success/25 bg-success/10 text-success",
        destructive: "border-destructive/25 bg-destructive/10 text-destructive",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Alert({
  className,
  variant,
  ...props
}: ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return (
    <div
      data-slot="alert"
      role="alert"
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}

function AlertTitle({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-title"
      className={cn("font-medium leading-snug", className)}
      {...props}
    />
  );
}

function AlertDescription({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn("text-[13px] leading-relaxed opacity-90", className)}
      {...props}
    />
  );
}

export { Alert, AlertDescription, AlertTitle, alertVariants };
