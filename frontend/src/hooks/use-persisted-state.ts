/**
 * localStorage 持久化的 useState：整页刷新 / 应用重启后，恢复上一次的状态。
 *
 * 三期起顶层页面用 Activity 保活（切页不再卸载），本钩子的用武之地是「跨刷新 /
 * 跨重启」的记忆；「不可序列化、必须活过切页」的状态（流式面板、滚动位置）由
 * 保活兜住，两者互补。键名集中在 lib/ui-storage.ts（跨模块契约不散落字面量），
 * 少数页面私有的历史键（dsf-labeling-*）维持原地。
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
