import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

const badgeVariants = cva(
  // 章的固定形态（ui-spec §5.15「章 / 标记」行）：胶囊形、18px 一档、11px 字（--t-xs）、medium。
  // 18px 实现侧暂无高度令牌（--h-xs 是 22px 的行内动作档），按规范字面书写并在此登记出处。
  "inline-flex h-[18px] w-fit items-center justify-center gap-1 whitespace-nowrap rounded-full px-2 text-[11px] font-medium [&>svg]:size-3",
  {
    variants: {
      variant: {
        // 状态章一律「实底彩色 + 白字」（§5.2，v4.1 定案）：字色走 --on-ink，
        // 暗色下随令牌自动翻成深字；浅底形态不再做章。
        default: "bg-primary text-primary-foreground",
        success: "bg-ok-ink text-on-ink",
        info: "bg-info-ink text-on-ink",
        destructive: "bg-bad-ink text-on-ink",
        // 「已停用」这类不需要强调的状态：不开新的章底色（§5.2 尾句），
        // 用灰字 + 字重表达——落在 2026-09-20 的取色拍板上（候选 α）。
        muted: "text-n-500",
        outline: "text-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Badge };
