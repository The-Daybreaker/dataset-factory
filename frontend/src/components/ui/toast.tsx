import type { ReactElement } from "react";
import { useSyncExternalStore } from "react";
import { dismissToast, getToastSnapshot, subscribeToast } from "../../lib/toast";
import { cn } from "../../lib/utils";

/**
 * 浮层提示的渲染端（§5.11 四类浮层里的 Toast）。
 *
 * 形态照 `ui-system.css` 的 `.toast`：黑底白字（全站唯一一处黑底浮层，暗色随 n-900 / n-0
 * 自动翻成浅底深字）、居中贴底、`--r-md` 圆角、几秒后自己消失。与原型不同的只有一处：
 * 原型写死 `white-space: nowrap` 配 70vw 省略号，而这里的正文是「无法连接后端——请确认
 * dsf serve 已启动」这类长句，截断等于没说，所以改成允许换行、并把宽度收到 420px。
 */
export function ToastViewport({ className }: { className?: string }): ReactElement {
  const items = useSyncExternalStore(subscribeToast, getToastSnapshot);
  return (
    <div
      role="status"
      className={cn(
        "pointer-events-none fixed inset-x-0 bottom-6 z-(--z-toast) flex flex-col items-center gap-2 px-4",
        className,
      )}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => dismissToast(item.id)}
          className="animate-in fade-in-0 max-w-[min(70vw,420px)] rounded-md bg-n-900 px-4 py-2 text-center text-t-md leading-(--lh-base) text-n-0 opacity-95 wrap-anywhere"
        >
          {item.text}
        </button>
      ))}
    </div>
  );
}
