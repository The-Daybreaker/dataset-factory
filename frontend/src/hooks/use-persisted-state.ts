/**
 * localStorage 持久化的 useState：组件被条件渲染卸载重挂（打标页是 lazy + 条件渲染，
 * 切页即整页重挂）或整页刷新后，恢复上一次的状态。
 *
 * 为什么不用会话域方案（状态上提 App 层，chat-session.tsx 那样）：能序列化的纯数据
 * （选中批次、分组折叠）用 localStorage 更省——刷新页面也能恢复，且不用把打标页的
 * 内部状态提升到 App 层去加重全局。会话流式内容那类「不可序列化、必须活过卸载」的
 * 状态才值得上提。
 *
 * 读写都兜底：隐私模式 / 配额满时 localStorage 会抛错，记忆失败可接受，功能不受影响。
 */
import { useEffect, useState } from "react";

/** 恢复值的类型守卫：localStorage 里的 JSON 不可信，畸形值一律回退 initial。 */
type Validator<T> = (value: unknown) => value is T;

/**
 * initial 为 Set 时按数组恢复（JSON 没有集合类型；元素限定 string，脏项丢弃）。
 * 其余类型按 JSON.parse 原样恢复——调用方要更强的形状校验时传 validate。
 */
function revive<T>(parsed: unknown, initial: T): T {
  if (initial instanceof Set) {
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((entry): entry is string => typeof entry === "string")
        : [],
    ) as unknown as T;
  }
  return parsed as T;
}

export function usePersistedState<T>(
  key: string,
  initial: T,
  validate?: Validator<T>,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return initial;
      const parsed: unknown = JSON.parse(raw);
      if (validate && !validate(parsed)) return initial;
      return revive(parsed, initial);
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      // Set 要转数组存（JSON.stringify(new Set()) 产出 "{}"，恢复时永远拿到空集）。
      const serialized =
        state instanceof Set ? JSON.stringify([...state]) : JSON.stringify(state);
      localStorage.setItem(key, serialized);
    } catch {
      // 存不进去就算了（隐私模式 / 配额满）：下次进来退回 initial，不算故障。
    }
  }, [key, state]);

  return [state, setState];
}
