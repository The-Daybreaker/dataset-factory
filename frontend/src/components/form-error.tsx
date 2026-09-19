import type { ReactElement, ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** 各屏的行距与字号本就不同，样式由调用点原样给出（统一它会动到已验收的像素）。 */
  className?: string;
}

/**
 * 行内错误提示：把「这段文字是一次操作的失败反馈、出现时要即时播报给读屏」这条
 * 可访问性契约收到一处——35 个调用点各写一遍 `role="alert"`，漏一处就静默失效。
 *
 * 组件库的 `Alert` 是带边框与图标的成块提示，形状不同，这里不复用。
 */
export function FormError({ children, className }: Props): ReactElement {
  return (
    <p role="alert" className={className}>
      {children}
    </p>
  );
}
