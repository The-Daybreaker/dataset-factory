import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-t-md font-medium transition-colors focus-visible:outline-none disabled:pointer-events-none disabled:bg-muted disabled:text-n-400 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:bg-primary-hover active:bg-primary-press",
        destructive: "bg-transparent text-bad-ink hover:bg-bad-bg",
        "destructive-fill": "bg-bad-ink text-on-ink hover:bg-bad-ink-strong",
        "destructive-soft": "bg-bad-ink-soft text-on-ink hover:bg-bad-ink-strong",
        outline:
          "border border-input bg-card hover:bg-accent hover:text-accent-foreground",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "text-text-3 hover:bg-accent hover:text-text-1 active:bg-muted",
        link: "text-text-3 underline underline-offset-4 hover:text-text-1",
        accent: "bg-primary/10 text-primary hover:bg-primary/15",
      },
      size: {
        default: "h-(--h-md) px-3",
        xs: "h-(--h-xs) rounded-full px-3 text-t-xs",
        sm: "h-(--h-sm) px-2 text-t-sm",
        lg: "h-(--h-lg) px-4 text-t-lg",
        mini: "h-4 rounded-none bg-transparent px-0 text-t-sm text-text-3 hover:bg-transparent hover:text-text-1 disabled:bg-transparent [&_svg]:size-3.5",
        icon: "size-(--h-md) p-0",
        "icon-lg": "size-(--h-lg) p-0 [&_svg]:size-4.5",
        "icon-xs": "size-(--h-xs) rounded-md p-0 [&_svg]:size-[13px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant,
  size,
  ...props
}: ComponentProps<"button"> & VariantProps<typeof buttonVariants>) {
  return (
    <button
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
