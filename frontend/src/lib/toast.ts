/**
 * 浮层提示（toast）的状态源。
 *
 * 做成模块级存储而不是 Context：投递点散落在各个 catch 分支（连不上后端的错误到处都可能
 * 发生），用 Context 就得给每处接一个 hook、给每个测试外壳补一层 Provider；而它本身只是
 * 一个「短寿命的字符串清单」，没有配置项需要向下传递。渲染端见 `components/ui/toast.tsx`。
 */

export interface ToastItem {
  id: number;
  text: string;
}

/** 驻留时长（§5.11：2～4 秒自动消失）。 */
const LIFETIME_MS = 3500;

let items: readonly ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const timers = new Map<number, number>();

function emit(): void {
  for (const listener of listeners) listener();
}

function schedule(id: number): void {
  const existing = timers.get(id);
  if (existing !== undefined) window.clearTimeout(existing);
  timers.set(
    id,
    window.setTimeout(() => {
      timers.delete(id);
      items = items.filter((item) => item.id !== id);
      emit();
    }, LIFETIME_MS),
  );
}

function dismiss(id: number): void {
  const timer = timers.get(id);
  if (timer !== undefined) window.clearTimeout(timer);
  timers.delete(id);
  items = items.filter((item) => item.id !== id);
  emit();
}

/**
 * 投递一条提示；同样的文案已在屏上时只把它的时间续上，不再叠第二条。
 *
 * 去重是必要的：服务关闭后一次页面重挂载会并发失败好几个请求，不去重就会一次弹出好几条
 * 一模一样的提示，跟用户原来抱怨的「两处报错框」是同一个毛病，只是换了形态。
 */
export function toast(text: string): void {
  const shown = items.find((item) => item.text === text);
  if (shown !== undefined) {
    schedule(shown.id);
    return;
  }
  const id = nextId;
  nextId += 1;
  items = [...items, { id, text }];
  schedule(id);
  emit();
}

export function subscribeToast(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** `useSyncExternalStore` 的快照：数组只在增删时换引用，平时保持稳定。 */
export function getToastSnapshot(): readonly ToastItem[] {
  return items;
}

export { dismiss as dismissToast };
